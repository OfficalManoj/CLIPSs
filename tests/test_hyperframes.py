"""Animations with HyperFrames (video/hyperframes.py), against a fake CLI.

HyperFrames is used as published: their `init`, `add`, `lint`, `check` and
`render`. These tests stand in for their CLI (no Node, no Chrome, no network),
and pin what Clips Kitty sends it and what it does with the answers:

- every run gets the agreed environment, and EBUSY (their #4058) is retried;
- a catalog block is added by their `add`, hosted on a transparent root;
- a request is written by the app's own provider system, so an OpenRouter
  request carries the attribution headers, and gets one retry with the errors;
- the finished MOV is laid over the clip from its start, alpha kept, after the
  watermark, and a clip with no animations is never touched.
"""

import json
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from core import binaries
from video import hyperframes as hf

ATTRIBUTION = {
    "HTTP-Referer": "https://colingpt9.github.io/clips-studio/",
    "X-OpenRouter-Title": "Clips Kitty",
    "X-OpenRouter-Categories": "video-gen",
}

GOOD_HTML = """<!doctype html>
<html><head><style>html, body, #root { background: transparent; }</style></head>
<body><div id="root" data-composition-id="main" data-start="0" data-duration="3"
 data-width="1080" data-height="1920"><div id="t" class="clip" data-start="0" data-duration="3">Hi</div></div>
<script>window.__timelines["main"] = gsap.timeline({ paused: true });</script></body></html>"""

LINT_OK = {"ok": True, "errorCount": 0, "findings": []}
LINT_BAD = {"ok": False, "errorCount": 1, "findings": [
    {"code": "missing_timeline_registry", "severity": "error",
     "message": "Missing `window.__timelines` registration.",
     "fixHint": "Register each composition timeline on `window.__timelines[compositionId]`."}]}
CHECK_OK = {"ok": True, "lint": LINT_OK, "runtime": {"findings": []},
            "layout": {"findings": [{"code": "outside_safe_zone", "severity": "warning", "message": "x"}]}}


class FakeCli:
    """Answers like their CLI, by command, and records every call."""

    def __init__(self, lint=None, catalog=None):
        self.calls = []
        self.envs = []
        self.lint = list(lint or [LINT_OK])
        self.catalog = catalog or []
        self.busy = 0  # EBUSY answers to give before a real one

    def __call__(self, cmd, cwd=None, env=None, **kw):
        if cmd[-1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "v22.11.0\n", "")
        args = cmd[2:]
        self.calls.append(args)
        self.envs.append(env)
        if self.busy:
            self.busy -= 1
            return subprocess.CompletedProcess(cmd, 1, "", "Error: spawn EBUSY")
        name, out = args[0], ""
        if name == "init":
            project = Path(cwd) / args[1]
            project.mkdir(parents=True)
            (project / "index.html").write_text("<html></html>", encoding="utf-8")
        elif name == "add":
            block = args[1]
            project = Path(args[args.index("--dir") + 1])
            (project / "compositions").mkdir(exist_ok=True)
            (project / "compositions" / f"{block}.html").write_text(
                "<html><body><div data-composition-id=\"b\" data-composition-variables='["
                '{"id":"name","type":"string","label":"Name","default":"Dr. Maya Chen"}]\'>'
                "</div></body></html>", encoding="utf-8")
            snippet = (f'<div data-composition-id="{block}" data-composition-src="compositions/{block}.html" '
                       'data-duration="4.8" data-width="1920" data-height="1080"></div>')
            out = json.dumps({"ok": True, "name": block, "snippet": snippet, "warnings": []})
        elif name == "lint":
            out = json.dumps(self.lint.pop(0) if len(self.lint) > 1 else self.lint[0])
        elif name == "check":
            out = "[hyperframes] check: starting Chrome\n" + json.dumps(CHECK_OK)
        elif name == "render":
            Path(args[args.index("-o") + 1]).write_bytes(b"mov")
        elif name == "catalog":
            out = json.dumps(self.catalog)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def cli(monkeypatch, tmp_path):
    root = tmp_path / "vendor-hf"
    script = hf.cli_script(root)
    script.parent.mkdir(parents=True)
    script.write_text("// their CLI", encoding="utf-8")
    monkeypatch.setenv("CLIPS_STUDIO_HYPERFRAMES", str(root))
    monkeypatch.setenv("CLIPS_STUDIO_NODE", "node-test")
    fake = FakeCli()
    monkeypatch.setattr(hf, "spawn", fake)
    monkeypatch.setattr(hf, "sleep", lambda s: None)
    monkeypatch.setattr(hf, "alpha_cover", lambda mov, at: 0.2)
    hf._variables_cache.clear()
    return fake


