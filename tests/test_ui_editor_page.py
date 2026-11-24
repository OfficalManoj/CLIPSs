"""The Editor page in the app (editor suite K2, U2 to U5): source checks.

Clips Kitty Editor is a native window the main process lays over the Editor
page's area, so the page has rules a browser test can't see:

- every string it shows goes through t() (lib/i18n.ts);
- the Editor nav entry and every way in exist only while the engine says the
  editor is found: otherwise the app looks exactly as before;
- anything of the app's own that can be drawn over the editor's area while
  the page is up either holds the cover (lib/editorCover.ts) or has a rule of
  its own (the list in docs/editor-suite/api/k2-editor.md);
- the area can be reached with Tab and has a name a screen reader can say.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
APP = UI / "App.tsx"
PAGE = UI / "pages" / "Editor.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_editor_page_shows_no_untranslated_english():
    found = [f"{path.relative_to(UI).as_posix()} {problem}"
             for path in (PAGE, UI / "components" / "editor" / "ProjectExportPrompt.tsx")
             for problem in untranslated(_read(path))]
    assert not found, "English on the Editor page that doesn't go through t():\n" + "\n".join(found)


def test_the_editor_libs_say_nothing_without_t():
    # Plain sentences in the libraries reach the page as notices and questions.
    for name in ("editor.ts", "projectExportPrompt.ts"):
        source = _read(UI / "lib" / name)
        for match in re.finditer(r"text: '([^']+)'|askNative\('([^']+)'", source):
            raise AssertionError(f"lib/{name}: a sentence without t(): {match.group(0)}")


def test_the_clips_page_is_called_clips():
    """KC (the owner's call): "Clip Editor" is now "Clips", in the sidebar
    and the page's title; the guard against the label that blocked the
    Store listing stays in the comment."""
    app = _read(APP)
    assert "{ id: 'studio', label: 'Clips', icon: '✂' }" in app
    assert "Microsoft Store" in app and "never go back to that one" in app
    assert "label: 'Clip Editor'" not in app
    assert "<h2 className=\"text-2xl font-bold\">{t('Clips')}</h2>" in _read(UI / "pages" / "ClipStudio.tsx")
    assert "{ id: 'editor', label: 'Editor'" in app
    # Placed right after Clips.
    assert app.index("id: 'editor'") > app.index("id: 'studio'")
    assert app.index("id: 'editor'") < app.index("id: 'creators'")


def test_nav_entry_and_page_only_when_the_editor_is_found():
    app = _read(APP)
    assert "useEditorFound()" in app
    assert "NAV.filter((item) => item.id !== 'editor' || editorFound)" in app
    assert "page === 'editor' && editorFound &&" in app
    status = _read(UI / "lib" / "editorStatus.ts")
    # Found only with the main process's bridge too (not in the browser shim).
    assert "found && !!window.studio?.editor" in status
    assert "editorApi" in status and ".status()" in status


def test_clip_approvals_are_read_inside_their_provider():
    """K-D9: usePendingApprovals() runs in a component rendered inside
    ClipUpdatesProvider, never in the one that renders the provider (where
    it read the empty default and always gave 0)."""
    app = _read(APP)
    outer = app[app.index("export default function App("):]
    outer = outer[:outer.index("\n}\n")]
    assert "<ClipUpdatesProvider>" in outer and "<Shell />" in outer and "usePendingApprovals" not in outer
    shell = app[app.index("function Shell("):]
    assert "usePendingApprovals()" in shell and "ClipUpdatesProvider" not in shell


def test_every_edit_opens_the_editor_and_says_when_it_is_not_installed():
    """KC: Quick edit is gone. The card's and the side panel's Edit open
    Clips Kitty Editor; while it is not found they stay, off, with a tooltip
    saying it isn't installed (they used to open Quick edit then)."""
    studio = _read(UI / "pages" / "ClipStudio.tsx")
    assert "onEdit={() => void openInEditor({ kind: 'clip', clip_id: clip.id })}" in studio
    assert "onEdit={() => void openInEditor({ kind: 'clip', clip_id: current.id })}" in studio
    assert studio.count("editReady={editorFound}") == 2
    for gone in ("EditorModal", "EditorView", "editingClip", "setEditingClipId", "onOpenEditor", "onOpenInEditor"):
        assert gone not in studio, gone
    # FL3: no "Edit whole video" on the Clips page; a whole video is
    # opened from the Editor page's own "Edit a whole video".
    assert "Edit whole video" not in studio and "ShapeSelect" not in studio and "wholeShape" not in studio
    missing = "t('Clips Kitty Editor wasn’t found with this copy of Clips Kitty, so clips can’t be edited here.')"
    for rel in ("components/ClipCard.tsx", "components/ClipEditor.tsx"):
        source = _read(UI / rel)
        assert missing in source, rel
        assert "aria-disabled={!editReady}" in source, rel
    assert "onClick={editReady ? onEdit : undefined}" in _read(UI / "components" / "ClipEditor.tsx")
    assert "if (editReady) onEdit()" in _read(UI / "components" / "ClipCard.tsx")


def test_quick_edit_is_gone():
    """KC: the old editor and what only it used are deleted; nothing imports them."""
    gone = ("components/TimelineEditor.tsx", "components/EditorModal.tsx", "components/ColorControls.tsx",
            "components/FilterPicker.tsx", "components/EditChat.tsx", "components/EditSuggestions.tsx",
            "components/FreeMusicPicker.tsx", "components/UploadPostPanel.tsx", "lib/captionEdits.ts",
            "lib/speakerTurns.ts")
    for rel in gone:
        assert not (UI / rel).exists(), rel
    names = [rel.rsplit("/", 1)[1].split(".")[0] for rel in gone]
    for path in UI.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        for name in names:
            assert not re.search(rf"from '[./]+(?:components/|lib/)?{name}'", text), (path.name, name)
    # Shared pieces stay (queue settings, Creators, the add-videos form).
    for rel in ("components/CaptionStyleControls.tsx", "components/GamingLayoutEditor.tsx",
                "components/WatermarkControls.tsx"):
        assert (UI / rel).exists(), rel


# Everything App.tsx (or the sidebar it draws) renders that can be drawn over
# the editor's area, and its rule. Keep in step with k2-editor.md.
COVER_RULES = {
    "components/FeedbackHub.tsx": "cover",          # its dialog, opened from the sidebar
    "components/SetupWizard.tsx": "cover",          # full-window wizard
    "components/editor/ProjectExportPrompt.tsx": "cover",
    "components/ApprovalCard.tsx": "approvals",     # cards at the bottom right: the strip's button instead
}
# Rendered from App.tsx but drawn only in the page's flow or the sidebar.
IN_FLOW = {"components/UpdateBanner.tsx", "components/ModelSwitcher.tsx"}

_OVERLAY = re.compile(r"""\bfixed\b[^'"`]*inset|className="fixed|['"`]fixed |createPortal\(|<dialog\b|showModal\(""")


def _app_components() -> set[str]:
    app = _read(APP)
    return {f"{m.group(1)}.tsx" for m in re.finditer(r"from '\./(components/[\w/]+)'", app)}


def test_every_overlay_from_the_shell_has_a_rule():
    unknown = []
    for relative in sorted(_app_components()):
        source = _read(UI / relative)
        if _OVERLAY.search(source) and relative not in COVER_RULES and relative not in IN_FLOW:
            unknown.append(relative)
    assert not unknown, ("Drawn over the page from App.tsx with no rule for Clips Kitty Editor "
                         "(hold useEditorCover, or add it to k2-editor.md and this list):\n" + "\n".join(unknown))


def test_each_rule_is_kept():
    for relative, rule in COVER_RULES.items():
        source = _read(UI / relative)
        if rule == "cover":
            assert "useEditorCover(" in source, relative
    cards = _read(UI / "components" / "ApprovalCard.tsx")
    assert "|| hidden) return null" in cards
    app = _read(APP)
    assert "hidden={editorShown && !approvalsOpen}" in app
    assert "useEditorCover(editorShown && approvalsOpen)" in app
    doc = _read(ROOT / "docs" / "editor-suite" / "api" / "k2-editor.md")
    for relative in list(COVER_RULES) + sorted(IN_FLOW):
        assert Path(relative).stem in doc, f"{relative} is not in k2-editor.md's list"


def test_the_page_hides_the_editor_while_covered_and_when_left():
    page = _read(PAGE)
    assert "const wanted = !!store.shownId && holds === 0" in page
    assert "bridge?.setVisible(wanted)" in page
    assert "() => () => void bridge?.setVisible(false)" in page


def test_the_area_follows_size_place_and_scale():
    page = _read(PAGE)
    for needed in ("new ResizeObserver(send)", "window.devicePixelRatio", "matchMedia(`(resolution:",
                   "bridge.onMeasure(send)", "requestAnimationFrame", "Math.floor(r.left * dpr)",
                   "Math.max(1, Math.round(r.width * dpr))"):
        assert needed in page, needed


def test_keyboard_and_screen_reader():
    page = _read(PAGE)
    assert 'role="application"' in page
    # Reached by Tab, except behind a window of the app's own (F1 U-1).
    assert "tabIndex={covered ? -1 : 0}" in page
    assert "aria-label={t('Editor. Press Enter to use it, Ctrl+F6 to come back.')}" in page
    assert "bridge?.focus()" in page
    assert 'aria-live="polite"' in page
    # A click in the editor leaves no page button focused (Space would press it).
    assert "(document.activeElement as HTMLElement | null)?.blur?.()" in page
    app = _read(APP)
    # A press and a focus in the page are told apart (FL2 D1): a focus the
    # page fires only because the window came back does not take the keys.
    assert "document.addEventListener('pointerdown', press, true)" in app
    assert "document.addEventListener('focusin', focus, true)" in app


def test_questions_over_the_editor_are_native():
    page = _read(PAGE)
    lib = _read(UI / "lib" / "editor.ts")
    assert "window.confirm(" in page          # delete a project
    assert "bridge.ask(" in lib               # clip changed in Quick edit
    assert "Start a new project from the clip" in lib


def test_quick_edit_asks_before_making_an_editor_export_again():
    api = _read(UI / "lib" / "api.ts")
    for route in ("`/clips/${id}/render`", "`/clips/${id}/ai-edit`", "`/clips/${id}/captions`, { lines }, 'PUT'"):
        assert "renderRequest<" in api and route in api, route
    assert "newFilesRequest<{ job_id: number; languages: string[]; clips: number }>('/translate', body)" in api
    assert "replace_project_edits: true" in api and "without_project_edits: true" in api
    prompt = _read(UI / "components" / "editor" / "ProjectExportPrompt.tsx")
    # First focus never on "Apply anyway" / "Make it anyway".
    assert "ref={canOpen ? undefined : first}" in prompt
    assert prompt.count("ref={first}") == 1
    assert "<ProjectExportPrompt />" in _read(APP)


def test_one_edit_per_place():
    """KC: one "Edit" on the card and one in the side panel, both opening
    the editor; no "Open in editor" beside an "Edit this clip"."""
    for rel in ("components/ClipCard.tsx", "components/ClipEditor.tsx"):
        source = _read(UI / rel)
        assert "t('Edit')" in source, rel
        assert "Open in editor" not in source and "Edit this clip" not in source, rel
    lib = _read(UI / "lib" / "editor.ts")
    said = "".join(re.findall(r"t\('([^']*)'\)", lib))
    assert "Quick edit" not in said and "Clip Editor" not in said


def test_notices_and_exports_reach_a_screen_reader():
    """UX round: the export notice sits in live regions that are always there
    (status, or alert for a failure); an export is announced by quarters.
    In the strip the live regions are what shows the notice, so its words
    are there once (F1 U-5), and each close button names its notice."""
    page = _read(PAGE)
    assert page.count('role="status" className="sr-only"') == 0
    assert 'role="status" className="text-accent truncate empty:hidden"' in page
    assert 'role="alert" className="text-amber-400 truncate empty:hidden"' in page
    assert "aria-hidden=\"true\">{store.notice.text}" not in page
    assert "aria-label={t('Dismiss')}" not in page
    assert page.count("t('Dismiss: {notice}')") == 2
    assert 'role="status" className="text-sm text-accent empty:hidden"' in page
    assert 'role="alert" className="text-sm text-amber-400 empty:hidden"' in page
    assert "Math.floor(Math.max(0, exporting.fraction) * 4)) * 25" in page
    # The percent shown every poll is not itself a live region.
    assert '<span className="text-muted">{status}</span>' in page
    lib = _read(UI / "lib" / "editor.ts")
    assert "t('The export did not finish.'), tooltip: answer.error || undefined, failure: true" in lib


def test_the_export_sheet_is_told_where_the_file_goes():
    lib = _read(UI / "lib" / "editor.ts")
    for words in ("Replaces this clip in the Library", "Export folder: {folder}",
                  "Goes to your Downloads folder", "Saved next to this clip, which was changed since"):
        assert f"t('{words}')" in lib, words
    # The open, a start again, and the page shown again (FL2 F4: the line
    # follows a clip deleted while its project was open).
    assert lib.count("await showOptions(") == 3
    assert "exportDestination(project, exportFolder, {" in lib


def test_saved_as_this_clips_video_has_no_folder_tooltip():
    """K-D21: the notice after a clip's export names no internal folder (a
    screen reader reads a tooltip too); the folder stays for "Saved to"
    and for an export kept beside the clip."""
    lib = _read(UI / "lib" / "editor.ts")
    case = lib[lib.index("case 'replaced_clip':"):lib.index("case 'kept_beside':")]
    assert "t('Saved as this clip’s video.')" in case and "answer.path" not in case
    rest = lib[lib.index("case 'kept_beside':"):lib.index("case 'failed':")]
    assert rest.count("tooltip: answer.path") == 2


def test_the_opening_bar_never_goes_back_or_says_100_before_the_editor_shows():
    """K-D25: the bar follows the engine's progress (the base video's own),
    never back (the event stream and the page's asking can cross), only the
    project being opened moves it, and it stops at 99% until the editor has
    the project on screen (the page's bar goes when it does)."""
    lib = _read(UI / "lib" / "editor.ts")
    rules = _read(UI / "lib" / "editorOpening.ts")
    assert "export const OPENING_MOST = 0.99" in rules and "export { OPENING_MOST }" in lib
    assert "return Math.min(OPENING_MOST, Math.max(said, now))" in rules
    helper = lib[lib.index("export function openingProgress("):]
    helper = helper[:helper.index("\n}\n")]
    assert "nextOpeningProgress(before, project, restart)" in helper and "store.opening.id === project.id" in helper
    ready = lib[lib.index("async function openReady("):lib.index("let opened: EditorProject")]
    assert "progress: openingProgress(project, restart)" in ready
    assert ready.count("progress: openingProgress(project)") == 1 and "project.progress ||" not in ready
    # Made again after failing: the bar starts again (FL2 F3; the rule itself
    # runs under Node in tests/test_ui_editor_fl2.py).
    assert "await openReady(opened, token, false, true)" in lib
    event = lib[lib.index("export function onEditorProjectEvent("):lib.index("let newsWaiter")]
    assert "store.opening.id !== e.id" in event and "openingProgress({ id: e.id, progress: e.progress })" in event


def test_a_finished_build_is_opened_at_once_not_at_the_next_look():
    """K-D25: the page asks for a project being made once a second, or at
    once when the engine says its build ended."""
    lib = _read(UI / "lib" / "editor.ts")
    ready = lib[lib.index("async function openReady("):lib.index("let opened: EditorProject")]
    assert "await buildNews(1000)" in ready and "await sleep(1000)" not in ready
    event = lib[lib.index("export function onEditorProjectEvent("):lib.index("let newsWaiter")]
    assert "if (e.state && e.state !== 'building') newsWaiter?.()" in event


# ---- FL3 (F1): the fixes he saw, plus Publish ---------------------------------------------------


def _project_view(page: str) -> str:
    view = page[page.index("function ProjectView("):]
    return view[:view.index("\nexport default function Editor(")]


def test_an_open_goes_straight_to_the_project_not_the_lists():
    """FL3 A: "Open in editor" shows the strip and the area while the
    project is made and opened, never Recent projects first."""
    page = _read(PAGE)
    main = page[page.index("export default function Editor("):]
    assert "store.shownId || store.opening || store.openError ? (" in main and "<ProjectLists />" in main
    view = _project_view(page)
    # The strip: the clip's name (or the plain words before it is known) and the bar.
    strip = view[view.index("{store.opening && ("):view.index("{showNotes && (")]
    assert "t('Getting “{name}” ready…')" in strip and "t('Getting the project ready…')" in strip
    assert "Math.round(store.opening.progress * 100)" in strip and "bg-accent" in strip
    # The area says "Opening the editor…" from the start (whatever an earlier
    # editor said), or after a failed open the way back (F1 F-1).
    assert re.search(r"if \(!store\.shownId\) \{[^}]*message =\s*store\.openError && !store\.opening", view)
    assert "{t('Opening the editor…')}" in view and "(!store.shownId || !rt.visible) &&" in view
    # The strip shows the new project only: no name, "Saved" or Publish of
    # the one before (F1 F-1, U-2; the behaviour runs in test_ui_editor_open.py).
    assert "{store.shownId && (\n          <span className=\"font-medium truncate" in view
    assert "if (!rt.running || !store.shownId) return ''" in page
    assert re.search(r"!store\.shownId && !store\.opening && store\.openError && \(\s*<span role=\"alert\"", view)
    # Nothing is shown in the area before the project is.
    assert "const wanted = !!store.shownId && holds === 0" in view
    # "Projects" during an open stops it and shows the lists.
    lib = _read(UI / "lib" / "editor.ts")
    close = lib[lib.index("export async function closeEditorProject("):]
    close = close[:close.index("\n}\n")]
    assert "++openToken" in close and "opening: null" in close and "shownId: null" in close
    # "Clip approvals (n)" only with a project shown: during an open App.tsx
    # draws the cards themselves and the button would do nothing.
    assert re.search(r"\{store\.shownId && approvals > 0 && \(", view)
    # A failed open ends with no project and its reason, said in the strip.
    assert lib.count("set({ opening: null, openError: openErrorText(e) })") >= 2
    lists = page[page.index("function ProjectLists("):page.index("function stateText(")]
    # The lists' own "Getting ready" card could no longer show: gone.
    assert "{store.openError}" not in lists and "store.opening &&" not in lists


def test_a_whole_video_starts_in_its_own_shape():
    """FL3 B: "Same as the video" first and the default in both ways to
    start a project; the other four shapes stay."""
    page = _read(PAGE)
    shapes = page[page.index("export const SHAPES"):page.index("export function ShapeSelect(")]
    assert re.findall(r"value: '([^']+)'", shapes) == ["source", "16:9", "9:16", "1:1", "4:5"]
    assert "t('Same as the video')" in page
    assert "useState<EditorShape>('source')" in page and page.count("useState<EditorShape>('16:9')") == 0
    assert "shape: videoShape" in page and "shape: fileShape" in page
    api = _read(UI / "lib" / "api.ts")
    assert "export type EditorShape = 'source' | '16:9' | '9:16' | '1:1' | '4:5'" in api


def test_publish_is_the_clips_pages_one_clip_window():
    """FL3 C (review fix S1): one Publish window, two ways in. The Editor
    page's Publish opens PublishAllDialog for that one clip, through the
    provider the Clips page uses, and hides the editor while it is open."""
    page = _read(PAGE)
    view = _project_view(page)
    assert re.search(r"<PublishAllDialog\s+clips=\{\[publishClip\]\}\s+provider=\{publishSetup\.provider\}", view)
    assert "returnFocus={publishButton}" in view
    assert "useEditorCover(publishClip !== null)" in view
    # Off stays reachable by Tab (its reason is read out), the click ignored (F1 U-6).
    assert "{publish !== 'none' && (" in view and "aria-disabled={publishOff !== null}" in view
    assert " disabled={publishOff" not in view and "if (publishOff !== null) return" in view
    for words in ("Export first: Publish sends the clip in your Library.",
                  "Wait for the export to finish."):
        assert f"t('{words}')" in view, words
    # On under the Clips page's rule: whether or not a provider is set up;
    # the window says what is (F1 U-4, bb F-2).
    assert "set up WoopSocial or Upload-Post" not in view and "const publishOff = exportWhy" in view
    assert "Your last export was saved next to this clip, not as its video" in view
    # Read again before it opens: the clip may have changed meanwhile.
    opener = view[view.index("const openPublish = async"):]
    opener = opener[:opener.index("\n  }\n")]
    assert "await rereadShownProject(id)" in opener and "publishState(now.project) !== 'ready'" in opener
    # A lookup that fails says so in the strip, in words that fit: a read error
    # is not "no longer in the Library", and a gone clip's project is read again.
    assert "t('Clips Kitty could not read the clip. Try again in a moment.'), failure: true" in opener
    gone = opener[opener.index("t('This clip is no longer in the Library.'), failure: true"):]
    assert "await rereadShownProject(id)" in gone
    # Publish sits with the project's state at the right of the strip (mockup 03):
    # one ml-auto group, not two ml-auto items splitting the space.
    strip = view[view.index("{/* Publish and the project's state"):]
    strip = strip[:strip.index("{/* An export's start")]
    assert strip.count("ml-auto") == 1 and "{status}" in strip and "{t('Publish')}" in strip
    assert view.count("ml-auto") == 1
    # The Clips page and the Editor page read the same setup: WoopSocial first.
    setup = _read(UI / "lib" / "publishSetup.ts")
    assert "provider: wsReady ? 'woopsocial' : 'uploadpost'" in setup
    assert "readPublishSetup()" in _read(UI / "pages" / "ClipStudio.tsx") and "readPublishSetup()" in view
    # An export taken in is read into the shown project, so Publish follows it.
    lib = _read(UI / "lib" / "editor.ts")
    exported = lib[lib.index("bridge.onExport((answer) => {"):]
    assert "rereadShownProject(" in exported[:exported.index("bridge.getState()")]
    # The page's own overlay over the editor's area is on k2-editor.md's list.
    assert "PublishAllDialog" in _read(ROOT / "docs" / "editor-suite" / "api" / "k2-editor.md")


def test_publish_is_on_only_once_the_clips_video_is_the_edit(tmp_path):
    from test_ui_editor_fl2 import _opening

    got = _opening(tmp_path, """
      const s = m.publishState
      const clip = {id: 5, video_id: 'v'}
      const replaced = {result: 'replaced_clip'}
      return [
        s(null),
        s({kind: 'video', exports: [replaced]}),
        s({kind: 'file'}),
        s({kind: 'clip', clip, clip_gone: true, exports: [replaced]}),
        s({kind: 'clip', clip}),
        s({kind: 'clip', clip, exports: []}),
        s({kind: 'clip', clip, exports: [replaced]}),
        s({kind: 'clip', clip, clip_changed: true, exports: [replaced]}),
        s({kind: 'clip', clip, clip_changed: true, exports: [replaced, {result: 'kept_beside'}]}),
        s({kind: 'clip', clip, exports: [{result: 'kept_beside'}, replaced]}),
      ]""")
    assert got == ["none", "none", "none", "none", "export_first", "export_first", "ready", "clip_changed",
                   "kept_beside", "ready"]


def test_f1_regression_round_page_rules():
    """review-f1-final 4, 5 and 7 in the page's code."""
    page = _read(PAGE)
    # 4: entering the page again clears an old failed open (lib keeps it while
    # a project is shown or opening; tests/test_ui_editor_open.py runs it).
    router = page[page.index("export default function Editor("):]
    assert "clearOpenError()" in router[:router.index("return store.shownId")]
    # 5: "Same as the video" is told by the video's own size kept beside the
    # named shape, never by the shape alone.
    label = page[page.index("export function shapeLabel("):]
    label = label[:label.index("\n}\n")]
    assert "project.source_size" in label and "t('Same as the video')" in label
    assert "{shapeLabel(p)}" in page
    assert "source_size?: [number, number] | null" in _read(UI / "lib" / "api.ts")
    # 7: the Publish window's backdrop does not close it while it publishes.
    dialog = _read(UI / "components" / "PublishAllDialog.tsx")
    backdrop = dialog[dialog.index('role="dialog"'):]
    backdrop = backdrop[:backdrop.index("<div")]
    assert "onClick={onClose}" not in backdrop and "if (!busy) onClose()" in backdrop


# ---- KC: subtitles and dubbing in the editor, YouTube in the Publish window ------------------------


def test_other_languages_is_a_window_from_the_editor_strip():
    """KC (the owner's call): "Other languages…" sits next to Publish in the
    Editor page's strip, for clip projects only, and opens a window of its
    own: modal, the editor hidden while it is open, the keyboard inside it,
    Escape closes it and the focus goes back to the button. KF: the window
    makes language tabs in the editor; no video, style picker or preview."""
    view = _project_view(_read(PAGE))
    strip = view[view.index("{/* Publish and the project's state"):]
    strip = strip[:strip.index("{/* An export's start")]
    assert strip.index("{t('Other languages…')}") < strip.index("{t('Publish')}")
    assert strip.count("{publish !== 'none' && (") == 2      # clip projects whose clip is there
    assert "useEditorCover(languagesOpen)" in view
    assert re.search(r"<OtherLanguagesDialog\s+projectId=\{shownProject.id\}[^/]*onClose=\{\(\) => setLanguagesOpen\(false\)\}"
                     r"\s+returnFocus=\{languagesButton\}", view)
    window = _read(UI / "components" / "editor" / "OtherLanguagesDialog.tsx")
    assert 'role="dialog"' in window and 'aria-modal="true"' in window
    assert "useDialogFocus(" in window and "returnFocus" in window
    assert "if (!typing()) onClose()" in window       # Escape in a text field is the field's
    assert "makeLanguageTabs(" in window and "{t('Make language tabs')}" in window
    for gone in ("<video", "CaptionStyleControls", "LiveTextOverlay", "MultilingualExport"):
        assert gone not in window, gone
    for rel in ("MultilingualExport.tsx", "TranslationReview.tsx", "LiveTextOverlay.tsx"):
        assert not (UI / "components" / rel).exists(), rel
    # The Clips page gets no new button for it.
    assert "OtherLanguages" not in _read(UI / "pages" / "ClipStudio.tsx")
    assert "{t('Other languages…')}" not in _read(UI / "components" / "ClipEditor.tsx")


def test_a_window_over_a_dialog_keeps_its_own_keys():
    """The "changed in the editor" question can open over Other languages:
    its Tab and Escape are its own, not the window under it."""
    hook = _read(UI / "lib" / "useDialogFocus.ts")
    assert "!box.current.contains(target) &&" in hook and "target.closest('[aria-modal=\"true\"]')" in hook


def test_youtube_and_published_links_are_in_the_one_clip_publish_window():
    """KC: the one-clip Publish window (Clips page and Editor page) holds
    "YouTube (direct upload)" while it is switched on, and the clip's
    "Published links"; one clip can be published with only YouTube on."""
    dialog = _read(UI / "components" / "PublishAllDialog.tsx")
    # The frame picker runs over the file's own length (Quick edit's speed or cuts made some shorter).
    assert "duration={fileSeconds ?? clipSeconds(clips[0])}" in dialog
    assert "{single && youtube && ytStatus?.enabled && (" in dialog
    assert "{t('YouTube (direct upload)')}" in dialog
    assert "<YouTubePanel" in dialog and "pendingRender={null}" in dialog
    assert "<PublishedDiscordLinks key={clips[0].id} clipId={clips[0].id} />" in dialog
    assert "Open a clip and its Publish tab" not in dialog
    setup = _read(UI / "lib" / "publishSetup.ts")
    assert "api.youtubeStatus()" in setup and "canPublishOne" not in setup
    studio = _read(UI / "pages" / "ClipStudio.tsx")
    assert studio.count("youtube={publishSetup.youtube}") == 1
    view = _project_view(_read(PAGE))
    assert "providerReady={publishSetup.ready}" in view and "youtube={publishSetup.youtube}" in view
    # The thumbnail has its own frame picker: no playhead from Quick edit.
    thumb = _read(UI / "components" / "YouTubeThumbnail.tsx")
    assert "currentTime" not in thumb and "currentTime" not in _read(UI / "components" / "YouTubePanel.tsx")
    assert 'type="range"' in thumb and "{t('Use this frame')}" in thumb
    # The stills carry the file's version (an editor export keeps the clip's
    # id), and the slider stops before the end (no frame at the exact length).
    assert "api.clipFrameUrl(clipId, Math.min(stillAt, lastFrame), version)" in thumb
    assert "api.clipFrameUrl(clipId, at, version)" in thumb
    assert "const lastFrame = Math.max(0, Math.floor((duration - 0.1) * 10) / 10)" in thumb
    assert "max={lastFrame}" in thumb and "max={duration}" not in thumb
    assert "version={clip.media_version}" in _read(UI / "components" / "YouTubePanel.tsx")


def test_an_export_says_whether_the_caption_words_went_with_it():
    """KC: after an export becomes the clip's video, the notice says the
    caption words were saved for other languages, or that they were kept."""
    lib = _read(UI / "lib" / "editor.ts")
    case = lib[lib.index("case 'replaced_clip':"):lib.index("case 'kept_beside':")]
    assert "answer.captions === 'followed'" in case and "answer.captions === 'kept'" in case
    assert "t('Saved as this clip’s video. Other languages keep the old caption words.')" in case
    assert "captions?: 'followed' | 'kept'" in _read(UI / "lib" / "api.ts")


def test_the_clips_pages_publish_shows_like_the_editors():
    """KC, his ask "publishing in clips": the Clips page's Publish (card and
    side panel) and Publish all are always there, as the Editor page's
    Publish is. The setup is read again on each click, and with no account
    connected the same window says how to connect one."""
    studio = _read(UI / "pages" / "ClipStudio.tsx")
    assert "onPublish={() => void openPublish(() => setPublishOne(current))}" in studio
    assert "onPublish={() => void openPublish(() => setPublishOne(clip))}" in studio
    assert "onClick={() => void openPublish(() => setPublishing(true))}" in studio
    bar = studio[studio.index("onClick={() => void openPublish(() => setPublishing(true))}") - 400:]
    assert bar.index("Publish all (") < bar.index("{publishReady && (")   # Publish all is outside the rule
    opener = studio[studio.index("const openPublish = async"):]
    opener = opener[:opener.index("open()") + len("open()")]
    assert "setPublishSetup(await readPublishSetup())" in opener and "open()" in opener
    assert studio.count("providerReady={publishReady}") == 2
    dialog = _read(UI / "components" / "PublishAllDialog.tsx")
    assert "{!providerReady ? (" in dialog
    assert "t('To send these clips to your social accounts, set up WoopSocial or Upload-Post in Settings.')" in dialog
    assert "t('To send this clip to several platforms at once, set up WoopSocial or Upload-Post in Settings.')" in dialog


def test_the_clips_pages_schedule_follows_a_newly_connected_account():
    """Review r5 #6: Schedule shows once an account is connected, without a
    Publish click first: the setup is read when the page is shown and again
    when the window regains focus or becomes visible."""
    studio = _read(UI / "pages" / "ClipStudio.tsx")
    effect = studio[studio.index("// Which provider the windows send through (lib/publishSetup.ts): read"):]
    effect = effect[:effect.index("}, [])")]
    assert "readPublishSetup()" in effect and "setPublishSetup(setup)" in effect
    assert "window.addEventListener('focus', read)" in effect
    assert "document.addEventListener('visibilitychange', onVisible)" in effect
    assert "window.removeEventListener('focus', read)" in effect
    assert "document.removeEventListener('visibilitychange', onVisible)" in effect
    assert "{publishReady && (" in studio                     # Schedule still follows the setup
