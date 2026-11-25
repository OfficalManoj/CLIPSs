"""Ask Clips Kitty on every page that has it, with one conversation.

lib/assistantChat.ts is run with Node (type imports only, so no bundler);
the pages are read as source, the way the other UI tests here do it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src"


def _run(tmp_path, body: str, data=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    module = tmp_path / "assistantChat.ts"
    module.write_text((UI / "lib" / "assistantChat.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_markdown_from_the_model_shows_as_plain_words(tmp_path):
    # What the app test saw in the box on gemma4:e4b.
    replies = [
        "You have **15** clips.",
        "*   **[7]** Beta Hot Take GG",
        "- one\n+ two\n## Your clips",
        "You can check the status using `job_status(job_id=5)`.",
        "a *really* good one, 2 * 3 = 6",
        "__done__",
    ]
    out = _run(tmp_path, "return data.map(m.plainReply)", replies)
    assert out == [
        "You have 15 clips.",
        "• [7] Beta Hot Take GG",
        "• one\n• two\nYour clips",
        "You can check the status using job_status(job_id=5).",
        "a really good one, 2 * 3 = 6",
        "done",
    ]


def test_one_conversation_for_every_box(tmp_path):
    body = """
    const seen = [];
    const stop = m.subscribeChat(() => seen.push(m.chatState().turns.length));
    m.updateChat((s) => ({ turns: [...s.turns, { role: 'user', text: 'hi' }], busy: true }));
    m.updateChat({ busy: false });
    stop();
    m.updateChat((s) => ({ turns: [...s.turns, { role: 'assistant', text: 'hello' }] }));
    const kept = m.chatState();
    m.resetChat();
    return { seen, turns: kept.turns.map((t) => t.text), busy: kept.busy, after: m.chatState().turns.length };
    """
    out = _run(tmp_path, body)
    assert out == {"seen": [1, 1], "turns": ["hi", "hello"], "busy": False, "after": 0}


def test_the_box_keeps_its_conversation_in_the_shared_store():
    src = (UI / "components" / "Assistant.tsx").read_text(encoding="utf-8")
    assert "useSyncExternalStore(subscribeChat, chatState)" in src
    # No page-local copy of the conversation is left behind.
    assert not re.search(r"useState<\s*Turn", src) and "setTurns" not in src
    assert "plainReply(res.reply)" in src
    assert "api.agentChat(text, history, seedOptions(), model, where)" in src


def test_the_box_is_on_the_dashboard_and_the_clip_editor():
    dashboard = (UI / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
    studio = (UI / "pages" / "ClipStudio.tsx").read_text(encoding="utf-8")
    assert '<Assistant where="the Dashboard" />' in dashboard
    assert "import Assistant from '../components/Assistant'" in studio
    assert "<Assistant\n        where={boxWhere}" in studio
    # On the clip list, in the full editor, and with no videos yet.
    assert studio.count("{askBox}") == 2 and "videos.length === 0 && askBox" in studio
    assert "with clip ${openClip.id}" in studio
