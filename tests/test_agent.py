"""The assistant loop: what it may do, and what it must never do.

No Ollama and no network here. The loop takes the tool caller as an argument
precisely so it can be tested with a fake one, which also means these run on a
CI box with none of the pipeline's dependencies.
"""

from server import agent


def _tools():
    return [
        {"name": "list_videos", "description": "List them", "inputSchema": {"type": "object"},
         "handler": lambda a: "a video"},
        {"name": "publish_plan", "description": "Plan it", "inputSchema": {"type": "object"},
         "handler": lambda a: "a plan"},
        {"name": "publish_plan_execute", "description": "Upload", "inputSchema": {"type": "object"},
         "handler": lambda a: "uploaded"},
        {"name": "uploadpost_status", "description": "Ready?", "inputSchema": {"type": "object"},
         "handler": lambda a: "connected"},
        {"name": "uploadpost_publish", "description": "Post everywhere",
         "inputSchema": {"type": "object"}, "handler": lambda a: "posted"},
    ]


class _Ollama:
    """A fake Ollama that replies with whatever script it is given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def post(self, url, json=None, timeout=None):
        self.seen.append(json)
        reply = self.replies.pop(0)

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"message": reply}

        return Response()


def _run(monkeypatch, replies, message="do it"):
    fake = _Ollama(replies)
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    calls = []

    def call_tool(name, args):
        calls.append(name)
        return f"result of {name}", ({"items": [{"clip_id": 1}]} if name == "publish_plan" else None)

    out = agent.run(message, [], _tools(), call_tool, "http://x", "gemma4:e4b")
    return out, calls, fake


def test_posting_is_not_offered_to_the_model():
    # The plan Colin approved on 2026-11-25: posting runs only from the plan
    # card, after the person's click. The model plans; it never posts.
    names = [spec["function"]["name"] for spec in agent.tool_specs(_tools())]
    assert "publish_plan_execute" not in names
    assert "uploadpost_publish" not in names
    # Planning and reading stay offered.
    assert "uploadpost_status" in names
    assert "publish_plan" in names and "list_videos" in names


def test_the_posting_tools_are_the_human_only_ones():
    assert agent.HUMAN_ONLY == {"publish_plan_execute", "schedule_clips_execute", "uploadpost_publish"}


def test_a_plain_answer_ends_the_loop(monkeypatch):
    out, calls, _ = _run(monkeypatch, [{"role": "assistant", "content": "Here you go"}])
    assert out["reply"] == "Here you go"
    assert calls == []


def test_a_tool_call_runs_and_the_answer_follows(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant", "tool_calls": [{"function": {"name": "list_videos", "arguments": {}}}]},
        {"role": "assistant", "content": "There is one video"},
    ])
    assert calls == ["list_videos"]
    assert out["reply"] == "There is one video"
    assert out["steps"][0]["tool"] == "list_videos"


def test_a_plan_is_handed_back_for_a_human_to_confirm(monkeypatch):
    out, _, _ = _run(monkeypatch, [
        {"role": "assistant", "tool_calls": [{"function": {"name": "publish_plan", "arguments": {}}}]},
        {"role": "assistant", "content": "Here is the plan"},
    ])
    assert out["plan"] == {"items": [{"clip_id": 1}]}


def test_executing_a_publish_needs_the_click(monkeypatch):
    out, calls, fake = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "publish_plan_execute", "arguments": {}}}]},
        {"role": "assistant", "content": "Press Upload these"},
    ])
    assert calls == [], "the handler must never run from the box"
    assert out["steps"][0]["result"].startswith("Not done: posting needs the person's click")
    # The model is told, in plain words, what to do instead.
    tool_message = fake.seen[1]["messages"][-2]  # the same list; the reply came after
    assert tool_message["role"] == "tool" and "plan card" in tool_message["content"]


def test_multi_platform_publishing_needs_the_click(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "uploadpost_publish",
                                      "arguments": {"clip_id": 1, "platforms": ["youtube"]}}}]},
        {"role": "assistant", "content": "Press the button"},
    ])
    assert calls == []
    assert out["steps"][0]["result"] == agent.NEEDS_CLICK


def test_scheduling_needs_the_click(monkeypatch):
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "schedule_clips_execute", "arguments": {"clip_ids": [1]}}}]},
        {"role": "assistant", "content": "ok"},
    ])
    assert calls == []
    assert out["steps"][0]["result"] == agent.NEEDS_CLICK


def test_publish_when_done_is_never_taken_from_the_model(monkeypatch):
    fake = _Ollama([
        {"role": "assistant", "tool_calls": [{"function": {"name": "queue_video", "arguments": {
            "url": "https://youtu.be/x", "publish_when_done": ["youtube"]}}}]},
        {"role": "assistant", "content": "Queued"},
    ])
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    seen = []

    def call_tool(name, args):
        seen.append(dict(args))
        return "Queued as job 1", None

    tools = [{"name": "queue_video", "description": "Queue", "handler": lambda a: "",
              "inputSchema": {"type": "object", "properties": {
                  "url": {"type": "string"}, "publish_when_done": {"type": "array"}}}}]
    out = agent.run("clip and post it", [], tools, call_tool, "http://x", "gemma4:e4b")
    assert seen == [{"url": "https://youtu.be/x"}]
    assert agent.NEEDS_CLICK in out["steps"][0]["result"]
    # Not in what the model is shown either, while the MCP tool keeps it.
    sent = fake.seen[0]["tools"][0]["function"]["parameters"]["properties"]
    assert "publish_when_done" not in sent
    assert "publish_when_done" in tools[0]["inputSchema"]["properties"]



def test_the_bars_defaults_never_bring_publish_when_done_back():
    args = agent.with_defaults({"url": "https://youtu.be/x", "captions": False},
                               {"captions": True, "aspect": "9:16", "publish_when_done": ["youtube"]})
    assert args == {"url": "https://youtu.be/x", "captions": False, "aspect": "9:16"}
    assert agent.with_defaults({"url": "u"}, None) == {"url": "u"}


def test_the_real_queue_video_hides_publish_when_done_from_the_box():
    from server.mcp import TOOLS

    specs = {s["function"]["name"]: s["function"] for s in agent.tool_specs(TOOLS)}
    assert "publish_when_done" not in specs["queue_video"]["parameters"]["properties"]
    assert not agent.HUMAN_ONLY & set(specs)
    assert "set_clip_text" in specs


def test_the_system_prompt_no_longer_says_to_execute():
    assert "publish_when_done" not in agent.SYSTEM
    assert "presses its button" in agent.SYSTEM


def test_string_arguments_are_parsed(monkeypatch):
    # Some models hand back the arguments as a JSON string rather than an object.
    out, calls, _ = _run(monkeypatch, [
        {"role": "assistant",
         "tool_calls": [{"function": {"name": "list_videos", "arguments": '{"a": 1}'}}]},
        {"role": "assistant", "content": "done"},
    ])
    assert calls == ["list_videos"]
    assert out["steps"][0]["arguments"] == {"a": 1}


def test_a_model_that_loops_forever_is_stopped(monkeypatch):
    forever = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "list_videos", "arguments": {}}}]}
    ] * (agent.MAX_TURNS + 2)
    out, calls, _ = _run(monkeypatch, forever)
    assert len(calls) == agent.MAX_TURNS
    assert "one thing at a time" in out["reply"]


def test_the_model_is_told_what_time_it_is(monkeypatch):
    _, _, fake = _run(monkeypatch, [{"role": "assistant", "content": "ok"}])
    system = fake.seen[0]["messages"][0]["content"]
    assert "The time right now is" in system
    # Without this the model invented a date in the past and the schedule was
    # refused.
    assert "RFC 3339" in system


def test_scheduling_a_batch_is_planned_not_posted(monkeypatch):
    """Turning a video into a month of posts is still asked for in a sentence:
    the model plans it, the card shows it, the click posts it."""
    tools = [
        *_tools(),
        {"name": "schedule_clips_plan", "description": "Plan", "inputSchema": {"type": "object"},
         "handler": lambda a: "a schedule"},
        {"name": "schedule_clips_execute", "description": "Post",
         "inputSchema": {"type": "object"}, "handler": lambda a: "posted"},
    ]
    names = [spec["function"]["name"] for spec in agent.tool_specs(tools)]
    assert "schedule_clips_execute" not in names
    assert "schedule_clips_plan" in names


def test_a_schedule_plan_reaches_the_card(monkeypatch):
    fake = _Ollama([
        {"role": "assistant", "tool_calls": [{"function": {"name": "schedule_clips_plan", "arguments": {}}}]},
        {"role": "assistant", "content": "Here is the schedule"},
    ])
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    card = {"provider": "woopsocial", "items": [{"clip_id": 3}]}
    out = agent.run("post them", [], [], lambda n, a: ("planned", card), "http://x", "gemma4:e4b")
    assert out["plan"] == card


def test_the_model_gets_room_for_the_tool_list(monkeypatch):
    # The system prompt and tools alone are ~3,700 tokens: in Ollama's 4,096
    # default, Gemma 4 ran out of room mid-thought and answered nothing.
    _, _, fake = _run(monkeypatch, [{"role": "assistant", "content": "ok"}])
    assert fake.seen[0]["options"]["num_ctx"] >= 16384


def test_an_empty_answer_says_so(monkeypatch):
    out, calls, _ = _run(monkeypatch, [{"role": "assistant", "content": ""}])
    assert calls == [] and "didn't answer" in out["reply"]


def test_the_box_offers_the_gemma_4_build_that_fits_the_card():
    # Setup installs a model that cannot call tools on most PCs, so the box
    # offers Gemma 4 itself (#121). Which build is the card's to decide.
    # No card, or one too small for e4b: the smallest build.
    for vram_gb in (None, 0, 4.3):
        assert agent.install_offer(vram_gb)["model"] == "gemma4:e2b"
    # A 6 GB card reports a little over 6 (6144 MiB), and anything bigger is
    # offered the same build: the box needs no more, whatever picks the clips.
    for vram_gb in (6.4, 8.6, 12.9, 25.8):
        assert agent.install_offer(vram_gb)["model"] == "gemma4:e4b"


def test_the_offer_says_how_big_the_download_is():
    for vram_gb in (None, 12.9):
        offer = agent.install_offer(vram_gb)
        assert offer["size_gb"] == agent.GEMMA4_DOWNLOAD_GB[offer["model"]] > 0


def test_the_builds_offered_are_ones_the_models_page_lists():
    # The box and the Models page must not name different Gemma 4 builds.
    from llm.manager import RECOMMENDATIONS

    listed = {model for _hardware, model, _note in RECOMMENDATIONS}
    assert set(agent.GEMMA4_DOWNLOAD_GB) <= listed
