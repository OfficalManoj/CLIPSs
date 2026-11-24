/** The Editor page's state, kept outside the page (editor suite K2, U2 and U4).
 *
 *  Which project is shown, the editor's own state as the main process reports
 *  it, a project being made, and the last notice. It lives in this module, not
 *  in the page component, so leaving the page and coming back shows the work
 *  exactly as it was, and a project being made keeps being made while the
 *  person is on another page.
 *
 *  Every way in (a Library clip's Edit on its card or in the Clips page's
 *  side panel, the page's own lists: Recent projects, "Edit a whole video"
 *  and "Edit a file from your PC") calls openInEditor() or
 *  openEditorProject(); App.tsx switches to the page on 'open-editor-page'.
 *  The Clips page's "Edit whole video" was removed in FL3, and Quick edit
 *  (the old editor) in KC. */
import { useEffect, useState } from 'react'
import { editorApi, EditorRouteError, type EditorOpenRequest, type EditorProject, type EditorTakeIn } from './api'
import { exportDestination, nextOpeningProgress, OPENING_MOST } from './editorOpening'
import { getExportFolder } from './exportFolder'
import { t } from './i18n'

export const OPEN_EDITOR_PAGE = 'open-editor-page'

/** What the main process says about the editor (ui/src/main/editor.ts). */
export interface EditorRuntime {
  running: boolean
  projectId: string
  visible: boolean
  focused: boolean
  dirty: boolean
  exporting: { fraction: number; stage: string } | null
  software: boolean
  /** '' | 'missing' | 'cannot_start' | 'no_project' */
  error: string
  /** '' | 'unexpected' | 'idle' */
  ended: string
  code: number | null
}

export interface EditorNotice {
  text: string
  tooltip?: string
  /** "Show in folder" for this project's latest recorded export. */
  revealProject?: string
  /** Something went wrong: said at once to a screen reader (role="alert"). */
  failure?: boolean
}

export interface EditorStore {
  /** The project shown on the page; null shows the lists. */
  shownId: string | null
  project: EditorProject | null
  /** A project being made or opened: its name and how far it is (and its
   *  id once known, so another project's progress is never shown for it). */
  opening: { name: string; progress: number; id?: string } | null
  /** Why the last open failed, in plain words. */
  openError: string
  runtime: EditorRuntime
  notice: EditorNotice | null
  /** The shown project's notes were shown once for this open. */
  notesSeen: boolean
}

let store: EditorStore = {
  shownId: null,
  project: null,
  opening: null,
  openError: '',
  runtime: {
    running: false,
    projectId: '',
    visible: false,
    focused: false,
    dirty: false,
    exporting: null,
    software: false,
    error: '',
    ended: '',
    code: null
  },
  notice: null,
  notesSeen: false
}
const listeners = new Set<(s: EditorStore) => void>()

function set(patch: Partial<EditorStore>): void {
  store = { ...store, ...patch }
  for (const fn of listeners) fn(store)
}

export function editorStore(): EditorStore {
  return store
}

