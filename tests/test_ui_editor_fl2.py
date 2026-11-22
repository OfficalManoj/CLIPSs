"""Editor suite FL2, the app's part.

C1, Ctrl shortcuts (ui/src/main/editorKeys.ts): the table written into Clips
Kitty Editor's own settings before it starts as the editor's defaults
("hostKeybinds"), without touching anything else; since the UX fix round
(D2) no table chord in "keybinds", and what the first writer put there taken
out again, never a chord the person chose in Settings > Shortcuts. The module runs under Node
here, as tests/test_ui_steps.py runs lib/steps.ts, against real files in a
temporary folder.

F3 and F4 (ui/src/renderer/src/lib/editorOpening.ts): the opening bar starts
again when a failed build is made again, and the Export sheet's line once
the clip is deleted. Also run under Node.

F1 and F4 in the main process (ui/src/main/editor.ts): the editor started
while a project is made, and the sheet's line told again; source checks, as
tests/test_ui_editor_main.py does.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "ui" / "src" / "main"
LIB = ROOT / "ui" / "src" / "renderer" / "src" / "lib"

# Clips Kitty Editor's keyboard table as Concat v0.2.12 ships it
# (src/crates/concat/src/keymap.rs ACTIONS: id and default chords), and the
# chords keymap::reserved keeps for the window on Windows.
EDITOR_ACTIONS = {
    "save": ["ctrl+s"], "import": ["ctrl+i"], "export": ["ctrl+e"], "settings": ["ctrl+,"],
    "undo": ["ctrl+z"], "redo": ["ctrl+shift+z"], "select-all": ["ctrl+a"], "copy": ["ctrl+c"],
    "paste": ["ctrl+v"], "duplicate": ["ctrl+d"], "split": ["ctrl+b"], "delete": ["delete", "backspace"],
    "ripple-delete": ["shift+delete", "shift+backspace"], "freeze": ["f"], "delete-left": ["q"],
    "delete-right": ["w"], "split-tool": ["b"], "mute": ["m"], "pan": ["h"], "snap": ["n"],
    "preview-axis": ["s"], "zoom-in": ["ctrl+=", "ctrl+plus"], "zoom-out": ["ctrl+-"], "play": ["space"],
    "start": ["home"], "end": ["end"], "step-back": ["left"], "step-forward": ["right"], "prev-edit": ["up"],
    "next-edit": ["down"],
}
RESERVED = {"ctrl+o", "ctrl+w", "ctrl+q", "f10", "alt+f", "alt+e", "alt+v", "ctrl+y"}
# What stays as the editor ships it (the FL2 spec): Space, the arrows,
# Home/End and Delete are not letters.
NOT_LETTERS = {"space", "left", "right", "up", "down", "home", "end", "delete"}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, module: Path, body: str, data=None):
    """Run `body` (JavaScript: `m` is the module, `data` the JSON given) and
    return what it returns, as JSON."""
    copy = tmp_path / module.name
    copy.write_text(module.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(copy.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify(await (async () => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _keys(tmp_path, body: str, data=None):
    return _run(tmp_path, MAIN / "editorKeys.ts", body, data)


# ---- C1: the table -----------------------------------------------------------


def test_every_letter_shortcut_gets_a_free_ctrl_chord(tmp_path):
    table = _keys(tmp_path, "return { table: m.KITTY_KEYS, version: m.KEYS_TABLE_VERSION }")
    keys = table["table"]
    assert table["version"] >= 1
    # Every action the editor ships on a bare letter or other bare character
    # (anything but the named keys that stay) is in the table...
    bare = {a for a, chords in EDITOR_ACTIONS.items()
            if any("+" not in c and c not in NOT_LETTERS for c in chords)}
    assert bare == {"freeze", "delete-left", "delete-right", "split-tool", "mute", "pan", "snap", "preview-axis",
                    "delete"}
    assert bare <= set(keys) and set(keys) <= set(EDITOR_ACTIONS)
    assert keys == {"freeze": "ctrl+f", "delete-left": "ctrl+shift+q", "delete-right": "ctrl+shift+w",
                    "split-tool": "ctrl+shift+b", "mute": "ctrl+m", "pan": "ctrl+h", "snap": "ctrl+n",
                    "preview-axis": "ctrl+shift+s", "delete": "delete", "ripple-delete": "shift+delete"}
    # ...on a chord no other action ships on, the window keeps, or another
    # entry takes; and none of them is a bare letter or Backspace.
    others = {c for a, chords in EDITOR_ACTIONS.items() if a not in keys for c in chords}
    chosen = list(keys.values())
    assert len(set(chosen)) == len(chosen)
    for action, chord in keys.items():
        assert chord not in others and chord not in RESERVED, (action, chord)
        assert "backspace" not in chord
        assert chord.startswith("ctrl+") or chord in ("delete", "shift+delete"), (action, chord)
        # Lower-case tokens, modifiers first in keymap::chord's order.
        tokens = chord.split("+")
        assert tokens[:-1] == [t for t in ("ctrl", "alt", "shift") if t in tokens[:-1]] and chord == chord.lower()


def test_the_settings_file_is_found_where_the_editor_keeps_it(tmp_path):
    exe_dir = tmp_path / "editor"
    exe_dir.mkdir()
    got = _keys(tmp_path, "return [m.editorSettingsPath(data.exe, {APPDATA: data.appdata}),"
                          " m.editorSettingsPath(data.exe, {})]",
                {"exe": str(exe_dir / "clips-kitty-editor.exe"), "appdata": str(tmp_path / "Roaming")})
    assert Path(got[0]) == tmp_path / "Roaming" / "com.clipskitty.editor" / "settings.json"
    assert got[1] is None
    (exe_dir / "portable").mkdir()       # a portable build keeps everything beside itself
    got = _keys(tmp_path, "return m.editorSettingsPath(data.exe, {APPDATA: data.appdata})",
                {"exe": str(exe_dir / "clips-kitty-editor.exe"), "appdata": str(tmp_path / "Roaming")})
    assert Path(got) == exe_dir / "portable" / "settings.json"


# ---- C1: written into the editor's settings ----------------------------------

SETTINGS = {
    "dark": None, "magnetic": True, "previewAxis": True,
    "server": {"enabled": False, "listen": "127.0.0.1:7420", "token": ""},
    "layout": {"wide": {"split": {"columns": True, "ratio": 0.81672984}}},
    "voiceover": {"gainDb": 0.5, "promptSpeed": 1},
    "futureKey": [1, 2, {"x": "y"}],
}


def _apply(tmp_path, settings_path: Path, record_path: Path) -> str:
    return _keys(tmp_path, "return m.applyShortcutDefaults(data.s, data.r)",
                 {"s": str(settings_path), "r": str(record_path)})


TABLE = {"freeze": "ctrl+f", "delete-left": "ctrl+shift+q", "delete-right": "ctrl+shift+w",
         "split-tool": "ctrl+shift+b", "mute": "ctrl+m", "pan": "ctrl+h", "snap": "ctrl+n",
         "preview-axis": "ctrl+shift+s", "delete": "delete", "ripple-delete": "shift+delete"}


def _record_key(settings: Path) -> str:
    return str(settings).replace("/", "\\").lower()


def _record(record: Path, settings: Path) -> dict:
    return json.loads(record.read_text(encoding="utf-8"))[_record_key(settings)]


def test_a_fresh_editor_gets_the_table_as_its_defaults_and_no_changed_shortcuts(tmp_path):
    """D2: the table is the editor's defaults ("hostKeybinds"); "keybinds",
    what Settings > Shortcuts shows as changed, gets none of it."""
    settings = tmp_path / "Roaming" / "com.clipskitty.editor" / "settings.json"
    record = tmp_path / "kitty" / "editor-shortcuts.json"
    assert _apply(tmp_path, settings, record) == "written"
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written == {"hostKeybinds": TABLE}
    assert _record(record, settings) == {"table": 2, "wrote": {}}
    # No staging file is left beside either.
    assert sorted(p.name for p in settings.parent.iterdir()) == ["settings.json"]
    assert sorted(p.name for p in record.parent.iterdir()) == ["editor-shortcuts.json"]
    before = settings.read_bytes()
    assert _apply(tmp_path, settings, record) == "current"
    assert settings.read_bytes() == before


def test_other_settings_and_the_persons_chords_are_left_as_they_are(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({**SETTINGS, "keybinds": {"undo": "ctrl+u", "mute": ""}}, indent=2),
                        encoding="utf-8")
    record = tmp_path / "record.json"
    assert _apply(tmp_path, settings, record) == "written"
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert {k: v for k, v in written.items() if k not in ("keybinds", "hostKeybinds")} == SETTINGS
    # The person's own choices before Clips Kitty ever ran stay: undo moved,
    # mute taken off its key.
    assert written["keybinds"] == {"undo": "ctrl+u", "mute": ""}
    assert written["hostKeybinds"] == TABLE


def test_what_the_first_fl2_writer_left_is_cleaned_and_the_persons_chord_kept(tmp_path):
    """A file as table 1 left it (keybinds = the whole table, remembered as
    written), after the person moved freeze and cleared pan: the table's
    entries go, theirs stay; once, then the file is current."""
    settings = tmp_path / "settings.json"
    record = tmp_path / "record.json"
    binds = {**TABLE, "freeze": "ctrl+shift+f", "pan": "", "undo": "ctrl+u"}
    settings.write_text(json.dumps({**SETTINGS, "keybinds": binds, "hostKeybinds": TABLE}), encoding="utf-8")
    record.write_text(json.dumps({_record_key(settings): {"table": 1, "wrote": TABLE}}), encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "written"
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["keybinds"] == {"freeze": "ctrl+shift+f", "pan": "", "undo": "ctrl+u"}
    assert written["hostKeybinds"] == TABLE
    assert {k: v for k, v in written.items() if k not in ("keybinds", "hostKeybinds")} == SETTINGS
    assert _record(record, settings) == {"table": 2, "wrote": {}}
    before = settings.read_bytes()
    assert _apply(tmp_path, settings, record) == "current"
    assert settings.read_bytes() == before


def test_an_entry_still_what_an_older_table_wrote_is_taken_out(tmp_path):
    """An action whose chord changed between tables: the entry still holding
    what Clips Kitty wrote before (never touched by the person) goes too."""
    settings = tmp_path / "settings.json"
    record = tmp_path / "record.json"
    settings.write_text(json.dumps({"keybinds": {"snap": "ctrl+shift+n", "mute": "ctrl+shift+m"}}), encoding="utf-8")
    record.write_text(json.dumps({_record_key(settings): {"table": 1, "wrote": {"snap": "ctrl+shift+n"}}}),
                      encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "written"
    assert json.loads(settings.read_text(encoding="utf-8"))["keybinds"] == {"mute": "ctrl+shift+m"}


def test_a_table_chord_set_again_is_the_default_and_goes(tmp_path):
    """Every start: an entry equal to its default (an action set back on its
    table chord, or written again by an older Clips Kitty) is taken out; it
    changes nothing the person can press."""
    settings = tmp_path / "settings.json"
    record = tmp_path / "record.json"
    assert _apply(tmp_path, settings, record) == "written"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["keybinds"] = {"snap": "ctrl+n", "freeze": "ctrl+g"}
    settings.write_text(json.dumps(data), encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "written"
    assert json.loads(settings.read_text(encoding="utf-8"))["keybinds"] == {"freeze": "ctrl+g"}


def test_the_hosts_defaults_are_kept_as_the_table(tmp_path):
    """X1: Reset in the editor's Settings > Shortcuts goes back to
    "hostKeybinds", so that key is kept equal to the table at every start,
    and nothing else is touched."""
    settings = tmp_path / "settings.json"
    record = tmp_path / "record.json"
    assert _apply(tmp_path, settings, record) == "written"
    data = json.loads(settings.read_text(encoding="utf-8"))
    del data["hostKeybinds"]
    settings.write_text(json.dumps({**SETTINGS, **data}), encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "written"
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["hostKeybinds"] == TABLE and "keybinds" not in written
    assert {k: v for k, v in written.items() if k != "hostKeybinds"} == SETTINGS
    written["hostKeybinds"]["pan"] = "h"
    settings.write_text(json.dumps(written), encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "written"
    assert json.loads(settings.read_text(encoding="utf-8"))["hostKeybinds"]["pan"] == "ctrl+h"
    before = settings.read_bytes()
    assert _apply(tmp_path, settings, record) == "current"
    assert settings.read_bytes() == before


def test_a_settings_file_that_will_not_read_is_left_alone(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{half a file", encoding="utf-8")
    record = tmp_path / "record.json"
    assert _apply(tmp_path, settings, record) == "unreadable"
    assert settings.read_text(encoding="utf-8") == "{half a file" and not record.exists()
    settings.write_text("[1, 2]", encoding="utf-8")
    assert _apply(tmp_path, settings, record) == "unreadable"


def test_the_cleaning_rule(tmp_path):
    got = _keys(tmp_path, "return [m.cleanKeybinds({}, null),"
                          " m.cleanKeybinds({freeze: 'x', pan: 'ctrl+h', undo: 'ctrl+h'}, null),"
                          " m.cleanKeybinds({freeze: 'ctrl+f'}, {freeze: 'ctrl+f'}, {freeze: 'ctrl+g'}),"
                          " m.cleanKeybinds({freeze: 'x', mute: ''}, {freeze: 'ctrl+f', mute: 'ctrl+m'})]")
    empty, theirs, older, moved = got
    assert empty == {"keybinds": {}, "removed": []}
    assert theirs == {"keybinds": {"freeze": "x", "undo": "ctrl+h"}, "removed": ["pan"]}
    assert older == {"keybinds": {}, "removed": ["freeze"]}
    assert moved == {"keybinds": {"freeze": "x", "mute": ""}, "removed": []}


def test_the_main_process_writes_the_table_before_it_starts_the_editor():
    source = (MAIN / "editor.ts").read_text(encoding="utf-8")
    start = source[source.index("async function start("):source.index("function onExit(")]
    # Only with no editor running, nor one asked to close that has not left yet.
    assert start.index("if (ed === null && !(lingering && alive(lingering))) shortcutDefaults(exe)") < start.index(
        "child = spawn(")
    assert "lingering = target.pid" in source
    helper = source[source.index("function shortcutDefaults("):source.index("async function start(")]
    assert "editorSettingsPath(exe, process.env)" in helper
    assert "join(app.getPath('userData'), 'editor-shortcuts.json')" in helper
    assert "try {" in helper and "catch" in helper        # never stops a start
    keys = (MAIN / "editorKeys.ts").read_text(encoding="utf-8")
    assert re.findall(r"^import .* from '([^']+)'", keys, re.M) == ["node:fs", "node:path"]


# ---- F3 and F4: the page's rules ---------------------------------------------


def _opening(tmp_path, body: str, data=None):
    return _run(tmp_path, LIB / "editorOpening.ts", body, data)


def test_the_bar_starts_again_when_a_failed_build_is_made_again(tmp_path):
    got = _opening(tmp_path, """
      const p = m.nextOpeningProgress
      return [
        p(0.3, {state: 'building', progress: 0.2}),
        p(0.3, {state: 'building', progress: 0.5}),
        p(0, {state: 'ready', progress: 1}),
        p(0, {state: 'failed', progress: 0.8}),
        p(0.8, {state: 'building', progress: 0.05}, true),
        p(0.8, {state: 'building', progress: 0.05}),
      ]""")
    never_back, forward, never_whole, failed_counts_none, restarted, held = got
    assert never_back == 0.3 and forward == 0.5 and never_whole == 0.99
    assert failed_counts_none == 0 and restarted == 0.05 and held == 0.8


def test_the_sheet_line_once_the_clip_is_gone(tmp_path):
    words = {"replaces": "R", "besideChanged": "B", "folder": "F {folder}", "downloads": "D"}
    got = _opening(tmp_path, """
      const w = data
      const d = m.exportDestination
      return [
        d({kind: 'clip'}, 'X:/out', w),
        d({kind: 'clip', clip_changed: true}, 'X:/out', w),
        d({kind: 'clip', clip_gone: true}, 'X:/out', w),
        d({kind: 'clip', clip_gone: true, clip_changed: true}, '', w),
        d({kind: 'video'}, 'X:/out', w),
        d(null, '', w),
      ]""", words)
    assert got == ["R", "B", "F X:/out", "D", "F X:/out", "D"]


def test_the_page_reads_the_project_again_when_it_is_shown_again():
    lib = (LIB / "editor.ts").read_text(encoding="utf-8")
    refresh = lib[lib.index("export async function refreshShownProject("):]
    refresh = refresh[:refresh.index("\n}\n")]
    assert "await editorApi.project(id)" in refresh and "await bridge.show(id, await showOptions(project))" in refresh
    assert "store.runtime.projectId !== id" in refresh
    again = lib[lib.index("export async function ensureEditorShown("):]
    again = again[:again.index("\n}\n")]
    assert "editorApi.project(id)" in again and "showOptions(project)" in again
    page = (ROOT / "ui" / "src" / "renderer" / "src" / "pages" / "Editor.tsx").read_text(encoding="utf-8")
    assert "void refreshShownProject()" in page


def test_the_main_process_tells_the_editor_a_changed_line_without_reopening():
    source = (MAIN / "editor.ts").read_text(encoding="utf-8")
    show = source[source.index("ipcMain.handle('editor:show'"):source.index("ipcMain.handle('editor:setBounds'")]
    assert "if (target.ready && destination !== target.destination) await retell(target, projectId)" in show
    retell = source[source.index("async function retell("):]
    retell = retell[:retell.index("\n}\n")]
    # The folder the editor already has open: it answers at once and only
    # points the sheet at the new place (embed.rs open_project, same folder).
    assert "'host.open_project'" in retell and "path: target.projectDir" in retell
    assert "target.destination = destination" in retell


# ---- F1: the editor started while the project is made -------------------------


def test_the_editor_is_prepared_while_a_project_is_made_and_never_shown_empty():
    source = (MAIN / "editor.ts").read_text(encoding="utf-8")
    prepare = source[source.index("ipcMain.handle('editor:prepare'"):source.index("ipcMain.handle('editor:unprepare'")]
    assert "if (ed) return { ok: true }" in prepare and "return start('', '')" in prepare and "serial(" in prepare
    unprepare = source[source.index("ipcMain.handle('editor:unprepare'"):]
    unprepare = unprepare[:unprepare.index("\n  })\n")]
    assert "target.projectId === ''" in unprepare and "closeNow(target" in unprepare
    start = source[source.index("async function start("):source.index("function onExit(")]
    # No project on its command line, hidden, and not shown when it answers.
    assert "...(places\n" in start and "'--hidden'" in start
    assert "if (wantVisible && !prepared) await rpc(target, 'host.set_visible', { visible: true })" in start
    visible = source[source.index("ipcMain.handle('editor:setVisible'"):source.index("ipcMain.handle('editor:prepare'")]
    assert "target.projectId !== ''" in visible
    switch = source[source.index("async function switchTo("):source.index("async function retell(")]
    assert "const wasPrepared = target.projectId === ''" in switch
    assert switch.index("'host.open_project'") < switch.index("if (wantVisible) await rpc(target, 'host.set_visible'")
    lib = (LIB / "editor.ts").read_text(encoding="utf-8")
    ready = lib[lib.index("async function openReady("):lib.index("let opened: EditorProject")]
    assert "if (project.state === 'building') void window.studio?.editor?.prepare?.()" in ready
    assert lib.count("releasePrepared(token)") == 2
    preload = (ROOT / "ui" / "src" / "preload" / "index.ts").read_text(encoding="utf-8")
    assert "ipcRenderer.invoke('editor:prepare')" in preload and "ipcRenderer.invoke('editor:unprepare')" in preload
