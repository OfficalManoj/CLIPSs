"""The assistant on a cloud model with the user's own key.

run() (the local Ollama path) is covered by tests/test_agent.py and is left
exactly as it was; run_cloud() is its twin through backend.chat().
"""

from llm.base import ChatTurn, LLMBackend, ToolCall
from server.agent import run_cloud

TOOLS = [{"name": "list_videos", "description": "List videos", "inputSchema": {"type": "object"}},
         {"name": "publish_plan", "description": "Plan", "inputSchema": {"type": "object"}}]


class Scripted(LLMBackend):
    name = "openrouter/meta/muse-spark-1.3"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def generate(self, prompt, *, json_mode=False):
        raise AssertionError("the assistant chats, it does not generate")

    def chat(self, messages, tools):
        self.seen.append(([dict(m) for m in messages], tools))
        return self.turns.pop(0)


def test_it_calls_tools_then_answers():
    backend = Scripted([
        ChatTurn(tool_calls=[ToolCall(id="c1", name="list_videos", arguments={"limit": 5})], raw=["r1"]),
        ChatTurn(text="You have two videos."),
    ])
    ran = []

    def call_tool(name, args):
        ran.append((name, args))
        return "two videos", None

    result = run_cloud("what videos do I have?", [], TOOLS, call_tool, backend)
    assert result["reply"] == "You have two videos."
    assert result["model"] == "openrouter/meta/muse-spark-1.3"
    assert ran == [("list_videos", {"limit": 5})]
    assert result["steps"][0]["tool"] == "list_videos"

    second_messages, tools = backend.seen[1]
    assert second_messages[0]["role"] == "system"
    assistant, tool = second_messages[-2], second_messages[-1]
    assert assistant["raw"] == ["r1"] and assistant["tool_calls"][0]["id"] == "c1"
    assert tool == {"role": "tool", "tool_call_id": "c1", "name": "list_videos", "content": "two videos"}
    assert tools[0]["function"]["name"] == "list_videos"


def test_a_publish_plan_reaches_the_window():
    backend = Scripted([
        ChatTurn(tool_calls=[ToolCall(id="c1", name="publish_plan", arguments={})]),
        ChatTurn(text="Here is the plan."),
    ])
    result = run_cloud("publish them", [], TOOLS, lambda n, a: ("planned", {"items": [1]}), backend)
    assert result["plan"] == {"items": [1]}


def test_earlier_turns_are_passed_as_plain_text():
    backend = Scripted([ChatTurn(text="ok")])
    run_cloud("and now?", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                           {"role": "tool", "content": "ignored"}], TOOLS, lambda n, a: ("", None), backend)
    messages = backend.seen[0][0]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_an_empty_cloud_answer_says_so():
    from llm.base import ChatTurn

    class Silent:
        name = "openrouter/test"

        def chat(self, messages, tools):
            return ChatTurn(text="", tool_calls=[], raw=None)

    out = run_cloud("clip this", [], [], lambda *_a: ("", None), Silent())
    assert "didn't answer" in out["reply"]


def test_posting_needs_the_click_on_a_cloud_model_too():
    """The same rule as run(): the posting tools never run from the box, and
    the model is told it needs the person's click on the plan card."""
    from server.agent import HUMAN_ONLY, NEEDS_CLICK

    for name in sorted(HUMAN_ONLY):
        backend = Scripted([
            ChatTurn(tool_calls=[ToolCall(id="c1", name=name, arguments={"clip_ids": [1]})]),
            ChatTurn(text="Press the button on the card."),
        ])
        ran = []
        result = run_cloud("post them", [], TOOLS, lambda n, a, ran=ran: (ran.append(n), ("posted", None))[1], backend)
        assert ran == [], name
        assert result["steps"][0]["result"] == NEEDS_CLICK
        assert backend.seen[1][0][-1]["content"] == NEEDS_CLICK


def test_publish_when_done_is_dropped_on_a_cloud_model_too():
    from server.agent import NEEDS_CLICK

    backend = Scripted([
        ChatTurn(tool_calls=[ToolCall(id="c1", name="queue_video",
                                      arguments={"url": "https://youtu.be/x", "publish_when_done": ["tiktok"]})]),
        ChatTurn(text="Queued."),
    ])
    seen = []
    result = run_cloud("clip and post", [], TOOLS, lambda n, a: (seen.append(dict(a)), ("Queued", None))[1],
                       backend)
    assert seen == [{"url": "https://youtu.be/x"}]
    assert NEEDS_CLICK in result["steps"][0]["result"]


def test_a_schedule_plan_reaches_the_card_on_a_cloud_model():
    backend = Scripted([
        ChatTurn(tool_calls=[ToolCall(id="c1", name="schedule_clips_plan", arguments={})]),
        ChatTurn(text="Here is the schedule."),
    ])
    card = {"provider": "woopsocial", "items": [{"clip_id": 2}]}
    result = run_cloud("post them 5 a day", [], TOOLS, lambda n, a: ("planned", card), backend)
    assert result["plan"] == card


def test_where_the_box_is_reaches_a_cloud_model_too():
    backend = Scripted([ChatTurn(text="ok")])
    run_cloud("retitle this clip", [], TOOLS, lambda n, a: ("", None), backend,
              context="the Clip Editor, with clip 7 open")
    assert "clip 7 open" in backend.seen[0][0][0]["content"]
