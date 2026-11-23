"""lib/editor.ts run under Node (FL3 fix round 1): opening a project and
stopping an open with "Projects", with a fake bridge (the main process) and
a fake editorApi (the engine).

The module is copied next to small stand-ins for what it imports (React,
lib/api.ts, lib/exportFolder.ts, lib/i18n.ts); lib/editorOpening.ts is the
real one. Node strips the types (--experimental-strip-types).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "ui" / "src" / "renderer" / "src" / "lib"

STUBS = {
    "react.mjs": "export const useEffect = () => {}; export const useState = (v) => [v, () => {}];\n",
    "i18n.mjs": "export const t = (s) => s;\n",
    "exportFolder.mjs": (
        "export async function getExportFolder() {\n"
        "  const f = globalThis.__fake; f.folderCalls += 1;\n"
        "  if (f.onFolder) await f.onFolder(f.folderCalls);\n"
        "  return 'D:/exports';\n"
        "}\n"
    ),
    "api.mjs": (
        "export class EditorRouteError extends Error {\n"
        "  constructor(status, reason, message) { super(message); this.status = status; this.reason = reason }\n"
        "}\n"
        "const project = (id) => ({ id, name: 'Project ' + id, state: 'ready', kind: 'video', clip: null,\n"
        "  exports: [], notes: [], recovered: [] });\n"
        "const f = () => globalThis.__fake;\n"
        "export const editorApi = {\n"
        "  project: async (id) => (f().project ? f().project(id, project) : project(id)),\n"
        "  reopen: async (id) => (f().reopen ? f().reopen(id, project) : project(id)),\n"
        "  open: async (req) => (f().open ? f().open(req, project) : { project: project('p0'), created: true }),\n"
        "};\n"
    ),
}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, body: str):
    """Run `body` with `m` the copied lib/editor.ts, `f` the fake state and
    `bridge` the fake bridge; return what it returns, as JSON."""
    source = (LIB / "editor.ts").read_text(encoding="utf-8")
    for name, stub in (("react", "./react.mjs"), ("./api", "./api.mjs"), ("./exportFolder", "./exportFolder.mjs"),
                       ("./i18n", "./i18n.mjs"), ("./editorOpening", "./editorOpening.ts")):
        source, n = re.subn(rf"from '{re.escape(name)}'", f"from '{stub}'", source)
        assert n == 1, name
    (tmp_path / "editor.ts").write_text(source, encoding="utf-8")
    shutil.copyfile(LIB / "editorOpening.ts", tmp_path / "editorOpening.ts")
    for name, text in STUBS.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    script = (
        "globalThis.__fake = { folderCalls: 0, onFolder: null, calls: [], answer: 0 };"
        "const f = globalThis.__fake;"
        "const bridge = {"
        "  onShow: null,"
        "  async show(id) { f.calls.push('show ' + id); if (bridge.onShow) await bridge.onShow(id);"
        "    return { ok: true } },"
        "  async close() { f.calls.push('close') },"
        "  async prepare() { f.calls.push('prepare') },"
        "  async unprepare() { f.calls.push('unprepare') },"
        "  async ask(q) { f.calls.push('ask'); return f.answer },"
        "  onState(fn) { bridge.emit = fn }, onExport(fn) { bridge.exported = fn }, async getState() { return null },"
        "  async setWords() {},"
        "};"
        "globalThis.window = { studio: { editor: bridge }, dispatchEvent() {}, addEventListener() {},"
        "  confirm: () => true };"
        f"const m = await import({json.dumps((tmp_path / 'editor.ts').as_uri())});"
        f"console.log(JSON.stringify(await (async () => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _state(after: str = "") -> str:
    return after + "const s = m.editorStore(); return { shown: s.shownId, opening: s.opening, calls: f.calls };"


def test_an_open_shows_the_project(tmp_path):
    got = _run(tmp_path, "await m.openEditorProject('p1');" + _state())
    # unprepare after every open that ended: the main process closes only an editor with no project.
    assert got == {"shown": "p1", "opening": None, "calls": ["show p1", "unprepare"]}


def test_projects_while_the_show_options_are_read_stops_the_open_before_show(tmp_path):
    # getExportFolder's 2nd call is showOptions' (the 1st is reopen's).
    got = _run(tmp_path, """
      f.onFolder = async (n) => { if (n === 2) await m.closeEditorProject() }
      await m.openEditorProject('p1');""" + _state())
    assert got == {"shown": None, "opening": None, "calls": ["close"]}


def test_a_show_that_returns_after_a_stop_is_closed_again(tmp_path):
    got = _run(tmp_path, """
      bridge.onShow = async () => { bridge.onShow = null; await m.closeEditorProject() }
      await m.openEditorProject('p1');""" + _state())
    # The page's own close, then the close after the late show: no editor left running hidden.
    assert got == {"shown": None, "opening": None, "calls": ["show p1", "close", "close"]}


def test_a_show_another_open_took_over_is_left_to_that_open(tmp_path):
    got = _run(tmp_path, """
      let second
      bridge.onShow = async () => { bridge.onShow = null; second = m.openEditorProject('p2') }
      await m.openEditorProject('p1');
      await second;""" + _state())
    assert got == {"shown": "p2", "opening": None, "calls": ["show p1", "show p2", "unprepare"]}


# ---- F1 desktop fix round: an open while another project is shown -----------------------------

_SNAP = "const snap = () => { const s = m.editorStore(); return { shown: s.shownId, project: s.project && s.project.id," \
        " opening: s.opening, notice: s.notice, error: s.openError } };"


def test_an_open_while_a_project_is_shown_leaves_nothing_of_it_on_screen(tmp_path):
    """bb F-1: from the moment clip B starts opening, nothing of A (its name,
    notice, Publish, editor) is on screen: A is saved and closed the way
    "Projects" does, and the page shows B's opening state."""
    got = _run(tmp_path, _SNAP + """
      await m.openEditorProject('p1');
      m.showEditorNotice({ text: 'Saved as this clip’s video.' });
      let during = null
      f.project = async (id, make) => { if (id === 'p2') during = snap(); return make(id) }
      await m.openEditorProject('p2');
      return { during, after: snap(), calls: f.calls }""")
    assert got["during"] == {"shown": None, "project": None, "opening": {"name": "", "progress": 0}, "notice": None,
                             "error": ""}
    assert got["after"] == {"shown": "p2", "project": "p2", "opening": None, "notice": None, "error": ""}
    # A's editor is closed (it saves first) before B's is shown.
    assert got["calls"] == ["show p1", "unprepare", "close", "show p2", "unprepare"]


