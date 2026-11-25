"""The MCP server speaks the protocol correctly and reports the traps.

Everything here runs against a fake engine, so no API, no network and no
heavy import: these run in CI, where numpy and torch do not exist.
"""

import io
import json
import urllib.error

import pytest

from server import mcp


def _send(message: dict) -> dict | None:
    return mcp.handle(message)


def _request(msg_id, method, params=None):
    out = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        out["params"] = params
    return out


def test_initialize_agrees_on_the_client_version():
    reply = _send(_request(1, "initialize", {"protocolVersion": "2025-03-26"}))
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert reply["result"]["serverInfo"]["name"] == "clips-kitty"


def test_initialize_offers_our_version_when_theirs_is_unknown():
    reply = _send(_request(1, "initialize", {"protocolVersion": "1.0.0"}))
    assert reply["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


def test_a_notification_is_never_answered():
    assert _send({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_is_well_formed():
    tools = _send(_request(2, "tools/list"))["result"]["tools"]
    assert {t["name"] for t in tools} >= {"queue_video", "list_clips", "export_clip"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "handler" not in tool  # the callable must never reach the client
        assert tool["description"]


def test_unknown_tool_is_a_protocol_error():
    reply = _send(_request(3, "tools/call", {"name": "nope", "arguments": {}}))
    assert reply["error"]["code"] == mcp.INVALID_PARAMS


def test_unknown_method_is_a_protocol_error():
    assert _send(_request(4, "nonsense"))["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_queueing_reports_a_job_id(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"job_id": 149})
    reply = _send(_request(5, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    assert reply["result"]["isError"] is False
    assert "149" in reply["result"]["content"][0]["text"]


def test_an_already_processed_video_explains_itself(monkeypatch):
    # job_id null is not a failure, and an agent that assumes otherwise will
    # tell the user their video was queued when it was not.
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "job_id": None, "already_processed": True, "video_id": "abc"})
    reply = _send(_request(6, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is False
    assert "Not queued" in text and "force" in text


def test_no_clips_is_not_confused_with_a_wrong_id(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: [])
    reply = _send(_request(7, "tools/call", {
        "name": "list_clips", "arguments": {"video_id": "typo"}}))
    assert "list_videos" in reply["result"]["content"][0]["text"]


@pytest.mark.parametrize("start,end,wanted", [
    (134.54, 172.32, "2m15s-2m52s"), (59.9, 89.9, "1m00s-1m30s"),
])
def test_clip_timestamps_round_seconds_without_rounding_minutes_twice(monkeypatch, start, end, wanted):
    monkeypatch.setattr(mcp, "_request", lambda *_a: [{"id": 1, "start_s": start, "end_s": end, "score": 80}])
    reply = _send(_request(8, "tools/call", {"name": "list_clips", "arguments": {"video_id": "sample"}}))
    assert wanted in reply["result"]["content"][0]["text"]


def test_a_stopped_engine_is_a_tool_error_not_a_crash(monkeypatch):
    def refuse(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mcp, "_request", refuse)
    reply = _send(_request(8, "tools/call", {"name": "queue_status", "arguments": {}}))
    assert reply["result"]["isError"] is True
    assert "not answering" in reply["result"]["content"][0]["text"]


def test_the_publishing_tools_are_offered():
    tools = _send(_request(20, "tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"youtube_status", "publish_plan", "publish_plan_execute", "publish_status"} <= names


def test_a_plan_says_plainly_that_nothing_was_uploaded(monkeypatch):
    # An agent reads only this text, so it has to carry the fact that a plan is
    # a proposal and the confirmation step is not optional.
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "items": [{"clip_id": 1, "title": "One", "publish_at": None},
                  {"clip_id": 2, "title": "Two", "publish_at": "2026-09-18T17:00:00Z"}],
        "warnings": [],
    })
    reply = _send(_request(21, "tools/call", {
        "name": "publish_plan", "arguments": {"clip_ids": [1, 2]}}))
    text = reply["result"]["content"][0]["text"]
    assert "NOTHING has been uploaded" in text
    assert "publish_plan_execute" in text
    assert "clip 1" in text and "clip 2" in text


def test_a_plan_passes_on_the_quota_warning(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "items": [{"clip_id": 1, "title": "One", "publish_at": None}],
        "warnings": ["3 clips, but only 1 uploads left on today's quota."],
    })
    reply = _send(_request(22, "tools/call", {
        "name": "publish_plan", "arguments": {"clip_ids": [1]}}))
    assert "quota" in reply["result"]["content"][0]["text"]


def test_executing_reports_what_was_skipped(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {
        "started": [{"clip_id": 1, "publish_job_id": 9}],
        "skipped": [{"clip_id": 2, "reason": "This clip has no rendered file yet."}],
    })
    reply = _send(_request(23, "tools/call", {
        "name": "publish_plan_execute", "arguments": {"items": [{"clip_id": 1}, {"clip_id": 2}]}}))
    text = reply["result"]["content"][0]["text"]
    assert "Started 1 upload" in text
    assert "clip 2 skipped" in text


def test_executing_nothing_uploads_nothing(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("must not call the API with an empty plan")

    monkeypatch.setattr(mcp, "_request", explode)
    reply = _send(_request(24, "tools/call", {
        "name": "publish_plan_execute", "arguments": {"items": []}}))
    assert "nothing was published" in reply["result"]["content"][0]["text"]


def test_youtube_off_tells_the_agent_to_stop_rather_than_improvise(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"enabled": False})
    reply = _send(_request(25, "tools/call", {"name": "youtube_status", "arguments": {}}))
    text = reply["result"]["content"][0]["text"]
    assert "switched off" in text
    assert "not something to work around" in text


def test_serve_reads_lines_and_writes_one_message_per_line():
    stdin = io.StringIO(
        json.dumps(_request(1, "initialize", {"protocolVersion": "2025-06-18"})) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps(_request(2, "tools/list")) + "\n"
    )
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    lines = [ln for ln in stdout.getvalue().split("\n") if ln]
    assert len(lines) == 2  # the notification is not answered
    assert json.loads(lines[0])["id"] == 1
    assert json.loads(lines[1])["result"]["tools"]


def test_bad_json_gets_a_parse_error():
    stdout = io.StringIO()
    mcp.serve(io.StringIO("{not json\n"), stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == mcp.PARSE_ERROR


def test_the_api_base_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CLIPS_STUDIO_API", "http://127.0.0.1:9999/")
    assert mcp.api_base() == "http://127.0.0.1:9999"


def test_the_box_can_set_the_things_the_panel_has_checkboxes_for(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(30, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "captions": False, "long_clips": True,
        "podcast": True}}))
    assert sent["body"]["captions"] is False
    assert sent["body"]["long_clips"] is True
    assert sent["body"]["podcast"] is True


def test_captions_left_alone_are_not_sent_at_all(monkeypatch):
    # The default lives in settings; sending None would override it with nothing.
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(31, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x"}}))
    assert "captions" not in sent["body"]


def test_longform_becomes_a_mode(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(32, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "longform": "highlights"}}))
    assert sent["body"]["longform"] == {"mode": "highlights"}


def test_a_watermark_is_looked_up_by_name(monkeypatch):
    calls = []

    def fake(method, path, body=None):
        calls.append(path)
        if path == "/branding":
            return [{"id": 3, "name": "Main channel"}, {"id": 4, "name": "Alt"}]
        return {"job_id": 7, "_body": body}

    monkeypatch.setattr(mcp, "_request", fake)
    mcp.handle(_request(33, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "watermark": "main channel"}}))
    assert "/branding" in calls


def test_a_watermark_that_does_not_exist_lists_the_real_ones(monkeypatch):
    # Guessing an id here would brand a whole stream with the wrong logo.
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: (
        [{"id": 3, "name": "Main channel"}] if p == "/branding" else {"job_id": 7}))
    reply = mcp.handle(_request(34, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "watermark": "nope"}}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert "Main channel" in text


def test_every_longform_mode_in_the_dropdown_can_be_asked_for():
    # The panel offers four; the box offered three, so "clips up to 140s for X"
    # quietly did something else.
    tools = {t["name"]: t for t in mcp.TOOLS}
    modes = tools["queue_video"]["inputSchema"]["properties"]["longform"]["enum"]
    assert modes == ["short_clips", "clips_140", "highlights", "edited_stream"]


def test_a_caption_font_is_matched_however_it_was_typed():
    assert mcp._caption_style({"font": "impact"})["font"] == "Impact"
    assert mcp._caption_style({"font": "comic sans"})["font"] == "Comic Sans MS"


def test_a_font_that_is_not_installed_is_refused_with_the_list():
    # An unknown font renders as something else without complaining, so every
    # clip of the stream would be wrong and nothing would say why.
    with pytest.raises(ValueError, match="Impact"):
        mcp._caption_style({"font": "Papyrus"})


def test_colour_words_work_as_well_as_hex():
    assert mcp._caption_style({"color": "yellow"})["color"] == "#FFE600"
    assert mcp._caption_style({"color": "#ff0000"})["color"] == "#FF0000"
    with pytest.raises(ValueError, match="not a colour"):
        mcp._caption_style({"highlight_color": "chartreuse"})


def test_centre_means_middle():
    assert mcp._caption_style({"position": "centre"})["position"] == "middle"
    assert mcp._caption_style({"position": "Center"})["position"] == "middle"
    with pytest.raises(ValueError, match="bottom, middle, top"):
        mcp._caption_style({"position": "diagonal"})


def test_sizes_are_clamped_rather_than_refused():
    # "make them huge" is a real request; it should land at the top of the
    # range instead of failing.
    assert mcp._caption_style({"font_size": 900})["font_size"] == 160
    assert mcp._caption_style({"words_per_caption": 99})["words_per_caption"] == 6


def test_what_was_not_asked_for_is_left_alone():
    # Anything sent here overrides the user's saved style, so silence matters.
    assert mcp._caption_style({"uppercase": False}) == {"uppercase": False}


def test_a_caption_request_reaches_the_job(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.update({"body": b})
                        or {"job_id": 7})
    mcp.handle(_request(35, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x",
        "caption_style": {"position": "top", "highlight": True, "highlight_color": "gold"}}}))
    assert sent["body"]["caption_style"] == {
        "position": "top", "highlight": True, "highlight_color": "#FFD700"}


