"""The Ask Clips Kitty box's own model picker (server/agent_box.py).

Ollama first, then OpenRouter, then the direct providers and plan sign-ins;
each model marked with whether it can use tools; the box's choice kept apart
from the model that picks clips. No network: Ollama and the providers are faked.
"""

import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI

from core.state import StateDB
from llm.providers import http, keys
from llm.signin import catalog as signin
from server import agent_box, ai_api

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-or-v1-" + "c0ffee00" * 8

OPENROUTER_MODELS = {"data": [
    {"id": "google/gemma-4-26b-a4b-it", "name": "Google: Gemma 4 26B A4B",
     "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["response_format", "structured_outputs", "tools"]},
    {"id": "some/no-tools", "name": "Some: No Tools",
     "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["response_format", "structured_outputs"]},
]}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = {}
        self.text = str(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeOllama:
    """/api/tags and /api/show, from a dict of name -> can call tools."""

    def __init__(self, models, up=True):
        self.models = models
        self.up = up
        self.shown = []

    def get(self, url, timeout=None):
        if not self.up:
            raise ConnectionError("refused")
        return Resp(200, {"models": [{"name": n, "modified_at": "2026-11-01"} for n in self.models]})

    def post(self, url, json=None, timeout=None):
        if not self.up:
            raise ConnectionError("refused")
        self.shown.append(json["model"])
        tools = self.models.get(json["model"])
        if tools is None:
            return Resp(404, {})
        return Resp(200, {"capabilities": ["completion", "tools"] if tools else ["completion"]})


class Env:
    def __init__(self, tmp_path, monkeypatch, ollama, backend="ollama/gemma3:4b"):
        self.data = tmp_path / "data"
        self.data.mkdir()
        settings = tmp_path / "settings.yaml"
        settings.write_text("model: gemma3:4b\n", encoding="utf-8")
        self.config = {"llm": {"backend": backend, "data_dir": str(self.data),
                               "ollama_host": "http://127.0.0.1:1"}}
        self.provider_calls = []
        monkeypatch.setattr(http, "transport", self.transport)
        monkeypatch.setattr(http, "sleep", lambda s: None)
        monkeypatch.setitem(sys.modules, "requests", ollama)
        monkeypatch.setattr(signin, "all_for", lambda data_dir: [])
        self.db = lambda: StateDB(tmp_path / "state.db")
        app = FastAPI()
        ai_api.install(app, config=self.config, db=self.db, data_dir=self.data, settings_path=settings)
        self.box = agent_box.Box(config=self.config, db=self.db, data_dir=self.data,
                                 ollama_host="http://127.0.0.1:1",
                                 list_models=app.state.ai_text_models, vram_gb=lambda: 8.0)

    def transport(self, method, url, **kw):
        self.provider_calls.append(url)
        if url.endswith("/models/user"):
            return Resp(200, OPENROUTER_MODELS)
        raise AssertionError(url)


def _specs(groups):
    return [m["spec"] for g in groups for m in g["models"]]


# ---- the list -------------------------------------------------------------------


def test_ollama_then_openrouter_then_the_direct_providers(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False, "gemma4:e4b": True}))
    keys.save_key(env.data, "openrouter", KEY)
    groups = env.box.groups()
    ids = [g["id"] for g in groups]
    assert ids[:3] == ["ollama", "openrouter", "openai"]
    assert {"anthropic", "gemini", "xai", "meta", "deepseek", "qwen"} <= set(ids)
    # Gemma 4 first among the local ones, each marked by what Ollama said.
    assert groups[0]["models"] == [
        {"spec": "ollama/gemma4:e4b", "name": "gemma4:e4b", "tools": True, "note": ""},
        {"spec": "ollama/gemma3:4b", "name": "gemma3:4b", "tools": False, "note": ""},
    ]
    # OpenRouter's own pick first, and its tool support from supported_parameters.
    router = groups[1]["models"]
    assert router[0]["spec"] == "openrouter/google/gemma-4-26b-a4b-it" and router[0]["tools"] is True
    assert {"spec": "openrouter/some/no-tools", "tools": False} == {
        k: router[1][k] for k in ("spec", "tools")}


def test_a_provider_without_a_key_says_add_your_key(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))
    groups = {g["id"]: g for g in env.box.groups()}
    for pid in ("openrouter", "openai", "anthropic", "gemini"):
        assert groups[pid]["models"] == []
        assert groups[pid]["message"] == "Add your key in Settings"
        assert groups[pid]["settings"] is True
    assert env.provider_calls == [], "nothing is asked of a provider with no key"