def test_an_open_while_the_shown_project_exports_asks_first(tmp_path):
    """Keep exporting: the new open does not start and A stays as it was."""
    got = _run(tmp_path, _SNAP + """
      m.connectEditorBridge()
      await m.openEditorProject('p1');
      bridge.emit({ running: true, exporting: { fraction: 0.4, stage: 'video' } })
      f.answer = 0
      await m.openEditorProject('p2');
      const kept = snap()
      f.answer = 1
      await m.openEditorProject('p2');
      return { kept, after: snap(), calls: f.calls }""")
    assert got["kept"]["shown"] == "p1" and got["kept"]["opening"] is None
    assert got["after"]["shown"] == "p2"
    assert got["calls"] == ["show p1", "unprepare", "ask", "ask", "close", "show p2", "unprepare"]


def test_a_failed_open_while_a_project_was_shown_says_why(tmp_path):
    """review 3: the reason stays (the page shows it in the strip with the way
    back), and A does not come back."""
    got = _run(tmp_path, _SNAP + """
      await m.openEditorProject('p1');
      f.project = async (id, make) => { if (id === 'p2') throw new Error('gone'); return make(id) }
      await m.openEditorProject('p2');
      const failed = snap()
      await m.closeEditorProject();
      return { failed, back: snap() }""")
    assert got["failed"] == {"shown": None, "project": None, "opening": None, "notice": None,
                             "error": "The project could not be opened."}
    assert got["back"] == {"shown": None, "project": None, "opening": None, "notice": None, "error": ""}


def test_projects_clears_the_notices_of_the_project_it_closes(tmp_path):
    """U-3 / bb N-1: "Saved as this clip's video." does not follow to the lists."""
    got = _run(tmp_path, _SNAP + """
      await m.openEditorProject('p1');
      m.showEditorNotice({ text: 'Saved as this clip’s video.' });
      await m.closeEditorProject();
      return snap()""")
    assert got["notice"] is None and got["shown"] is None


def test_a_reopen_that_answers_building_after_a_stop_does_not_start_again(tmp_path):
    """review-f1-r2 1: "Projects" pressed while a failed project is reopened,
    and the reopen answers "building": the open stays stopped, and no editor
    is prepared after the close."""
    got = _run(tmp_path, """
      f.reopen = async (id, make) => { await m.closeEditorProject(); return { ...make(id), state: 'building' } }
      await m.openEditorProject('p1');""" + _state())
    assert got == {"shown": None, "opening": None, "calls": ["close"]}