def test_gaming_reaches_the_engine(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.append((p, b)) or {"job_id": 7})
    mcp.handle(_request(33, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "gaming": True}}))
    mcp.handle(_request(34, "tools/call", {"name": "queue_local_file", "arguments": {
        "path": "C:/vods/vod.mp4", "gaming": True}}))
    assert sent == [("/jobs", {"url": "https://youtu.be/x", "gaming": True}),
                    ("/videos/local", {"path": "C:/vods/vod.mp4", "gaming": True})]


@pytest.mark.parametrize("tool,path_key,endpoint", [
    ("queue_video", "url", "/jobs"), ("queue_local_file", "path", "/videos/local"),
])
def test_gaming_detector_and_layout_reach_the_same_engine_options(monkeypatch, tool, path_key, endpoint):
    sent = []
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.append((p, b)) or {"job_id": 7})
    layout = {"preset": "split", "cam": [0.7, 0.1, 0.2, 0.3]}
    options = {"gaming": True, "pipeline": {"id": "example-dev/quark-game"},
               "gaming_layout": layout, "captions": False, "max_clips": 2, "min_score": 55, "force": True}
    args = {path_key: "test-source", **options}
    result = mcp.handle(_request(35, "tools/call", {"name": tool, "arguments": args}))
    assert not result["result"]["isError"]
    assert sent == [(endpoint, args)]
    schema = next(t for t in mcp.TOOLS if t["name"] == tool)["inputSchema"]["properties"]
    assert options.keys() <= schema.keys()