# ---- environment, Node, EBUSY --------------------------------------------------


def test_every_run_gets_the_agreed_environment(cli, tmp_path):
    hf.run_cli(["lint", "--json"], cwd=tmp_path)
    env = cli.envs[-1]
    for key in ("HYPERFRAMES_AGENT", "HYPERFRAMES_NO_TELEMETRY", "HYPERFRAMES_NO_UPDATE_CHECK",
                "HYPERFRAMES_SKIP_SKILLS", "HYPERFRAMES_NO_OPTIONAL_INSTALL"):
        assert env[key] == "1", key
    assert env["PRODUCER_RESTRICT_PAGE_NETWORK"] == "true"
    assert env["HYPERFRAMES_FFMPEG_PATH"] == binaries.ffmpeg()
    assert env["HYPERFRAMES_FFPROBE_PATH"] == binaries.ffprobe()
    assert env["HYPERFRAMES_CACHE_DIR"].endswith("cache")


def test_their_cli_never_gets_the_engines_keys(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", "s3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
    monkeypatch.setenv("GITHUB_TOKEN", "t1")
    monkeypatch.setenv("SOME_PASSWORD", "p1")
    monkeypatch.setenv("CLIPS_STUDIO_KEEP_ME", "yes")
    hf.run_cli(["lint", "--json"], cwd=tmp_path)
    env = cli.envs[-1]
    for name in ("CLIPS_KITTY_SESSION_SECRET", "OPENROUTER_API_KEY", "GITHUB_TOKEN", "SOME_PASSWORD"):
        assert name not in env, name
    assert env["CLIPS_STUDIO_KEEP_ME"] == "yes"
    assert env["PATH"] == __import__("os").environ["PATH"]


def test_ebusy_is_tried_again(cli, tmp_path):
    cli.busy = 2
    assert hf.run_cli(["lint", "--json"], cwd=tmp_path).returncode == 0
    assert len(cli.named("lint")) == 3


def test_node_older_than_22_is_said_plainly(monkeypatch, cli, tmp_path):
    monkeypatch.setattr(hf, "spawn", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "v20.18.0\n", ""))
    with pytest.raises(hf.HyperFramesError, match="Node 22 or newer"):
        hf.run_cli(["lint"], cwd=tmp_path)