def test_a_stale_opens_failure_does_not_wipe_a_newer_open(tmp_path):
    """review-f1-r2 2: open A, open B while A's reopen is in flight, A's
    reopen fails: B's opening stays and A's error is never shown."""
    got = _run(tmp_path, """
      let second, seen = null
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      f.reopen = async (id, make) => {
        if (id === 'p1') { second = m.openEditorProject('p2'); await wait(10); throw new Error('A failed') }
        await wait(40)
        const s = m.editorStore(); seen = { opening: s.opening && s.opening.id, error: s.openError }
        return make(id)
      }
      await m.openEditorProject('p1');
      await second;
      const s = m.editorStore();
      return { seen, shown: s.shownId, error: s.openError }""")
    assert got == {"seen": {"opening": "p2", "error": ""}, "shown": "p2", "error": ""}


# ---- F1 regression round: an open of the project already shown -------------------------------

# A clip project of clip 7 (p1), the one shown, and one of clip 8 (p2).
_CLIPS = """
  const clipOf = { p1: 7, p2: 8 }
  const asClip = (id, make) => ({ ...make(id), kind: 'clip', clip: { id: clipOf[id], video_id: 'v1' } })
  f.project = async (id, make) => asClip(id, make)
  f.reopen = async (id, make) => asClip(id, make)
  f.open = async (req, make) => ({ project: asClip(req.clip_id === 7 ? 'p1' : 'p2', make), created: false })
"""


def test_opening_the_shown_project_again_keeps_it(tmp_path):
    """review-f1-final 1 (serious): "Open in editor" on the clip already shown,
    through openInEditor (the Clips page) and openEditorProject (Recent
    projects, Quick edit's question): no close, no restart, no question, even
    while it exports; the project and its notice stay."""
    got = _run(tmp_path, _SNAP + _CLIPS + """
      m.connectEditorBridge()
      await m.openInEditor({ kind: 'clip', clip_id: 7 });
      m.showEditorNotice({ text: 'Saved as this clip’s video.' });
      const before = f.calls.length
      await m.openInEditor({ kind: 'clip', clip_id: 7 });
      await m.openEditorProject('p1');
      bridge.emit({ running: true, exporting: { fraction: 0.4, stage: 'video' } })
      f.answer = 1  // "Close", were it asked
      await m.openInEditor({ kind: 'clip', clip_id: 7 });
      await m.openEditorProject('p1');
      return { after: snap(), added: f.calls.slice(before) }""")
    assert got["added"] == []
    assert got["after"] == {"shown": "p1", "project": "p1", "opening": None,
                            "notice": {"text": "Saved as this clip’s video."}, "error": ""}


def test_open_in_editor_on_another_clip_while_one_is_shown_switches(tmp_path):
    """qc-f1-final 3: the Clips page's way (openInEditor) with clip A shown
    and clip B asked for: A is closed, B is shown."""
    got = _run(tmp_path, _SNAP + _CLIPS + """
      await m.openInEditor({ kind: 'clip', clip_id: 7 });
      await m.openInEditor({ kind: 'clip', clip_id: 8 });
      return { after: snap(), calls: f.calls }""")
    assert got["after"]["shown"] == "p2" and got["after"]["error"] == ""
    assert got["calls"] == ["show p1", "unprepare", "close", "show p2", "unprepare"]


def test_a_whole_video_whose_answer_is_the_shown_project_keeps_it(tmp_path):
    """openInEditor for a video or a file asks the engine first while a
    project is shown: its own project is kept, another one is switched to."""
    got = _run(tmp_path, _SNAP + """
      f.open = async (req, make) => ({ project: make(req.video_id), created: false })
      await m.openEditorProject('p1');
      const before = f.calls.length
      await m.openInEditor({ kind: 'video', video_id: 'p1', shape: 'source' });
      const kept = { state: snap(), added: f.calls.slice(before) }
      await m.openInEditor({ kind: 'video', video_id: 'p2', shape: 'source' });
      return { kept, after: snap(), calls: f.calls }""")
    assert got["kept"] == {"state": {"shown": "p1", "project": "p1", "opening": None, "notice": None, "error": ""},
                           "added": []}
    assert got["after"]["shown"] == "p2"
    assert got["calls"] == ["show p1", "unprepare", "close", "show p2", "unprepare"]