def test_vertical_live_and_an_original_link_reach_the_engine(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None: sent.append((p, b)) or {"job_id": 7})
    mcp.handle(_request(31, "tools/call", {"name": "queue_video", "arguments": {
        "url": "https://youtu.be/x", "vertical_live": True}}))
    mcp.handle(_request(32, "tools/call", {"name": "queue_local_file", "arguments": {
        "path": "C:/lives/live.mp4", "vertical_live": True,
        "source_url": "https://www.tiktok.com/@someone/live"}}))
    assert sent[0] == ("/jobs", {"url": "https://youtu.be/x", "vertical_live": True})
    assert sent[1] == ("/videos/local", {"path": "C:/lives/live.mp4", "vertical_live": True,
                                         "source_url": "https://www.tiktok.com/@someone/live"})


def test_a_matchs_events_and_reels_said_to_the_assistant_reach_the_job(monkeypatch):
    """The Sport row has no box for match events or story reels: they're said
    in Ask Clips Kitty, so its tool takes them and hands them on untouched."""
    sent = {}

    def request(method, path, body=None, *a, **k):
        sent.update(body or {})
        return {"job_id": 150}

    monkeypatch.setattr(mcp, "_request", request)
    sport = {"name": "soccer", "highlights": "goals", "events": "18:16 Goal Player A\n28:40 Goal",
             "reels": ["recap"]}
    reply = _send(_request(6, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x", "sport": sport}}))
    assert reply["result"]["isError"] is False
    assert sent["sport"] == sport
    props = mcp.SPORT_PARAM["properties"]
    assert "events" in props and props["reels"]["items"]["enum"] == ["recap", "teams", "players"]


# ---- start_queue and clip_posts ---------------------------------------------


def _call(name: str, arguments: dict) -> dict:
    return _send(_request(30, "tools/call", {"name": name, "arguments": arguments}))["result"]


def test_the_new_tools_are_offered():
    names = {t["name"] for t in _send(_request(29, "tools/list"))["result"]["tools"]}
    assert {"start_queue", "clip_posts"} <= names
    assert len(names) == len(mcp.TOOLS) == 22


def test_start_queue_presses_start_then_reads_the_queue(monkeypatch):
    calls = []

    def fake(method, path, body=None, *a, **k):
        calls.append((method, path))
        if path == "/queue":
            return {"paused": False, "processing": [], "queued": [{"id": 4}, {"id": 5}]}
        return {"paused": False}

    monkeypatch.setattr(mcp, "_request", fake)
    result = _call("start_queue", {})
    assert calls == [("POST", "/queue/resume"), ("GET", "/queue")]
    assert result["isError"] is False
    assert result["content"][0]["text"] == (
        "The queue is running: 2 waiting; the first starts now. Follow it with job_status."
    )


def test_start_queue_with_nothing_waiting_says_so(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None, *a, **k: (
        {"processing": [], "queued": []} if p == "/queue" else {"paused": False}))
    text = _call("start_queue", {})["content"][0]["text"]
    assert "nothing is waiting" in text


def test_a_paused_queue_names_start_queue(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"paused": True, "capacity": 5})
    assert "start_queue" in _call("queue_status", {})["content"][0]["text"]
    assert "start_queue" in mcp.INSTRUCTIONS and "clip_posts" in mcp.INSTRUCTIONS


def _post(platform, provider, state, **extra):
    row = {"clip_id": 7, "platform": platform, "provider": provider, "state": state,
           "post_id": "", "post_url": "", "error": "", "request_id": "", "scheduled_for": "",
           "live": False, "created_at": "2026-11-22 10:00:00", "updated_at": "2026-11-22 10:00:00"}
    row.update(extra)
    return row


FOUR_POSTS = [
    _post("tiktok", "woopsocial", "published", post_url="https://example.com/v/1", live=True),
    _post("youtube", "woopsocial", "queued", scheduled_for="2026-11-22T15:00:00Z"),
    _post("instagram", "woopsocial", "published"),
    _post("facebook", "uploadpost", "failed", error="video too short"),
]


def test_clip_posts_reads_one_clip(monkeypatch):
    calls = []

    def fake(method, path, body=None, *a, **k):
        calls.append((method, path))
        return {"clip_id": 7, "posts": FOUR_POSTS}

    monkeypatch.setattr(mcp, "_request", fake)
    result = _call("clip_posts", {"clip_id": 7})
    assert calls == [("GET", "/clips/7/posts")]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "\n".join([
        "Clip 7:",
        "  tiktok (woopsocial): LIVE https://example.com/v/1",
        "  youtube (woopsocial): scheduled for 2026-11-22 15:00 UTC",
        "  instagram (woopsocial): published, link not known yet (links arrive within minutes; check again)",
        "  facebook (uploadpost): failed: video too short",
    ])


def test_clip_posts_for_a_clip_never_posted(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"clip_id": 3, "posts": []})
    assert _call("clip_posts", {"clip_id": 3})["content"][0]["text"] == (
        "Clip 3 has not been posted anywhere."
    )