export function useEditorStore(): EditorStore {
  const [value, setValue] = useState(store)
  useEffect(() => {
    listeners.add(setValue)
    setValue(store)
    return () => {
      listeners.delete(setValue)
    }
  }, [])
  return value
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

/** The plain words for a refused open. */
function openErrorText(e: unknown): string {
  if (e instanceof EditorRouteError) {
    if (e.reason === 'missing') return t('The editor is not installed with this copy of Clips Kitty.')
    if (e.status === 404) return t('That clip or video is no longer in the Library.')
    if (e.status === 400 && /look like a video/.test(e.message)) return t('That file doesn’t look like a video.')
    if (e.status === 503 && /could not read/.test(e.message)) {
      return t('Clips Kitty could not read the video’s file. Try again in a moment.')
    }
    if (e.status === 400) return t('Clips Kitty can’t open that file. Choose a video file.')
    if (e.status === 0) return t('Clips Kitty is still starting. Try again in a moment.')
  }
  return t('The project could not be opened.')
}

/** What the export answer means for the person, said in the page's strip. */
export function exportNotice(answer: EditorTakeIn): EditorNotice | null {
  switch (answer.result) {
    case 'replaced_clip':
      // No tooltip: the folder is Clips Kitty's own, nothing a person looks for (K-D21).
      // The captions' words go with it for other languages (KC), or say why not.
      if (answer.captions === 'followed') {
        return {
          text: t('Saved as this clip’s video, with your caption words.'),
          tooltip: t(
            'Other languages and the subtitles you make now use the words you typed in the editor. Translations made before this edit were removed, so translate again to use the new words.'
          )
        }
      }
      if (answer.captions === 'kept') {
        return {
          text: t('Saved as this clip’s video. Other languages keep the old caption words.'),
          tooltip: t(
            'The captions were moved, cut, split or deleted in the editor, or all emptied, so Clips Kitty could not match them to the clip’s caption lines. Subtitles in other languages are made from the words from before.'
          )
        }
      }
      return { text: t('Saved as this clip’s video.') }
    case 'kept_beside':
      return {
        text: t('This clip was changed meanwhile, so the export was saved next to it.'),
        tooltip: answer.path,
        revealProject: answer.project_id
      }
    case 'saved_to_folder':
      return {
        text: t('Saved to {folder}').replace('{folder}', answer.folder ?? ''),
        tooltip: answer.path,
        revealProject: answer.project_id
      }
    case 'failed':
      return { text: t('The export did not finish.'), tooltip: answer.error || undefined, failure: true }
    default:
      return null // cancelled: nothing to say
  }
}

/** The sentences of a project's notes (what is not carried over). */
export function noteSentences(codes: string[] | undefined): string[] {
  const words: Record<string, string> = {
    framing_baked: t('The AI’s framing is part of the video: it cannot be re-aimed in the editor.'),
    captions_plain: t('Captions are plain text in the editor: the lit-up word and the caption animation are not carried.'),
    font_default: t('The caption font is not on this PC for the editor, so the editor’s own font is used.'),
    music_duck: t('Music no longer dips under speech by itself; its volume is the one set.'),
    source_missing: t(
      'The original video is gone, so the clip’s finished file is used: its captions are part of the picture and its cuts cannot be taken back.'
    ),
    from_export: t(
      'This clip was already made in the editor and that project is gone, so its finished file is used: its captions are part of the picture and its cuts cannot be taken back.'
    ),
    watermark_still: t('A moving watermark stands still in the editor.'),
    music_missing: t('The clip’s music file is gone, so there is no music in the editor.')
  }
  return (codes ?? []).map((code) => words[code]).filter((s): s is string => !!s)
}

let openToken = 0

function gotoPage(): void {
  window.dispatchEvent(new CustomEvent(OPEN_EDITOR_PAGE))
}

/** A native question (drawn above the editor). The index of the button. */
export async function askNative(message: string, buttons: string[], detail?: string): Promise<number> {
  const bridge = window.studio?.editor
  if (!bridge) return window.confirm(message) ? 0 : 1
  return bridge.ask({ message, detail, buttons, defaultId: 0, cancelId: 0 })
}

/** "This clip was changed after this project was made" (made again, for
 *  example): open
 *  the project as it is (default), or start a new one from the clip. */
async function wantsFresh(project: EditorProject): Promise<boolean> {
  if (project.kind !== 'clip' || !project.clip_changed || project.clip_gone) return false
  const answer = await askNative(t('This clip was changed after this project was made.'), [
    t('Open the project'),
    t('Start a new project from the clip')
  ])
  return answer === 1
}

/** Wait for a project to be made, then open it on the page. */
async function openReady(first: EditorProject, token: number, mayRemake = true, restart = false): Promise<void> {
  let project = first
  const wasFailed = project.state === 'failed'
  set({ opening: { name: project.name, progress: openingProgress(project, restart), id: project.id }, openError: '' })
  // The editor is started while the project is made, so it is ready when the
  // project is (FL2 F1): hidden, with no project, until show() opens this one.
  if (project.state === 'building') void window.studio?.editor?.prepare?.()
  while (project.state === 'building') {
    await buildNews(1000)
    if (token !== openToken) return
    try {
      project = await editorApi.project(project.id)
    } catch (e) {
      if (token === openToken) set({ opening: null, openError: openErrorText(e) })
      return
    }
    if (token !== openToken) return
    set({ opening: { name: project.name, progress: openingProgress(project), id: project.id } })
  }
  if (token !== openToken) return
  // A project that had failed when it was asked for is made again by opening
  // it (the engine's choice 8), once; one that fails while it is made here
  // says why.
  if (project.state === 'failed' && (project.damaged || !wasFailed || !mayRemake)) {
    set({ opening: null, openError: project.message || t('The project could not be opened.') })
    return
  }
  let opened: EditorProject
  try {
    opened = await editorApi.reopen(project.id, await getExportFolder())
  } catch (e) {
    if (e instanceof EditorRouteError && e.reason === 'building') {
      await sleep(1000)
      if (token === openToken) await openReady({ ...project, state: 'building' }, token, false)
      return
    }
    if (token === openToken) set({ opening: null, openError: openErrorText(e) })
    return
  }
  // Every answer of a stopped or replaced open is left alone: it must not
  // start this open again, nor wipe the newer one with its failure.
  if (token !== openToken) return
  if (opened.state === 'building') {
    // Made again (it had failed): its bar starts again too (FL2 F3).
    await openReady(opened, token, false, true)
    return
  }
  if (opened.state !== 'ready') {
    set({ opening: null, openError: opened.message || t('The project could not be opened.') })
    return
  }
  const bridge = window.studio?.editor
  if (!bridge) {
    set({ opening: null, openError: t('The editor could not start.') })
    return
  }
  // The options first, then the last look at the token, then show at once:
  // "Projects" pressed while the options were read must stop this open.
  const options = await showOptions(opened)
  if (token !== openToken) return
  const shown = await bridge.show(opened.id, options)
  if (token !== openToken) {
    // Stopped while the editor opened it: an editor left with nothing shown
    // or being opened on the page is closed again, never left running
    // hidden. One that another open took over is that open's.
    if (!store.shownId && !store.opening) await bridge.close()
    return
  }
  if (!shown.ok && shown.error === 'exporting') {
    set({ opening: null, openError: t('The editor is exporting. Open another project when it has finished.') })
    return
  }
  const recovered = (opened.recovered ?? []).map(exportNotice).filter((n): n is EditorNotice => !!n)
  set({
    shownId: opened.id,
    project: opened,
    opening: null,
    openError: '',
    notesSeen: false,
    notice: recovered.length ? recovered[recovered.length - 1] : null
  })
}

/** Where the export folder is, and the line the editor's Export sheet shows
 *  under the name: what Clips Kitty will do with the file. */
export async function showOptions(
  project: EditorProject | null
): Promise<{ exportFolder: string; destination: string }> {
  const exportFolder = await getExportFolder()
  const destination = exportDestination(project, exportFolder, {
    replaces: t('Replaces this clip in the Library'),
    besideChanged: t('Saved next to this clip, which was changed since'),
    folder: t('Export folder: {folder}'),
    downloads: t('Goes to your Downloads folder')
  })
  return { exportFolder, destination }
}

/** The editor started early for a project being made (prepare) is closed
 *  again when that open ended without showing a project: it failed, or
 *  another open took its place. Never the editor of a shown project (the
 *  main process only closes one that has no project). */
function releasePrepared(token: number): void {
  if (token === openToken && store.opening === null) {
    void window.studio?.editor?.unprepare?.()
  }
}

/** The shown project read again when the page is shown again, so the
 *  editor's Export sheet says where the file goes now: a clip deleted while
 *  its project was open goes to the export folder (FL2 F4). Only the line
 *  changes; the editor keeps the project as it is. */
export async function refreshShownProject(): Promise<void> {
  const bridge = window.studio?.editor
  const id = store.shownId
  if (!bridge || !id || !store.runtime.running || store.runtime.projectId !== id) return
  let project: EditorProject
  try {
    project = await editorApi.project(id)
  } catch {
    return
  }
  if (store.shownId !== id) return
  set({ project })
  await bridge.show(id, await showOptions(project))
}

/** The shown project read again after one of its exports was taken in, so
 *  the page's Publish knows whether the clip's video is now the edit (FL3).
 *  Only the page's copy changes: the editor is not told anything. */
export async function rereadShownProject(projectId?: string): Promise<void> {
  const id = store.shownId
  if (!id || (projectId && projectId !== id)) return
  try {
    const project = await editorApi.project(id)
    if (store.shownId === id) set({ project })
  } catch {
    // Read again at the next open or export.
  }
}

/** The question asked before a project being exported is closed: leaving
 *  stops the export. True when the person chose to close it. */
async function closeWhileExporting(): Promise<boolean> {
  if (!store.runtime.exporting) return true
  const answer = await askNative(t('The editor is still exporting. Close the project and stop the export?'), [
    t('Keep exporting'),
    t('Close')
  ])
  return answer === 1
}

/** Another open starting while a project is shown (F1 desktop fix): the
 *  shown one is saved and closed the way "Projects" does it, so nothing of
 *  it (its editor, name, notices or Publish) stays on screen while the new
 *  one is made. Asked first while it exports. Null when the person kept
 *  exporting (the new open does not start), else the close under way
 *  (wrapped, so it is not waited for here: the page switches at once and
 *  the engine is asked meanwhile). */
type Leave = { closing: Promise<unknown> } | null
const NOTHING_TO_CLOSE: Leave = { closing: Promise.resolve() }
let leaving: Promise<Leave> | null = null
function leaveShownProject(): Promise<Leave> {
  // A second open while the first still asks (a double click) waits on the
  // same answer: one question, one close.
  if (!leaving) {
    leaving = (async (): Promise<Leave> => {
      if (!(await closeWhileExporting())) return null
      const closing = window.studio?.editor?.close()
      return { closing: closing ? closing.catch(() => undefined) : Promise.resolve() }
    })().finally(() => {
      leaving = null
    })
  }
  return leaving
}

/** The open is for the project already shown: it stays as it is (its
 *  editor, its undo history, an export under way), and only the page is
 *  shown. Never closed and started again. */
function keepShown(): void {
  gotoPage()
}

/** The page from the moment an open starts: no project shown, nothing of
 *  an earlier one (its notices, its notes), the opening bar at 0. */
function startOpening(): void {
  set({
    shownId: null,
    project: null,
    opening: { name: '', progress: 0 },
    openError: '',
    notice: null,
    notesSeen: false
  })
}

/** Open a clip, a Library video or a file in the editor: made first when
 *  there is no project for it yet. */
export async function openInEditor(req: EditorOpenRequest): Promise<void> {
  const shown = store.shownId
  // The shown project's own clip: it stays shown.
  if (shown && req.kind === 'clip' && store.project?.id === shown && store.project.clip?.id === req.clip_id) {
    keepShown()
    return
  }
  // A whole video or a file while a project is shown: the engine is asked
  // first, as its answer may be the shown project (kept) or another one.
  let early: Awaited<ReturnType<typeof editorApi.open>> | null = null
  if (shown && req.kind !== 'clip') {
    gotoPage()
    try {
      early = await editorApi.open(req)
    } catch (e) {
      if (store.shownId === shown) set({ notice: { text: openErrorText(e), failure: true } })
      return
    }
    if (store.shownId && early.project.id === store.shownId) {
      keepShown()
      return
    }
  }
  // Nothing awaited when no project is shown: the page switches at once.
  const left = store.shownId ? await leaveShownProject() : NOTHING_TO_CLOSE
  if (left === null) return
  const closing = left.closing
  const token = ++openToken
  gotoPage()
  startOpening()
  try {
    let answer = early ?? (await editorApi.open(req))
    await closing
    if (token !== openToken) return
    if (req.kind === 'clip' && !answer.created && (await wantsFresh(answer.project))) {
      answer = await editorApi.open({ ...req, fresh: true })
    }
    if (token !== openToken) return
    await openReady(answer.project, token)
  } catch (e) {
    if (token === openToken) set({ opening: null, openError: openErrorText(e) })
  }
  releasePrepared(token)
}

/** Open a project from the page's list (or from the "changed in the editor" question). */
export async function openEditorProject(id: string): Promise<void> {
  if (store.shownId && id === store.shownId) {
    keepShown()
    return
  }
  // Nothing awaited when no project is shown: the page switches at once.
  const left = store.shownId ? await leaveShownProject() : NOTHING_TO_CLOSE
  if (left === null) return
  const closing = left.closing
  const token = ++openToken
  gotoPage()
  startOpening()
  try {
    let project = await editorApi.project(id)
    await closing
    if (token !== openToken) return
    if (project.clip && (await wantsFresh(project))) {
      project = (await editorApi.open({ kind: 'clip', clip_id: project.clip.id, fresh: true })).project
    }
    if (token !== openToken) return
    await openReady(project, token)
  } catch (e) {
    if (token === openToken) set({ opening: null, openError: openErrorText(e) })
  }
  releasePrepared(token)
}

/** Start the editor again on the shown project (Try again, Open again, or
 *  coming back after it was closed while hidden for a long time). */
export async function ensureEditorShown(): Promise<void> {
  const bridge = window.studio?.editor
  const id = store.shownId
  if (!bridge || !id) return
  set({ runtime: { ...store.runtime, error: '', ended: '' } })
  // Read again: the clip may have been deleted since (FL2 F4).
  const project = await editorApi.project(id).catch(() => store.project)
  if (store.shownId !== id) return
  if (project && project !== store.project) set({ project })
  const options = await showOptions(project)
  // Looked at again right before show: an open of another project that
  // started meanwhile must not get this one's editor started under it.
  if (store.shownId !== id) return
  await bridge.show(id, options)
}

/** Try again after a start that failed or a software-only start. */
export async function restartEditor(): Promise<void> {
  const bridge = window.studio?.editor
  if (!bridge) return
  if (store.runtime.running) await bridge.close()
  await ensureEditorShown()
}

/** "Projects": the editor saves and leaves, and the page shows the lists.
 *  While it exports, the person is asked first: leaving stops the export. */
export async function closeEditorProject(): Promise<void> {
  if (!(await closeWhileExporting())) return
  ++openToken
  // Nothing of the project closed stays: its notices go with it (F1 U-3).
  set({ shownId: null, project: null, opening: null, openError: '', notice: null, notesSeen: false })
  await window.studio?.editor?.close()
}

/** A notice in the strip from the page itself (FL3: Publish could not open). */
export function showEditorNotice(notice: EditorNotice): void {
  set({ notice })
}

export function clearEditorNotice(): void {
  set({ notice: null })
}

/** The Editor page entered again (from the sidebar or another page): an
 *  earlier failed open's reason goes, so the lists show again, never a
 *  failure the person already left. Not while a project is shown or opening. */
export function clearOpenError(): void {
  if (!store.shownId && !store.opening && store.openError) set({ openError: '' })
}

export function markNotesSeen(): void {
  set({ notesSeen: true })
}

let bridged = false
/** Listen to the main process once (App.tsx calls this at start). */
export function connectEditorBridge(): void {
  const bridge = window.studio?.editor
  if (bridged || !bridge) return
  bridged = true
  bridge.onState((s) => set({ runtime: { ...store.runtime, ...(s as Partial<EditorRuntime>) } }))
  bridge.onExport((answer) => {
    const notice = exportNotice(answer as unknown as EditorTakeIn)
    // Only the shown project's: a late answer of a project being closed
    // never shows on the next one while it opens.
    if (notice && (answer as unknown as EditorTakeIn).project_id === store.shownId) set({ notice })
    // The shown project's exports changed: Publish follows them (FL3).
    void rereadShownProject((answer as unknown as EditorTakeIn).project_id)
  })
  void bridge.getState().then((s) => {
    if (s) set({ runtime: { ...store.runtime, ...(s as Partial<EditorRuntime>) } })
  })
  // The main process asks before quitting during an export; it has no t(),
  // so its words come from here, in the app's language.
  const sendWords = (): void => {
    void bridge.setWords?.({
      message: t('The editor is still exporting. Quit and stop the export?'),
      keep: t('Keep exporting'),
      quit: t('Quit')
    })
  }
  sendWords()
  window.addEventListener('app-language-changed', sendWords)
}

/** A project's progress from the engine's event stream. */
export function onEditorProjectEvent(e: { id?: string; state?: string; progress?: number }): void {
  if (!store.opening || !e.id || (store.opening.id && store.opening.id !== e.id)) return
  if (typeof e.progress === 'number') {
    set({ opening: { ...store.opening, progress: openingProgress({ id: e.id, progress: e.progress }) } })
  }
  if (e.state && e.state !== 'building') newsWaiter?.()
}

let newsWaiter: (() => void) | null = null

/** A second, or less when the engine says a project being made is done:
 *  the page then asks for it at once rather than at its next look. */
function buildNews(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms)
    function done(): void {
      clearTimeout(timer)
      if (newsWaiter === done) newsWaiter = null
      resolve()
    }
    newsWaiter = done
  })
}

export { OPENING_MOST }

/** How far the project being opened is, for its bar: never less than it
 *  already said for that project (the event stream and the page's own
 *  asking can arrive out of order), never 100% while it is opening, and
 *  from the start again when its build restarts (lib/editorOpening.ts). */
export function openingProgress(
  project: { id?: string; state?: string; progress?: number },
  restart = false
): number {
  const before = store.opening && (!store.opening.id || store.opening.id === project.id) ? store.opening.progress : 0
  return nextOpeningProgress(before, project, restart)
}

let appPointerAt = 0
/** A click or focus in the app's own page (App.tsx): the keys go back to it.
 *  A focus the page fires only because the window came back does not take
 *  them from the editor while they are given back to it (FL2 D1). */
export function noteAppPointer(why: 'pointer' | 'focus' = 'pointer'): void {
  appPointerAt = Date.now()
  if (store.runtime.running) void window.studio?.editor?.blur(why)
}

/** Whether the keys left the editor because of a click in the app. */
export function appPointerRecent(): boolean {
  return Date.now() - appPointerAt < 1500
}