def test_a_double_open_while_the_shown_project_exports_asks_once(tmp_path):
    """review-f1-final 3: two opens at once share one question and one close."""
    got = _run(tmp_path, _SNAP + """
      m.connectEditorBridge()
      await m.openEditorProject('p1');
      bridge.emit({ running: true, exporting: { fraction: 0.4, stage: 'video' } })
      f.answer = 1
      await Promise.all([m.openEditorProject('p3'), m.openEditorProject('p3')]);
      return { after: snap(), calls: f.calls }""")
    assert got["after"]["shown"] == "p3"
    assert got["calls"] == ["show p1", "unprepare", "ask", "close", "show p3", "unprepare"]


def test_a_stale_poll_failure_does_not_wipe_a_newer_open(tmp_path):
    """qc-f1-final 1: the poll loop's read of A fails after B's open began:
    B's open is left alone, and A's failure is never shown."""
    got = _run(tmp_path, """
      let second, reads = 0, seen = null
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      f.project = async (id, make) => {
        if (id !== 'p1') return make(id)
        reads += 1
        if (reads === 1) return { ...make(id), state: 'building', progress: 0.1 }
        second = m.openEditorProject('p2'); await wait(20); throw new Error('A could not be read')
      }
      f.reopen = async (id, make) => {
        if (id === 'p2') { await wait(60); const s = m.editorStore(); seen = { opening: !!s.opening, error: s.openError } }
        return make(id)
      }
      await m.openEditorProject('p1');
      await second;
      const s = m.editorStore();
      return { seen, shown: s.shownId, opening: s.opening, error: s.openError }""")
    assert got == {"seen": {"opening": True, "error": ""}, "shown": "p2", "opening": None, "error": ""}


def test_a_late_export_answer_of_another_project_says_nothing(tmp_path):
    """review-f1-final 2: only the shown project's export answers show in
    the strip."""
    got = _run(tmp_path, _SNAP + """
      m.connectEditorBridge()
      await m.openEditorProject('p1');
      bridge.exported({ project_id: 'p9', result: 'replaced_clip' })
      const other = snap().notice
      bridge.exported({ project_id: 'p1', result: 'replaced_clip' })
      return { other, own: snap().notice }""")
    assert got == {"other": None, "own": {"text": "Saved as this clip’s video."}}


def test_starting_the_shown_editor_again_stops_for_a_newer_open(tmp_path):
    """review-f1-final 6: an open of B that begins while ensureEditorShown
    reads the export folder: A's editor is not started under it."""
    got = _run(tmp_path, """
      let second, armed = false
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      await m.openEditorProject('p1');
      f.onFolder = async () => { if (armed) { armed = false; second = m.openEditorProject('p2'); await wait(10) } }
      armed = true
      await m.ensureEditorShown();
      await second;""" + _state())
    assert got == {"shown": "p2", "opening": None, "calls": ["show p1", "unprepare", "close", "show p2", "unprepare"]}


def test_entering_the_page_again_clears_an_old_failure_only(tmp_path):
    """review-f1-final 4: clearOpenError (the page entered again) takes an old
    failure away, so the lists show; the next open works as ever."""
    got = _run(tmp_path, _SNAP + """
      f.project = async (id, make) => { if (id === 'p2') throw new Error('gone'); return make(id) }
      await m.openEditorProject('p2');
      const failed = snap().error
      m.clearOpenError()
      const cleared = snap()
      await m.openEditorProject('p1');
      return { failed, cleared, shown: snap() }""")
    assert got["failed"] == "The project could not be opened."
    assert got["cleared"] == {"shown": None, "project": None, "opening": None, "notice": None, "error": ""}
    assert got["shown"]["shown"] == "p1"


def test_the_page_switches_while_the_shown_project_closes(tmp_path):
    """The shown project's close (main saves it, up to seconds) runs while the
    page already shows the new open; the new project is shown only after it."""
    got = _run(tmp_path, _SNAP + """
      const wait = (ms) => new Promise((r) => setTimeout(r, ms))
      await m.openEditorProject('p1');
      let during = null
      bridge.close = async () => { f.calls.push('close'); await wait(30); during = snap(); f.calls.push('closed') }
      await m.openEditorProject('p2');
      return { during, calls: f.calls }""")
    assert got["during"]["shown"] is None and got["during"]["opening"] is not None
    assert got["calls"] == ["show p1", "unprepare", "close", "closed", "show p2", "unprepare"]