def test_clip_posts_wants_exactly_one_of_clip_and_video(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("nothing should be asked of the engine")

    monkeypatch.setattr(mcp, "_request", explode)
    assert "exactly one" in _call("clip_posts", {})["content"][0]["text"]
    assert "exactly one" in _call("clip_posts", {"clip_id": 1, "video_id": "v"})["content"][0]["text"]


def test_clip_posts_for_a_video_lists_only_posted_clips(monkeypatch):
    calls = []
    answers = {
        "/videos/abc/clips": [{"id": 1}, {"id": 2}, {"id": 3}],
        "/clips/1/posts": {"clip_id": 1, "posts": [FOUR_POSTS[0]]},
        "/clips/2/posts": {"clip_id": 2, "posts": []},
        "/clips/3/posts": {"clip_id": 3, "posts": []},
    }

    def fake(method, path, body=None, *a, **k):
        calls.append((method, path))
        return answers[path]

    monkeypatch.setattr(mcp, "_request", fake)
    text = _call("clip_posts", {"video_id": "abc"})["content"][0]["text"]
    assert calls == [("GET", "/videos/abc/clips"), ("GET", "/clips/1/posts"),
                     ("GET", "/clips/2/posts"), ("GET", "/clips/3/posts")]
    assert text == "\n".join([
        "Clip 1:",
        "  tiktok (woopsocial): LIVE https://example.com/v/1",
        "2 clips not posted anywhere.",
    ])


def test_clip_posts_for_a_big_video_stays_under_the_assistants_cut(monkeypatch):
    clips = [{"id": n} for n in range(1, 61)]
    long_url = "https://example.com/" + "x" * 60

    def fake(method, path, body=None, *a, **k):
        if path.startswith("/videos/"):
            return clips
        clip_id = int(path.split("/")[2])
        return {"clip_id": clip_id, "posts": [
            _post("tiktok", "woopsocial", "published", post_url=long_url, live=True),
            _post("youtube", "woopsocial", "published", post_url=long_url, live=True),
        ]}

    monkeypatch.setattr(mcp, "_request", fake)
    text = _call("clip_posts", {"video_id": "big"})["content"][0]["text"]
    lines = text.split("\n")
    assert len(text) < 3600
    assert len(lines) <= mcp.CLIP_POSTS_MAX_LINES + 2
    assert "more line(s) left out" in lines[-1]


def test_clip_posts_for_an_unknown_clip(monkeypatch):
    def fake(method, path, body=None, *a, **k):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8765" + path, 404, "Not Found", {},
            io.BytesIO(b'{"detail":"no such clip"}'))

    monkeypatch.setattr(mcp, "_request", fake)
    result = _call("clip_posts", {"clip_id": 999})
    assert result["content"][0]["text"] == "No clip with id 999. list_clips shows the ids."


