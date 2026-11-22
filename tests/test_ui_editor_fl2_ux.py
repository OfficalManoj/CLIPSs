"""Editor suite FL2, the UX fix round, the app's part.

D1 (ui/src/main/editorFocus.ts): when Clips Kitty's window is left and comes
back, the keyboard goes back where it was: to the editor when it had it, or
to the app's own control. The rules run under Node, as
tests/test_ui_editor_fl2.py runs editorKeys.ts; the main process's wiring
(the window's own focus and blur, the page's press and focus) is checked in
the source, as tests/test_ui_editor_main.py does.

D3 (ui/src/main/editor.ts): an editor that never answered at its start is
remembered until it has gone, so the next start leaves its settings alone.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "ui" / "src" / "main"
RENDERER = ROOT / "ui" / "src" / "renderer" / "src"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _focus(tmp_path, body: str):
    """Run `body` (JavaScript: `m` is editorFocus.ts) and return its result."""
    module = MAIN / "editorFocus.ts"
    copy = tmp_path / module.name
    copy.write_text(module.read_text(encoding="utf-8"), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(copy.as_uri())});"
        f"console.log(JSON.stringify(await (async () => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---- D1: the rules -------------------------------------------------------------


def test_the_keys_go_back_to_the_editor_when_it_had_them(tmp_path):
    got = _focus(tmp_path, """
      const k = m.keysReturn()
      const out = {}
      k.left(true)                                   // Alt+Tab away, the editor had the keys
      out.whileAway = k.shown(false, true, 1000)     // the editor reports it lost them: not told
      out.back = k.back(2000)                        // the window activated again
      out.giving = k.givingBack(2000)
      out.pageFocus = k.appAsked('focus', 2050)      // the page's own focus on activation: ignored
      out.notYet = k.shown(false, true, 2100)        // not reported yet: the page is not told
      out.has = k.shown(true, true, 2300)            // the editor has them again
      out.done = k.givingBack(2300)
      out.focusLater = k.appAsked('focus', 2400)     // a later focus in the page counts again
      return out
    """)
    assert got == {"whileAway": True, "back": True, "giving": True, "pageFocus": False, "notYet": True,
                   "has": True, "done": False, "focusLater": True}


def test_the_keys_stay_on_the_apps_control_when_it_had_them(tmp_path):
    got = _focus(tmp_path, """
      const k = m.keysReturn()
      k.left(false)                                  // the sidebar or a field had the keys
      return { back: k.back(5000), giving: k.givingBack(5000), focus: k.appAsked('focus', 5010),
               shown: k.shown(false, false, 5020) }
    """)
    assert got == {"back": False, "giving": False, "focus": True, "shown": False}


def test_a_press_in_the_page_wins_over_a_give_back(tmp_path):
    got = _focus(tmp_path, """
      const k = m.keysReturn()
      k.left(true)
      k.back(100)
      return { press: k.appAsked('pointer', 150), giving: k.givingBack(160), shown: k.shown(false, true, 170) }
    """)
    assert got == {"press": True, "giving": False, "shown": False}


def test_a_give_back_that_never_lands_ends_and_the_page_is_told(tmp_path):
    got = _focus(tmp_path, """
      const k = m.keysReturn()
      k.left(true)
      k.back(0)
      const late = m.GIVE_BACK_MS + 10
      return { giving: k.givingBack(late), shown: k.shown(false, true, late), tries: m.GIVE_BACK_TRIES_MS,
               within: m.GIVE_BACK_TRIES_MS.every((t) => t < m.GIVE_BACK_MS) }
    """)
    assert got["giving"] is False and got["shown"] is False
    assert got["within"] is True and got["tries"][0] == 0


def test_hidden_or_gone_there_is_nothing_to_give_back(tmp_path):
    got = _focus(tmp_path, """
      const k = m.keysReturn()
      k.left(true)
      k.reset()                                      // the page hid the editor meanwhile
      return { back: k.back(10) }
    """)
    assert got == {"back": False}


# ---- D1: the wiring ------------------------------------------------------------


def test_only_the_windows_own_activation_gives_the_keys_back():
    source = (MAIN / "editor.ts").read_text(encoding="utf-8")
    assert "TEMPDEBUG" not in source and "console.log(" not in source
    watch = source[source.index("export function watchEditorWindow("):source.index("async function giveKeysBack(")]
    assert "win.on('blur', () => keys.left(pub.focused && wantVisible))" in watch
    assert "if (keys.back(Date.now())) void giveKeysBack(win)" in watch
    give = source[source.index("async function giveKeysBack("):source.index("export function setupEditor(")]
    # The same call a click in the editor amounts to, only while this window
    # is the active one and the editor is shown with a project.
    assert "rpc(target, 'host.focus')" in give
    for need in ("!win.isFocused()", "!wantVisible", "target.projectId === ''", "target.closing", "target.software"):
        assert need in give, need
    # A press in the page during a give back wins.
    assert "pagePresses !== presses" in give and "rpc(target, 'host.blur')" in give
    # While the window is in the background the page is not told the editor
    # let go (it would move its focus to "Projects").
    assert "focused: keys.shown(state.focused === true, pub.focused, Date.now())" in source
    blur = source[source.index("ipcMain.handle('editor:blur'"):source.index("ipcMain.handle('editor:close'")]
    assert "keys.appAsked(kind, Date.now())" in blur and "pagePresses++" in blur
    # Hidden: nothing to give back.
    assert "if (!show) keys.reset()" in source
    focus = (MAIN / "editorFocus.ts").read_text(encoding="utf-8")
    assert not re.findall(r"^import ", focus, re.M)            # plain rules, no Electron


def test_the_page_tells_a_press_from_a_focus_and_ctrl_f6_goes_both_ways():
    app = (RENDERER / "App.tsx").read_text(encoding="utf-8")
    assert "noteAppPointer('pointer')" in app and "noteAppPointer('focus')" in app
    lib = (RENDERER / "lib" / "editor.ts").read_text(encoding="utf-8")
    assert "window.studio?.editor?.blur(why)" in lib
    preload = (ROOT / "ui" / "src" / "preload" / "index.ts").read_text(encoding="utf-8")
    assert "ipcRenderer.invoke('editor:blur', why)" in preload
    page = (RENDERER / "pages" / "Editor.tsx").read_text(encoding="utf-8")
    # Ctrl+F6 in the page gives the editor the keys again (on F6's release).
    assert "if (e.key !== 'F6' || !pressed) return" in page and "void bridge?.focus()" in page
    # The K2 path stays: the editor letting go without a click in the app
    # (Ctrl+F6 in the editor) puts the focus on "Projects".
    assert "projectsButton.current?.focus()" in page


# ---- D3 -------------------------------------------------------------------------


def test_an_editor_that_never_answered_is_remembered_until_it_has_gone():
    source = (Path(MAIN / "editor.ts")).read_text(encoding="utf-8")
    start = source[source.index("async function start("):source.index("function onExit(")]
    never = start[start.index("if (!state || !(state.hostApi >= 2))"):start.index("target.ready = true")]
    assert "ed = null" in never and "if (alive(target.pid)) lingering = target.pid" in never
    assert never.index("ed = null") < never.index("lingering = target.pid")