def test_not_installed_says_how(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_STUDIO_HYPERFRAMES", str(tmp_path / "nothing"))
    monkeypatch.setattr(hf, "_roots", lambda: [tmp_path / "nothing"])
    with pytest.raises(hf.HyperFramesError, match="fetch_hyperframes"):
        hf.run_cli(["lint"], cwd=tmp_path)


# ---- catalog, guide ------------------------------------------------------------


def test_the_catalog_without_words_is_their_over_footage_blocks(cli):
    cli.catalog = [
        {"name": "lt-neon-border", "title": "Neon", "tags": ["lower-third", "glow"], "dimensions": {}, "duration": 4.8},
        {"name": "cosmic-orb", "title": "Orb", "tags": ["webgl", "background"], "dimensions": {}, "duration": 10},
    ]
    assert [b["name"] for b in hf.catalog()] == ["lt-neon-border"]
    assert [b["name"] for b in hf.catalog("orb")] == ["lt-neon-border", "cosmic-orb"]
    assert cli.named("catalog")[-1] == ["catalog", "--type", "block", "--json", "orb"]


def test_a_blocks_variables_come_from_what_their_add_installs(cli):
    assert hf.block_variables("lt-neon-border")[0]["id"] == "name"
    hf.block_variables("lt-neon-border")
    assert len(cli.named("add")) == 1  # remembered


def test_the_guide_is_drawn_from_their_docs(monkeypatch, tmp_path):
    root = tmp_path / "r"
    dist = hf.package_dir(root) / "dist"
    (dist / "skills/hyperframes/references").mkdir(parents=True)
    (dist / "docs").mkdir(parents=True)
    (dist / "skills/hyperframes/SKILL.md").write_text("# H\n\n## Contract\n\nTheir rule one.\n\n## Next\n\nx\n",
                                                      encoding="utf-8")
    (dist / "skills/hyperframes/references/overlays.md").write_text("## Pitfalls\n\nTransparent root.\n",
                                                                    encoding="utf-8")
    (dist / "docs/compositions.md").write_text("## Variables\n\ndata-composition-variables\n", encoding="utf-8")
    text = hf.guide(root)
    assert "Their rule one." in text and "Transparent root." in text and "data-composition-variables" in text
    assert "Next" not in text


# ---- making an animation -------------------------------------------------------


def test_a_block_is_added_by_their_cli_and_hosted_transparent(cli, tmp_path):
    made = hf.make({"block": "lt-neon-border", "variables": {"name": "Colin"}, "start": 2},
                   size=(1080, 1920), fps=30, out_dir=tmp_path / "anims")
    add = cli.named("add")[0]
    assert add[:2] == ["add", "lt-neon-border"] and json.loads(add[add.index("--vars") + 1]) == {"name": "Colin"}
    assert cli.named("init")[0][-2:] == ["--resolution", "portrait"]
    assert [c[0] for c in cli.calls if c[0] in ("lint", "check", "render")] == ["lint", "check", "render"]
    render = cli.named("render")[0]
    assert render[render.index("--format") + 1] == "mov" and render[render.index("--fps") + 1] == "30"
    assert made["start"] == 2 and made["duration"] == 4.8 and made["kind"] == "block"
    assert (tmp_path / "anims" / made["file"]).exists()


def test_the_block_root_is_transparent_and_fits_the_block(tmp_path):
    html = hf.block_root("lt-neon-border", {"width": 1920, "height": 1080}, (1080, 1920), 4.8, {"name": "O'Neil"})
    assert html.count("background: transparent") == 2
    assert "scale(0.562500)" in html and "top: 656px" in html
    assert "data-variable-values='{\"name\": \"O&#39;Neil\"}'" in html


def test_html_that_fails_their_lint_is_refused_in_their_words(cli, tmp_path):
    cli.lint = [LINT_BAD]
    with pytest.raises(hf.HyperFramesError, match="missing_timeline_registry"):
        hf.make({"html": "<html></html>"}, size=(1080, 1920), fps=30, out_dir=tmp_path)
    assert not cli.named("render")


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_a_request_goes_back_to_the_model_once_with_the_errors(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "guide", lambda root=None: "THEIR GUIDE")
    cli.lint = [LINT_BAD, LINT_OK]
    model = FakeModel(["```html\n<!doctype html><html>bad</html>\n```", "Here:\n" + GOOD_HTML])
    made = hf.make({"request": "a subscribe pop", "duration": 3}, size=(1080, 1920), fps=30,
                   out_dir=tmp_path, llm_factory=lambda: model)
    assert len(model.prompts) == 2
    assert "THEIR GUIDE" in model.prompts[0] and "a subscribe pop" in model.prompts[0]
    assert "missing_timeline_registry" in model.prompts[1] and "bad" in model.prompts[1]
    assert made["kind"] == "request"


def test_a_request_that_still_fails_is_a_plain_error(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "guide", lambda root=None: "g")
    cli.lint = [LINT_BAD]
    model = FakeModel(["<html>a</html>", "<html>b</html>"])
    with pytest.raises(hf.HyperFramesError, match="didn't pass HyperFrames' checks"):
        hf.make({"request": "x"}, size=(1080, 1920), fps=30, out_dir=tmp_path, llm_factory=lambda: model)
    assert len(model.prompts) == 2


def test_a_solid_background_is_refused(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "alpha_cover", lambda mov, at: 1.0)
    with pytest.raises(hf.HyperFramesError, match="solid background"):
        hf.make({"html": GOOD_HTML}, size=(1080, 1920), fps=30, out_dir=tmp_path)
    assert not list(tmp_path.glob("*.mov"))


def test_extract_html_finds_the_document():
    assert hf.extract_html("Sure!\n```html\n<!doctype html><html>x</html>\n```\nDone") == \
        "<!doctype html><html>x</html>"
    assert hf.extract_html("no html here") == ""


# ---- through the app's own providers -------------------------------------------


def test_a_request_on_openrouter_carries_the_attribution(cli, tmp_path, monkeypatch):
    """The model call goes through llm/providers/http.py, never a raw client."""
    pytest.importorskip("fastapi")
    from llm.providers import http, keys
    from server import animations_api

    monkeypatch.setattr(hf, "guide", lambda root=None: "g")
    keys.save_key(tmp_path, "openrouter", "sk-or-v1-" + "a1b2c3d4" * 8)
    sent = []

    class Response:
        status_code = 200
        headers: ClassVar[dict] = {}
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": GOOD_HTML}, "finish_reason": "stop"}]}

    def transport(method, url, **kw):
        sent.append((url, kw["headers"]))
        return Response()

    monkeypatch.setattr(http, "transport", transport)
    config = {"llm": {"backend": "ollama/gemma:7b", "data_dir": str(tmp_path)}}
    hf.make({"request": "a title"}, size=(1080, 1920), fps=30, out_dir=tmp_path / "a",
            llm_factory=lambda: animations_api.llm_for(config, "openrouter/google/gemma-4-26b-a4b-it"))
    assert sent and sent[0][0].endswith("/chat/completions")
    for header, value in ATTRIBUTION.items():
        assert sent[0][1].get(header) == value


def test_the_model_is_the_callers_else_the_apps():
    pytest.importorskip("fastapi")
    from server.animations_api import model_spec

    config = {"llm": {"backend": "openrouter/google/gemma-4-26b-a4b-it"}}
    assert model_spec(config, None) == "openrouter/google/gemma-4-26b-a4b-it"
    assert model_spec(config, "ollama/gemma4:e4b") == "ollama/gemma4:e4b"
    assert model_spec({"llm": {"backend": "gemma4:e4b"}}, None) == "ollama/gemma4:e4b"


def test_a_model_the_app_cant_use_is_a_plain_error():
    pytest.importorskip("fastapi")
    from server.animations_api import llm_for

    with pytest.raises(hf.HyperFramesError, match="can't be used"):
        llm_for({"llm": {"backend": "ollama/x"}}, "nosuchprovider/some-model")
    with pytest.raises(hf.HyperFramesError, match="can't be used"):
        llm_for({"llm": {}}, "ollama/")


def test_the_box_model_on_ollama_keeps_the_box_context_size():
    pytest.importorskip("fastapi")
    from server.animations_api import BOX_NUM_CTX, llm_for

    config = {"llm": {"backend": "ollama/gemma4:e4b", "num_ctx": 8192}}
    assert llm_for(config, "ollama/gemma4:e4b").num_ctx == BOX_NUM_CTX
    # Without a caller's model (an outside client), the app's own size stays.
    assert llm_for(config, None).num_ctx == 8192
    bigger = {"llm": {"backend": "ollama/gemma4:e4b", "num_ctx": 32768}}
    assert llm_for(bigger, "ollama/gemma4:e4b").num_ctx == 32768


def test_ollama_gets_room_for_a_whole_composition(monkeypatch):
    import requests

    from llm.ollama_backend import OllamaBackend

    sent = []

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": GOOD_HTML, "capabilities": ["completion"]}

    def post(url, json=None, timeout=None):
        sent.append((url, json))
        return R()

    monkeypatch.setattr(requests, "post", post)
    hf._ask(OllamaBackend("llama3.1:8b"), "p")
    body = [b for u, b in sent if u.endswith("/api/generate")][-1]
    assert body["options"]["num_predict"] == hf.OLLAMA_ANSWER_TOKENS


# ---- laying it on the clip -----------------------------------------------------


def test_the_overlay_graph_shifts_each_and_passes_at_the_end():
    graph = hf.overlay_graph([1.5, 4])
    assert "[1:v]setpts=PTS-STARTPTS+1.500/TB[a1]" in graph
    assert "[0:v][a1]overlay=0:0:eof_action=pass:format=auto[v1]" in graph
    assert "[v1][a2]overlay=0:0:eof_action=pass:format=auto[vout]" in graph


def test_a_name_that_is_not_a_file_in_the_folder_is_skipped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    (tmp_path / "outside.mov").write_bytes(b"x")
    folder = tmp_path / "animations"
    folder.mkdir()
    assert hf.apply_overlays(tmp_path / "clip.mp4", [{"file": "../outside.mov"}, {"file": "gone.mov"}], folder) == 0
    assert not calls


def test_a_clip_without_animations_is_never_touched():
    """The pipeline calls the overlay step only when the clip has some, so a
    run with none is byte for byte what it was (the argv snapshots hold the rest)."""
    source = (Path(__file__).resolve().parents[1] / "core" / "pipeline.py").read_text(encoding="utf-8")
    at = source.index("_hf.apply_overlays(")
    assert 'if opts.get("overlays"):' in source[at - 200:at]
    assert source.index("_wm.apply_image(render_path") < at < source.index("_outro.finish(render_path")


def _have_ffmpeg() -> bool:
    try:
        return subprocess.run([binaries.ffmpeg(), "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _have_ffmpeg(), reason="FFmpeg not available")
def test_the_overlay_shows_from_its_start_with_its_alpha(tmp_path):
    pytest.importorskip("numpy")
    ff = binaries.ffmpeg()
    clip = tmp_path / "clip.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3:r=30",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)], check=True)
    folder = tmp_path / "animations"
    folder.mkdir()
    # A red square, transparent around it, for one second.
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=320x240:d=1:r=30,format=rgba,"
                    "geq=r=255:g=0:b=0:a='if(lt(X,100)*lt(Y,100),255,0)'",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                    str(folder / "a.mov")], check=True)
    assert hf.apply_overlays(clip, [{"file": "a.mov", "start": 1}], folder) == 1

    def pixel(t, x, y):
        out = subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", str(clip), "-frames:v", "1",
                              "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(out[:3])

    assert pixel(0.5, 50, 50)[2] > 150          # before its start: the clip
    assert pixel(1.5, 50, 50)[0] > 150          # during: the red square
    assert pixel(1.5, 200, 200)[2] > 150        # around it: the clip shows through
    assert pixel(2.5, 50, 50)[2] > 150          # after its end: the clip again
    dur = subprocess.run([binaries.ffprobe(), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout
    assert abs(float(dur) - 3) < 0.2


# ---- the engine's routes and the "Ask Clips Kitty" box --------------------------


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    pytest.importorskip("httpx")
    pytest.importorskip("yt_dlp")
    import shutil as _shutil

    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    root = Path(__file__).resolve().parent.parent
    settings = tmp_path / "settings.yaml"
    _shutil.copy(root / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["llm"]["backend"] = "openrouter/google/gemma-4-26b-a4b-it"
    config["llm"]["data_dir"] = str(tmp_path / "data")
    return TestClient(create_app(config, settings), base_url="http://127.0.0.1")


def test_an_unknown_clip_has_no_animations(client):
    assert client.get("/clips/999/animations").status_code == 404
    assert client.post("/clips/999/animations", json={"block": "x"}).status_code == 404


def test_the_status_says_what_is_missing(client, monkeypatch):
    monkeypatch.setattr(hf, "find_node", lambda: ("node", 20))
    body = client.get("/animations/status").json()
    assert body["ready"] is False and "Node 22" in body["message"]


def test_the_box_writes_with_the_model_it_runs_on(client, monkeypatch, tmp_path):
    """add_animation from the box is written by the box's own model."""
    from llm.providers import keys
    from server import agent, agent_box, mcp

    # The box checks its model before a run: a key, and no model list fetch.
    keys.save_key(tmp_path / "data", "openrouter", "sk-or-v1-" + "c0ffee00" * 8)
    monkeypatch.setattr(agent_box.Box, "_quick_list", lambda self, spec: [])

    seen = {}
    tool = next(t for t in mcp.TOOLS if t["name"] == "add_animation")
    monkeypatch.setitem(tool, "handler", lambda args: seen.update(args) or "ok")

    def fake_run_cloud(message, history, tools, call_tool, backend):
        call_tool("add_animation", {"clip_id": 5, "request": "a title", "model": "something/else"})
        return {"reply": "done", "steps": [], "plan": None, "model": backend.name}

    monkeypatch.setattr(agent, "run_cloud", fake_run_cloud)
    assert client.post("/agent/chat", json={"message": "add a title"}).status_code == 200
    assert seen["model"] == "openrouter/google/gemma-4-26b-a4b-it"