def test_clip_posts_on_an_engine_without_the_posts_route(monkeypatch):
    """An engine from before GET /clips/{id}/posts answers FastAPI's own 404."""
    def fake(method, path, body=None, *a, **k):
        if path.startswith("/videos/"):
            return [{"id": 1}, {"id": 2}]
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8765" + path, 404, "Not Found", {},
            io.BytesIO(b'{"detail":"Not Found"}'))

    monkeypatch.setattr(mcp, "_request", fake)
    for args in ({"clip_id": 7}, {"video_id": "abc"}):
        result = _call("clip_posts", args)
        text = result["content"][0]["text"]
        assert result["isError"] is True
        assert text == mcp.ENGINE_TOO_OLD
        assert "too old" in text and "Update Clips Kitty" in text
        assert "No clip with id" not in text


def test_a_youtube_upload_past_its_time_is_not_called_scheduled(monkeypatch):
    posts = [
        _post("youtube", "youtube", "private", scheduled_for="2026-11-20T15:00:00Z"),
        _post("youtube", "youtube", "checking", scheduled_for="2026-11-22T09:00:00Z"),
        _post("youtube", "youtube", "scheduled", scheduled_for="2026-11-30T15:00:00Z"),
    ]
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: {"clip_id": 7, "posts": posts})
    assert _call("clip_posts", {"clip_id": 7})["content"][0]["text"] == "\n".join([
        "Clip 7:",
        "  youtube (youtube): private on YouTube (not public, so there is no link to share)",
        "  youtube (youtube): its time has passed; checking whether it went live"
        " (check again in a few minutes)",
        "  youtube (youtube): scheduled for 2026-11-30 15:00 UTC",
    ])