def test_ollama_not_running_is_said_plainly(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}, up=False))
    local = env.box.groups()[0]
    assert local["models"] == [] and local["message"] == agent_box.OLLAMA_DOWN


def test_a_plan_sign_in_is_listed_and_says_it_cannot_drive_the_box(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))

    class Plan:
        id, label = "chatgpt", "ChatGPT plan"

        def available(self):
            return True

        def status(self):
            return {"signed_in": True}

    monkeypatch.setattr(signin, "all_for", lambda data_dir: [Plan()])
    last = env.box.groups()[-1]
    assert last["id"] == "chatgpt" and last["message"] == agent_box.PLAN_NO_TOOLS


def test_ollama_is_asked_once_per_model(tmp_path, monkeypatch):
    fake = FakeOllama({"gemma4:e4b": True})
    env = Env(tmp_path, monkeypatch, fake)
    env.box.groups()
    env.box.groups()
    assert fake.shown == ["gemma4:e4b"]


# ---- the default and the choice ------------------------------------------------


def test_the_default_starts_with_the_apps_model_when_it_can_use_tools(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma4:e2b": True, "gemma4:e4b": True}),
              backend="ollama/gemma4:e4b")
    assert env.box.listing()["default"] == "ollama/gemma4:e4b"


def test_the_default_skips_a_model_that_cannot_use_tools(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False, "gemma4:e2b": True}))
    keys.save_key(env.data, "openrouter", KEY)
    # The app's gemma3:4b can't: the first model on this PC that can is next.
    assert env.box.listing()["default"] == "ollama/gemma4:e2b"


def test_the_default_never_switches_to_a_paid_model_by_itself(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False}))
    keys.save_key(env.data, "openrouter", KEY)
    # Nothing on this PC can use tools. OpenRouter is in the list for the
    # person to choose, but the box stays on the app's model and says why.
    assert env.box.listing()["default"] == "ollama/gemma3:4b"
    status = env.box.check()
    assert status["ready"] is False and "Gemma 4" in status["reason"]
    assert status["install"] is not None


def test_a_remembered_choice_is_kept_while_ollama_is_not_answering(tmp_path, monkeypatch):
    fake = FakeOllama({"gemma4:e4b": True})
    env = Env(tmp_path, monkeypatch, fake)
    keys.save_key(env.data, "openrouter", KEY)
    assert env.box.choose("ollama/gemma4:e4b") == "ollama/gemma4:e4b"
    fake.up = False
    assert env.box.listing()["default"] == "ollama/gemma4:e4b"
    status = env.box.check()
    assert status["ready"] is False
    assert status["reason"] == agent_box.OLLAMA_NOT_ANSWERING
    assert "can't use tools" not in status["reason"]


def test_the_box_remembers_its_own_choice_and_leaves_the_apps_model_alone(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma4:e4b": True}))
    keys.save_key(env.data, "openrouter", KEY)
    assert env.box.choose("openrouter/google/gemma-4-26b-a4b-it") == "openrouter/google/gemma-4-26b-a4b-it"
    listing = env.box.listing()
    assert listing["chosen"] == listing["default"] == "openrouter/google/gemma-4-26b-a4b-it"
    assert listing["active"] == "ollama/gemma3:4b"
    assert env.config["llm"]["backend"] == "ollama/gemma3:4b"
    # A run on it uses the app's settings with only the model swapped, unsaved.
    run = env.box.backend_config("openrouter/google/gemma-4-26b-a4b-it")
    assert run["backend"] == "openrouter/google/gemma-4-26b-a4b-it" and run["data_dir"] == str(env.data)
    assert env.config["llm"]["backend"] == "ollama/gemma3:4b"
    # "" follows the app's model again.
    assert env.box.choose("") == "" and env.box.listing()["default"] == "ollama/gemma4:e4b"


def test_a_bare_local_name_is_one_spelling():
    assert agent_box.full_spec("gemma:7b") == "ollama/gemma:7b"
    assert agent_box.full_spec("openrouter/meta/muse-spark-1.3") == "openrouter/meta/muse-spark-1.3"


# ---- before a run ----------------------------------------------------------------


def test_following_the_app_model_falls_back_to_one_that_can(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False, "gemma4:e4b": True}))
    checked = env.box.check()
    assert checked["ready"] is True and checked["model"] == "ollama/gemma4:e4b"


