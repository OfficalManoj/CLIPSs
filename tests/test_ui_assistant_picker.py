"""The Ask Clips Kitty box's model picker in the window.

lib/agentModels.ts is run with Node (no imports, so no bundler); the
components are read as source, the way the other UI tests here do it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src"


def _run(tmp_path, module_name: str, body: str, data=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    module = tmp_path / module_name
    module.write_text((UI / "lib" / module_name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


GROUPS = [
    {"id": "ollama", "label": "Ollama (this PC)", "settings": False, "message": "", "models": [
        {"spec": "ollama/gemma4:e4b", "name": "gemma4:e4b", "tools": True, "note": ""},
        {"spec": "ollama/gemma3:4b", "name": "gemma3:4b", "tools": False, "note": ""}]},
    {"id": "openrouter", "label": "OpenRouter", "settings": False, "message": "", "models": [
        {"spec": "openrouter/google/gemma-4-26b-a4b-it", "name": "Google: Gemma 4 26B A4B", "tools": True,
         "note": ""}]},
    {"id": "openai", "label": "OpenAI", "settings": True, "message": "Add your key in Settings", "models": []},
    {"id": "chatgpt", "label": "ChatGPT plan", "settings": False,
     "message": "The box can't use this plan yet. Choose another model.", "models": []},
]


def test_the_sections_keep_the_engines_order_and_mark_tools(tmp_path):
    sections = _run(tmp_path, "agentModels.ts", "return m.pickerSections(data)", GROUPS)
    assert [s["label"] for s in sections] == ["Ollama (this PC)", "OpenRouter", "OpenAI", "ChatGPT plan"]
    local = sections[0]["options"]
    assert [(o["value"], o["badge"]) for o in local] == [
        ("ollama/gemma4:e4b", "Uses tools"), ("ollama/gemma3:4b", "No tools")]
    assert "can't use tools" in local[1]["tooltip"]


def test_a_provider_without_a_key_is_one_row_that_opens_settings(tmp_path):
    sections = _run(tmp_path, "agentModels.ts", "return m.pickerSections(data)", GROUPS)
    openai = sections[2]["options"]
    assert openai == [{"value": "settings:openai", "name": "", "badge": "Add your key in Settings",
                       "tooltip": "Opens Settings → AI, where you add your key or sign in.", "disabled": False}]
    # A plan that can't run the box is said, and can't be picked.
    assert sections[3]["options"][0]["disabled"] is True
    assert _run(tmp_path, "agentModels.ts", "return [m.settingsProvider('settings:openai'), "
                "m.settingsProvider('ollama/gemma4:e4b')]") == ["openai", ""]


def test_the_closed_picker_shows_the_models_own_name(tmp_path):
    got = _run(tmp_path, "agentModels.ts",
               "return [m.shortName('openrouter/google/gemma-4-26b-a4b-it', data), "
               "m.shortName('anthropic/claude-x', data)]", GROUPS)
    assert got == ["Google: Gemma 4 26B A4B", "claude-x"]


def test_the_box_sends_its_model_and_never_switches_the_apps():
    box = (UI / "components" / "Assistant.tsx").read_text(encoding="utf-8")
    picker = (UI / "components" / "AssistantModelPicker.tsx").read_text(encoding="utf-8")
    assert "api.agentChat(text, history, seedOptions(), model)" in box
    assert "api.chooseAgentModel(spec)" in box
    assert "<AssistantModelPicker" in box
    # Its picker is in both the ready box and the not-ready one, so a model
    # that can't run can be swapped from where the message is.
    assert box.count("{picker}") == 2
    for source in (box, picker):
        assert "activateModel" not in source and "activateAI" not in source
    assert "openAISetup(provider)" in picker and "openAISetup(settingsFor)" in box


def test_the_ui_strings_go_through_t_and_have_no_em_dashes():
    for name in ("Assistant.tsx", "AssistantModelPicker.tsx"):
        source = (UI / "components" / name).read_text(encoding="utf-8")
        for value in re.findall(r"""\b(?:placeholder|title|aria-label)\s*=\s*(['"])(.*?)\1""", source):
            raise AssertionError(f"{name}: hard-coded attribute {value[1]}")
        assert "—" not in re.sub(r"(?s)/\*.*?\*/|//[^\n]*", "", source), name
    lib = (UI / "lib" / "agentModels.ts").read_text(encoding="utf-8")
    assert "—" not in re.sub(r"(?s)/\*.*?\*/|//[^\n]*", "", lib)


def test_the_cloud_model_label_is_shared_by_the_sidebar_and_the_dashboard(tmp_path):
    status = {"active": {"provider": "openrouter", "model": "google/gemma-4-26b-a4b-it", "local": False},
              "providers": [{"id": "openrouter", "label": "OpenRouter"}], "signin": []}
    local = {**status, "active": {"provider": "ollama", "model": "gemma3:4b", "local": True}}
    got = _run(tmp_path, "activeModel.ts", "return [m.cloudModelLabel(data[0]), m.cloudModelLabel(data[1])]",
               [status, local])
    assert got == ["OpenRouter · google/gemma-4-26b-a4b-it", ""]
    for page in ("components/ModelSwitcher.tsx", "pages/Dashboard.tsx"):
        assert "cloudModelLabel" in (UI / page).read_text(encoding="utf-8"), page
    dashboard = (UI / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
    assert "{cloudModel || settings.model}" in dashboard