def test_every_tool_is_named_in_the_api_guide():
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[1] / "docs" / "API.md").read_text(encoding="utf-8")
    missing = [t["name"] for t in mcp.TOOLS if f"`{t['name']}`" not in guide]
    assert not missing, f"MCP tools docs/API.md never names: {missing}"


def test_stdin_is_read_as_utf8_whatever_the_console_code_page(monkeypatch):
    """A Windows pipe defaults to cp1252; MCP messages are UTF-8 and may carry emoji."""
    focus = "the 🔥 moments – near the end"
    message = _request(31, "tools/call", {
        "name": "queue_video", "arguments": {"url": "https://youtu.be/x", "focus": focus}})
    raw = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252"))
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None, *a, **k: sent.update(b or {})
                        or {"job_id": 12})
    stdout = io.StringIO()
    assert mcp.serve(stdout=stdout) == 0
    reply = json.loads(stdout.getvalue())
    assert reply["result"]["isError"] is False
    assert sent["focus"] == focus


def test_clip_posts_quotes_a_mistyped_video_id(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None, *a, **k: calls.append(p) or [])
    text = _call("clip_posts", {"video_id": "my video"})["content"][0]["text"]
    assert calls == ["/videos/my%20video/clips"]
    assert text.startswith("No clips for my video.")


# ---- set_clip_text ------------------------------------------------------------


def test_set_clip_text_patches_only_what_was_passed(monkeypatch):
    calls = []

    def fake(method, path, body=None, *a, **k):
        calls.append((method, path, body))
        return {"id": 9, "title": "New hook", "description": "old", "hashtags": ["gaming"]}

    monkeypatch.setattr(mcp, "_request", fake)
    result = _call("set_clip_text", {"clip_id": 9, "title": "New hook"})
    assert calls == [("PATCH", "/clips/9", {"title": "New hook"})]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "Clip 9 updated.\nTitle: New hook"


def test_set_clip_text_sends_hashtags_as_a_list(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_request", lambda m, p, b=None, *a, **k: (
        sent.update(b), {"hashtags": b["hashtags"], "description": b["description"]})[1])
    text = _call("set_clip_text", {"clip_id": 3, "description": "A clutch win",
                                   "hashtags": "gaming, gta6 #clips"})["content"][0]["text"]
    assert sent == {"description": "A clutch win", "hashtags": ["gaming", "gta6", "#clips"]}
    assert "Hashtags: #gaming #gta6 #clips" in text