def test_a_chosen_local_model_without_tools_is_named_with_one_to_choose(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False, "gemma4:e2b": True}))
    checked = env.box.check("ollama/gemma3:4b")
    assert checked["ready"] is False
    assert checked["reason"] == ("gemma3:4b can't use tools, which this box needs. Choose a model marked "
                                 "\"Uses tools\" in the list, such as gemma4:e2b.")
    assert checked["install"] is None


def test_the_gemma_4_offer_stays_when_nothing_local_can(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({"gemma3:4b": False}))
    checked = env.box.check()
    assert checked["ready"] is False and "Install Gemma 4" in checked["reason"]
    assert checked["install"] == {"model": "gemma4:e4b", "size_gb": 7}
    assert env.box.check("ollama/gemma3:4b")["install"] == {"model": "gemma4:e4b", "size_gb": 7}


def test_an_openrouter_model_without_tools_is_named(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))
    keys.save_key(env.data, "openrouter", KEY)
    checked = env.box.check("openrouter/some/no-tools")
    assert checked["ready"] is False
    assert checked["reason"] == ("Some: No Tools can't use tools on OpenRouter, which this box needs. "
                                 "Choose a model marked \"Uses tools\" in the list, such as "
                                 "Google: Gemma 4 26B A4B.")
    assert env.box.check("openrouter/google/gemma-4-26b-a4b-it")["ready"] is True


def test_a_provider_without_a_key_points_at_settings(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))
    checked = env.box.check("anthropic/claude-sonnet-4-5")
    assert checked["ready"] is False and checked["settings"] == "anthropic"
    assert "Settings → AI" in checked["reason"]


def test_the_chatgpt_plan_is_not_run(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))
    checked = env.box.check("chatgpt/gpt-5")
    assert checked["ready"] is False and "ChatGPT plan" in checked["reason"]


# ---- the routes, on the real app --------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    pytest.importorskip("yt_dlp")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["llm"]["ollama_host"] = "http://127.0.0.1:1"
    app = create_app(config, settings)
    monkeypatch.setitem(sys.modules, "requests", FakeOllama({"gemma3:4b": False, "gemma4:e4b": True}))
    monkeypatch.setattr(signin, "all_for", lambda data_dir: [])
    return TestClient(app, base_url="http://127.0.0.1"), config


def test_the_routes_choose_the_box_model_only(client):
    http_client, config = client
    before = config["llm"]["backend"]
    listing = http_client.get("/agent/models").json()
    assert listing["groups"][0]["id"] == "ollama"
    assert listing["default"] == "ollama/gemma4:e4b"

    chosen = http_client.put("/agent/model", json={"model": "ollama/gemma3:4b"}).json()
    assert chosen["chosen"] == "ollama/gemma3:4b" and chosen["ready"] is False
    assert config["llm"]["backend"] == before

    status = http_client.get("/agent/status", params={"model": "ollama/gemma4:e4b"}).json()
    assert status["ready"] is True and status["model"] == "ollama/gemma4:e4b"

    # The chat refuses before running when the box's model can't use tools.
    refused = http_client.post("/agent/chat", json={"message": "list my videos"})
    assert refused.status_code == 400 and "can't use tools" in refused.json()["detail"]
    assert http_client.put("/agent/model", json={"model": "openrouter/"}).status_code == 400


def test_the_chat_tells_the_model_where_the_box_is(client, monkeypatch):
    # The box sits on the Clip Editor too; it says which clip is open there.
    from server import agent

    http_client, _ = client
    seen = {}

    def fake_run(message, history, tools, call_tool, host, model, num_ctx=16384, context=""):
        seen["context"] = context
        return {"reply": "ok", "steps": [], "plan": None, "model": model}

    monkeypatch.setattr(agent, "run", fake_run)
    r = http_client.post("/agent/chat", json={"message": "add a title to this clip", "model": "ollama/gemma4:e4b",
                                              "context": "the Clip Editor, with clip 7 open"})
    assert r.status_code == 200, r.text
    assert seen["context"] == "the Clip Editor, with clip 7 open"


# ---- speed: the box's own Ollama calls (app test minor 4) --------------------------


def test_the_box_calls_ollama_on_127_0_0_1_not_localhost():
    # "localhost" took 2 s a call on Windows (IPv6 first); 127.0.0.1 took 0.01 s.
    assert agent_box.loopback("http://localhost:11434") == "http://127.0.0.1:11434"
    assert agent_box.loopback("http://LOCALHOST:11434/") == "http://127.0.0.1:11434"
    assert agent_box.loopback("http://localhost") == "http://127.0.0.1"
    # Anything else is left exactly as set.
    assert agent_box.loopback("http://192.168.1.5:11434") == "http://192.168.1.5:11434"
    assert agent_box.loopback("http://my-localhost.lan:11434") == "http://my-localhost.lan:11434"


