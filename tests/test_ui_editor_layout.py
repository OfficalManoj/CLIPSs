"""The Layout picker on the Editor page (editor suite step KG): source checks.

- "Layout…" sits in the strip before "Other languages…", shown for a clip
  project with a layout to pick;
- a gaming clip gets the existing GamingLayoutEditor (its 11 layouts and
  "Remember for this creator"); any other clip the small FramingPicker
  (Auto, Letterbox, Center), each option with a tooltip;
- both hold the editor's cover while open, every string goes through t(),
  no em dash;
- the rebuild follows the language tabs' flow: the job, the editor closed,
  apply (409 retried), the editor shown again.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
PAGE = UI / "pages" / "Editor.tsx"
PICKER = UI / "components" / "editor" / "FramingPicker.tsx"
LIB = UI / "lib" / "editor.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_layout_button_sits_before_other_languages_for_a_clip_with_a_layout():
    page = _read(PAGE)
    assert page.index("{t('Layout…')}") < page.index("{t('Other languages…')}")
    assert "const layout = shownProject?.layout ?? null" in page and "{layout && (" in page
    assert "import GamingLayoutEditor from '../components/GamingLayoutEditor'" in page
    assert "import FramingPicker from '../components/editor/FramingPicker'" in page
    assert "useEditorCover(layoutOpen)" in page


def test_a_gaming_clip_gets_the_gaming_layout_editor_with_remember():
    page = _read(PAGE)
    block = page[page.index("<GamingLayoutEditor"):]
    block = block[:block.index("/>")]
    assert 'context="clip"' in block and "source={{ clipId: shownProject.clip.id }}" in block
    assert "settings={layout.gaming}" in block and "onDone=" in block
    assert "api.saveCreatorGamingLayout(clipId, body.gaming)" in page


def test_the_framing_picker_offers_auto_letterbox_and_center_with_tooltips():
    picker = _read(PICKER)
    for value, label in (("track", "Auto (AI)"), ("letterbox", "Letterbox"), ("center", "Center")):
        assert re.search(rf"value: '{value}',\s*label: '{re.escape(label)}',\s*tip: '", picker), value
    assert "title={t(o.tip)}" in picker and "{t(o.label)}" in picker
    assert not untranslated(picker)


def test_no_english_without_t_and_no_em_dash():
    for path in (PAGE, PICKER, LIB):
        assert "—" not in _read(path), path
    for path in (PAGE, PICKER):
        assert not untranslated(_read(path)), path


def test_the_rebuild_follows_the_language_tabs_flow():
    lib = _read(LIB)
    body = lib[lib.index("export async function changeLayout"):]
    for step in ("editorApi.layout(projectId, body)", "await bridge?.close()", "editorApi.applyLayout(projectId)",
                 "e.status === 409 && attempt < 10", "await ensureEditorShown()",
                 "t('Changing the layout. You can keep editing.')",
                 "t('The editor reopens with the new layout in a few seconds.')"):
        assert step in body, step