def test_set_clip_text_with_nothing_to_change_calls_nothing(monkeypatch):
    monkeypatch.setattr(mcp, "_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert _call("set_clip_text", {"clip_id": 3})["content"][0]["text"].startswith("Nothing changed")


def test_set_clip_text_names_a_missing_clip(monkeypatch):
    import urllib.error

    def missing(*a, **k):
        raise urllib.error.HTTPError("http://x/clips/404", 404, "Not Found", {}, None)

    monkeypatch.setattr(mcp, "_request", missing)
    text = _call("set_clip_text", {"clip_id": 404, "title": "x"})["content"][0]["text"]
    assert text == "There is no clip 404. list_clips shows the ids that exist."


def test_set_clip_text_sends_only_fields_patch_clips_takes():
    api = pytest.importorskip("server.api")
    tool = next(t for t in mcp.TOOLS if t["name"] == "set_clip_text")
    props = set(tool["inputSchema"]["properties"]) - {"clip_id"}
    assert props <= set(api.ClipPatch.model_fields)
    assert tool["inputSchema"]["required"] == ["clip_id"]


def _mp4(path, seconds: float, version: int = 0, moov_last: bool = True):
    """A file with just the boxes a player reads the length from."""
    scale = 1000
    if version == 1:
        mvhd_body = bytes([1, 0, 0, 0]) + bytes(16) + scale.to_bytes(4, "big") + int(seconds * scale).to_bytes(8, "big")
    else:
        mvhd_body = bytes(4) + bytes(8) + scale.to_bytes(4, "big") + int(seconds * scale).to_bytes(4, "big")
    mvhd_body += bytes(80)
    mvhd = (8 + len(mvhd_body)).to_bytes(4, "big") + b"mvhd" + mvhd_body
    trak = (8 + 4).to_bytes(4, "big") + b"trak" + bytes(4)
    moov_body = trak + mvhd
    moov = (8 + len(moov_body)).to_bytes(4, "big") + b"moov" + moov_body
    ftyp = (16).to_bytes(4, "big") + b"ftypisom" + bytes(4)
    mdat = (8 + 1000).to_bytes(4, "big") + b"mdat" + bytes(1000)
    path.write_bytes(ftyp + (mdat + moov if moov_last else moov + mdat))
    return path


@pytest.mark.parametrize("version,moov_last", [(0, True), (0, False), (1, True)])
def test_the_mp4_header_gives_the_files_length(tmp_path, version, moov_last):
    clip = _mp4(tmp_path / "c.mp4", 22.3, version=version, moov_last=moov_last)
    assert mcp._mp4_seconds(clip) == pytest.approx(22.3)


def test_a_missing_or_broken_file_has_no_length(tmp_path):
    assert mcp._mp4_seconds(tmp_path / "gone.mp4") is None
    (tmp_path / "junk.mp4").write_bytes(b"not a video at all")
    assert mcp._mp4_seconds(tmp_path / "junk.mp4") is None


def test_list_clips_shows_each_files_length(tmp_path, monkeypatch):
    # "The last 3 seconds" needs the file's length: 9m04s-9m23s is 19 s of
    # the video, but the file with its end card is 22.3 s.
    clip = _mp4(tmp_path / "c.mp4", 22.3)
    monkeypatch.setattr(mcp, "_request", lambda *_a: [
        {"id": 7, "start_s": 544, "end_s": 563, "score": 88, "title": "Big win", "path": str(clip)},
        {"id": 8, "start_s": 600, "end_s": 620, "score": 70, "title": "Not rendered", "path": ""},
    ])
    text = _send(_request(9, "tools/call", {"name": "list_clips", "arguments": {"video_id": "v"}}))[
        "result"]["content"][0]["text"]
    first, second = text.splitlines()
    assert "9m04s-9m23s  file 22.3s  Big win" in first
    assert "file" not in second and "Not rendered" in second
    tool = next(t for t in mcp.TOOLS if t["name"] == "list_clips")
    assert "length in seconds" in tool["description"] and "end card" in tool["description"]


def test_queue_local_file_passes_the_bars_switches(monkeypatch):
    sent = {}

    def fake(method, path, body=None):
        sent.update(body or {})
        return {"job_id": 5, "video_id": "local-1"}

    monkeypatch.setattr(mcp, "_request", fake)
    mcp._queue_local_file({"path": "C:/v.mp4", "podcast": True, "long_clips": True, "gaming_scoring": True,
                           "longform": {"mode": "highlights"}, "watermark_profile_id": 3,
                           "caption_style": {"font": "Impact", "position": "middle"}})
    assert sent["podcast"] is True and sent["long_clips"] is True and sent["gaming_scoring"] is True
    assert sent["longform"] == {"mode": "highlights"} and sent["watermark_profile_id"] == 3
    assert sent["caption_style"] == {"font": "Impact", "position": "middle"}

def test_queue_video_keeps_the_bars_long_form_and_branding(monkeypatch):
    """The bar sends longform as {"mode": ...} and its branding as an id; a
    link must not wrap the one again or drop the other."""
    sent = {}

    def fake(method, path, body=None):
        sent.update(body or {})
        return {"job_id": 6, "video_id": "abc"}

    monkeypatch.setattr(mcp, "_request", fake)
    mcp._queue_video({"url": "https://youtu.be/x", "longform": {"mode": "highlights", "shorts": True},
                      "watermark_profile_id": 4, "gaming_scoring": True})
    assert sent["longform"] == {"mode": "highlights", "shorts": True}
    assert sent["watermark_profile_id"] == 4 and sent["gaming_scoring"] is True
    sent.clear()
    mcp._queue_video({"url": "https://youtu.be/x", "longform": "edited_stream"})
    assert sent["longform"] == {"mode": "edited_stream"}