def test_the_box_host_is_the_loopback_but_the_pipeline_setting_is_untouched(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, FakeOllama({}))
    env.config["llm"]["ollama_host"] = "http://localhost:11434"
    box = agent_box.Box(config=env.config, db=env.db, data_dir=env.data, ollama_host="http://localhost:11434",
                        list_models=lambda provider_id: [])
    assert box.ollama_host == "http://127.0.0.1:11434"
    assert env.config["llm"]["ollama_host"] == "http://localhost:11434"


def test_a_chosen_model_is_checked_from_what_the_list_already_asked(tmp_path, monkeypatch):
    fake = FakeOllama({"gemma3:4b": False, "gemma4:e4b": True})
    env = Env(tmp_path, monkeypatch, fake)
    env.box.groups()
    asked = list(fake.shown)
    env.box.choose("ollama/gemma4:e4b")
    assert env.box.check()["ready"] is True
    # Already asked by the list as it was shown: not again.
    assert fake.shown == asked
    env.box.choose("ollama/gemma3:4b")
    assert env.box.check()["ready"] is False


def test_a_model_the_list_never_asked_about_is_asked_once(tmp_path, monkeypatch):
    fake = FakeOllama({"gemma4:e4b": True})
    env = Env(tmp_path, monkeypatch, fake)
    env.box.choose("ollama/gemma4:e4b")
    assert env.box.check()["ready"] is True
    assert env.box.check()["ready"] is True
    assert fake.shown == ["gemma4:e4b"]


# ---- the tools, through the chat route (app test minors 8 and 10) ----------------------


def _chat_calling(monkeypatch, http_client, name, arguments, defaults=None):
    """Runs /agent/chat with a model that makes one tool call; returns its text."""
    from server import agent

    out = {}

    def fake_run(message, history, tools, call_tool, host, model, num_ctx=16384, context=""):
        out["text"], _ = call_tool(name, dict(arguments))
        out["host"] = host
        return {"reply": "ok", "steps": [], "plan": None, "model": model}

    monkeypatch.setattr(agent, "run", fake_run)
    r = http_client.post("/agent/chat", json={"message": "go", "model": "ollama/gemma4:e4b",
                                              **({"defaults": defaults} if defaults else {})})
    assert r.status_code == 200, r.text
    return out


def test_a_file_on_this_pc_follows_the_generate_bars_switches(client, monkeypatch):
    from server import mcp

    http_client, _ = client
    seen = {}
    tool = next(t for t in mcp.TOOLS if t["name"] == "queue_local_file")
    monkeypatch.setitem(tool, "handler", lambda args: seen.update(args) or "Queued as job 5.")
    _chat_calling(monkeypatch, http_client, "queue_local_file", {"path": "C:/v.mp4", "captions": False},
                  defaults={"captions": True, "long_clips": True, "gaming": True,
                            "publish_when_done": ["youtube"]})
    # The bar fills in what the model didn't say; what it said still wins,
    # and posting is never taken from the bar.
    assert seen["long_clips"] is True and seen["gaming"] is True
    assert seen["captions"] is False
    assert "publish_when_done" not in seen


@pytest.mark.parametrize("detail,wanted", [
    ("YouTube publishing is not enabled", "Connect YouTube in Settings to post from here."),
    ("WoopSocial publishing is not enabled", "Add your WoopSocial key in Settings to post from here."),
    ("that time is in the past", "That was refused: that time is in the past"),
])
def test_a_posting_refusal_reaches_the_model_as_the_next_step(client, monkeypatch, detail, wanted):
    import io
    import json as _json
    import urllib.error

    from server import mcp

    http_client, _ = client

    def refuse(_args):
        raise urllib.error.HTTPError("http://x", 404, "Not Found", {},
                                     io.BytesIO(_json.dumps({"detail": detail}).encode()))

    tool = next(t for t in mcp.TOOLS if t["name"] == "publish_plan")
    monkeypatch.setitem(tool, "handler", refuse)
    out = _chat_calling(monkeypatch, http_client, "publish_plan", {"clip_ids": [1]})
    assert out["text"] == wanted
    assert "not enabled" not in out["text"]
    # The box's own chat goes to Ollama on 127.0.0.1 too.
    assert out["host"].startswith("http://127.0.0.1")
