/** The Editor page (editor suite K2, U2 to U5).
 *
 *  Clips Kitty Editor (a separate program, based on Concat) is drawn by the
 *  main process INSIDE this window, over this page's editor area. With no
 *  project shown the page lists recent projects and the two ways to start one
 *  here; with a project shown it is a 36 px strip of the app's own and the
 *  area, which the editor covers. A project being opened shows that strip
 *  and area at once, never the lists (FL3: "Open in editor" goes straight
 *  to the clip).
 *
 *  The editor is a native window above the web page, so nothing of the app's
 *  own may be drawn into the area while it shows: things that would hold the
 *  cover (lib/editorCover.ts), questions are native dialogs, notices go in
 *  the strip. The page's state lives in lib/editor.ts, so leaving the page
 *  and coming back shows the work as it was. */
import { useEffect, useRef, useState } from 'react'
import PublishAllDialog from '../components/PublishAllDialog'
import OtherLanguagesDialog from '../components/editor/OtherLanguagesDialog'
import { api, editorApi, type EditorProject, type EditorShape } from '../lib/api'
import {
  appPointerRecent,
  clearEditorNotice,
  clearOpenError,
  closeEditorProject,
  editorStore,
  ensureEditorShown,
  markNotesSeen,
  noteSentences,
  openEditorProject,
  openInEditor,
  refreshShownProject,
  rereadShownProject,
  restartEditor,
  showEditorNotice,
  useEditorStore
} from '../lib/editor'
import { useEditorCover, useEditorCoverHolds } from '../lib/editorCover'
import { publishState } from '../lib/editorOpening'
import { t } from '../lib/i18n'
import { readPublishSetup, type PublishSetup } from '../lib/publishSetup'
import { useEvents } from '../lib/useEvents'
import type { Clip, StudioEvent, Video } from '../lib/types'

/** "Same as the video" first, and the default (FL3): a video starts in its
 *  own shape; the other four are there to change it. */
export const SHAPES: { value: EditorShape; label: string }[] = [
  { value: 'source', label: 'Same as the video' },
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
  { value: '4:5', label: '4:5' }
]

/** The shape select, shared by both ways to start a project here. */
export function ShapeSelect({
  value,
  onChange,
  id
}: {
  value: EditorShape
  onChange: (shape: EditorShape) => void
  id?: string
}): JSX.Element {
  return (
    <select
      id={id}
      className="input !w-44 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value as EditorShape)}
      aria-label={t('Shape')}
      title={t(
        'The shape of the video you make. Same as the video keeps its own shape. Or pick 16:9 wide, 9:16 tall, 1:1 square or 4:5 portrait.'
      )}
    >
      {SHAPES.map((s) => (
        <option key={s.value} value={s.value}>
          {s.value === 'source' ? t('Same as the video') : s.label}
        </option>
      ))}
    </select>
  )
}

/** A project's shape as the shape box says it. A project made with "Same
 *  as the video" is kept under its named shape plus the video's own size
 *  (source_size), so that size is what tells it from the same shape picked
 *  by hand (F1 N-2). */
export function shapeLabel(project: Pick<EditorProject, 'shape' | 'source_size'>): string {
  if (project.shape === 'source' || project.source_size) return t('Same as the video')
  return project.shape ?? ''
}

const KIND_ICON: Record<EditorProject['kind'], { icon: string; label: string }> = {
  clip: { icon: '✂', label: 'A clip' },
  video: { icon: '▭', label: 'A whole video' },
  file: { icon: '⇪', label: 'A file from your PC' }
}

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

/** Recent projects, "Edit a whole video", "Edit a file from your PC". */
function ProjectLists(): JSX.Element {
  const store = useEditorStore()
  const [projects, setProjects] = useState<EditorProject[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState('')
  const [videoShape, setVideoShape] = useState<EditorShape>('source')
  const [fileShape, setFileShape] = useState<EditorShape>('source')
  const [listError, setListError] = useState('')

  const load = (): void => {
    editorApi
      .projects()
      .then((r) => {
        setProjects(r.projects)
        setListError('')
      })
      .catch(() => setListError(t('The projects could not be read.')))
  }
  useEffect(() => {
    load()
    api
      .videos()
      .then((v) => {
        setVideos(v)
        if (v.length) setVideoId((current) => current || v[0].video_id)
      })
      .catch(() => setVideos([]))
  }, [])
  useEvents((e: StudioEvent) => {
    if ((e.type as string) === 'editor_project' && e.state !== 'building') load()
  })
  // A project that finished being made or opening shows at the top.
  useEffect(load, [store.opening === null])

  const remove = async (p: EditorProject): Promise<void> => {
    if (!window.confirm(t('Delete this project? Videos you exported from it stay.'))) return
    try {
      await editorApi.remove(p.id)
    } catch (e) {
      const reason = (e as { reason?: string }).reason
      setListError(
        reason === 'open_in_editor'
          ? t('This project is open in the editor. Close it first.')
          : reason === 'building'
            ? t('This project is still being made.')
            : t('The project could not be deleted.')
      )
    }
    load()
  }

  const pickFile = async (): Promise<void> => {
    // No clips are made on this path: the dialog says so (FL2 D6).
    const path = await window.studio.pickVideoFile(t('Choose a video to edit'))
    if (path) void openInEditor({ kind: 'file', path, shape: fileShape })
  }

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <h2 className="text-2xl font-bold">{t('Editor')}</h2>
      {/* A project being opened, or one that could not be opened, shows in
          the strip (ProjectView), never here (F1). Always there, so a notice that appears is read out (a live region
          added together with its text is not). */}
      <p role="status" className="text-sm text-accent empty:hidden" title={store.notice?.tooltip}>
        {store.notice && !store.notice.failure ? store.notice.text : null}
      </p>
      <p role="alert" className="text-sm text-amber-400 empty:hidden" title={store.notice?.tooltip}>
        {store.notice?.failure ? store.notice.text : null}
      </p>

      <section className="card space-y-2" aria-labelledby="editor-recent">
        <h3 id="editor-recent" className="font-semibold">
          {t('Recent projects')}
        </h3>
        {listError && <p className="text-sm text-amber-400">{listError}</p>}
        {projects.length === 0 ? (
          <p className="text-sm text-muted">{t('No projects yet.')}</p>
        ) : (
          <ul className="divide-y divide-raised/60">
            {projects.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-1.5">
                <span aria-hidden title={t(KIND_ICON[p.kind]?.label ?? 'A clip')} className="w-5 text-center text-muted">
                  {KIND_ICON[p.kind]?.icon ?? '✂'}
                </span>
                <button
                  type="button"
                  className="flex-1 min-w-0 text-left text-sm hover:text-accent truncate disabled:opacity-60"
                  onClick={() => void openEditorProject(p.id)}
                  disabled={p.state === 'building' || !!p.damaged}
                  title={p.state === 'failed' ? p.message : t(KIND_ICON[p.kind]?.label ?? 'A clip')}
                >
                  {p.name || t('Untitled project')}
                  {/* Two projects of one video in different shapes have the
                      same name: the shape tells them apart (F1 N-2). */}
                  {p.kind !== 'clip' && p.shape && <span className="text-muted"> · {shapeLabel(p)}</span>}
                  {p.state === 'failed' && <span className="text-amber-400"> · {t('Not finished')}</span>}
                  {p.clip_gone && <span className="text-muted"> · {t('Clip deleted')}</span>}
                </button>
                {p.state === 'building' ? (
                  <div className="w-24 h-1.5 rounded bg-raised overflow-hidden" aria-label={t('Being made')}>
                    <div className="h-full bg-accent" style={{ width: `${Math.round((p.progress || 0) * 100)}%` }} />
                  </div>
                ) : (
                  <span className="text-xs text-muted tabular-nums shrink-0">{when(p.last_opened_at)}</span>
                )}
                <button
                  type="button"
                  className="text-xs text-muted hover:text-red-400 shrink-0"
                  onClick={() => void remove(p)}
                  disabled={p.state === 'building'}
                  aria-label={t('Delete project')}
                  title={t('Delete project')}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card space-y-2" aria-labelledby="editor-whole">
        <h3 id="editor-whole" className="font-semibold">
          {t('Edit a whole video')}
        </h3>
        <div className="flex flex-wrap gap-2 items-center">
          <select
            className="input !w-80 text-sm"
            value={videoId}
            onChange={(e) => setVideoId(e.target.value)}
            aria-label={t('Library video')}
            disabled={videos.length === 0}
          >
            {videos.length === 0 && <option value="">{t('No videos yet')}</option>}
            {videos.map((v) => (
              <option key={v.video_id} value={v.video_id}>
                {v.title || v.video_id}
              </option>
            ))}
          </select>
          <ShapeSelect value={videoShape} onChange={setVideoShape} />
          <button
            type="button"
            className="btn-accent !py-1.5 text-sm"
            disabled={!videoId || store.opening !== null}
            onClick={() => void openInEditor({ kind: 'video', video_id: videoId, shape: videoShape })}
          >
            {t('Open')}
          </button>
        </div>
      </section>

      <section className="card space-y-2" aria-labelledby="editor-file">
        <h3 id="editor-file" className="font-semibold">
          {t('Edit a file from your PC')}
        </h3>
        <div className="flex flex-wrap gap-2 items-center">
          <ShapeSelect value={fileShape} onChange={setFileShape} />
          <button
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            disabled={store.opening !== null}
            onClick={() => void pickFile()}
            title={t('Open a video file in the editor. Nothing is added to the Library and no AI runs.')}
          >
            {t('Choose a file…')}
          </button>
        </div>
      </section>
    </div>
  )
}

/** The state at the strip's right: "Saved", "Saving…", "Exporting 42%". */
function stateText(store: ReturnType<typeof editorStore>): string {
  const rt = store.runtime
  // Nothing shown yet (a project being opened): what the editor said of an
  // earlier project is not this one's (F1 U-2).
  if (!rt.running || !store.shownId) return ''
  if (rt.exporting) return `${t('Exporting')} ${Math.round(rt.exporting.fraction * 100)}%`
  return rt.dirty ? t('Saving…') : t('Saved')
}

/** What a screen reader is told of an export: its start and each quarter,
 *  not every half second of it. Empty when no export runs. */
export function exportAnnouncement(store: ReturnType<typeof editorStore>): string {
  const exporting = store.runtime.running ? store.runtime.exporting : null
  if (!exporting) return ''
  const quarter = Math.min(3, Math.floor(Math.max(0, exporting.fraction) * 4)) * 25
  return `${t('Exporting')} ${quarter}%`
}

/** A project shown: the strip and the area the editor covers. */
function ProjectView({
  approvals,
  onShowApprovals
}: {
  approvals: number
  onShowApprovals: () => void
}): JSX.Element {
  const store = useEditorStore()
  const holds = useEditorCoverHolds()
  // Publish (FL3): the same one-clip window as the Clips page's, through the
  // same provider, and on under the same rule: it opens whether or not a
  // provider is set up, and the window itself says what is (review fix S1,
  // F1 U-4). The editor is hidden while it is open (the cover rule).
  const [publishSetup, setPublishSetup] = useState<PublishSetup | null>(null)
  const [publishClip, setPublishClip] = useState<Clip | null>(null)
  useEditorCover(publishClip !== null)
  // Other languages (KF): language tabs for the shown clip's project, in a
  // window of its own; the editor is hidden while it is open.
  const [languagesOpen, setLanguagesOpen] = useState(false)
  useEditorCover(languagesOpen)
  const area = useRef<HTMLDivElement>(null)
  const projectsButton = useRef<HTMLButtonElement>(null)
  const publishButton = useRef<HTMLButtonElement>(null)
  const languagesButton = useRef<HTMLButtonElement>(null)
  const rt = store.runtime
  const bridge = window.studio.editor

  // Where the editor goes: the area's rectangle in physical pixels, at most
  // once per frame, on every change of size, place or screen scale.
  useEffect(() => {
    const el = area.current
    if (!el || !bridge) return
    let frame = 0
    const send = (): void => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const r = el.getBoundingClientRect()
        const dpr = window.devicePixelRatio || 1
        void bridge.setBounds({
          x: Math.floor(r.left * dpr),
          y: Math.floor(r.top * dpr),
          width: Math.max(1, Math.round(r.width * dpr)),
          height: Math.max(1, Math.round(r.height * dpr))
        })
      })
    }
    send()
    const observer = new ResizeObserver(send)
    observer.observe(el)
    if (el.parentElement) observer.observe(el.parentElement)
    window.addEventListener('resize', send)
    let media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
    const onScale = (): void => {
      media.removeEventListener('change', onScale)
      media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
      media.addEventListener('change', onScale)
      send()
    }
    media.addEventListener('change', onScale)
    const stopMeasure = bridge.onMeasure(send)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', send)
      media.removeEventListener('change', onScale)
      stopMeasure()
    }
  }, [bridge, store.shownId])

  // Shown only while this page is up, a project is shown and nothing of the
  // app's own is open over the area. Hidden when the page is left: the
  // editor and its work stay as they are.
  const wanted = !!store.shownId && holds === 0
  useEffect(() => {
    void bridge?.setVisible(wanted)
  }, [bridge, wanted])
  useEffect(() => () => void bridge?.setVisible(false), [bridge])

  // Started again when it is not running (closed after a while hidden), but
  // not after an error or a crash: the page then says so and offers to.
  useEffect(() => {
    if (store.shownId && !rt.running && !rt.error && rt.ended !== 'unexpected') void ensureEditorShown()
  }, [store.shownId, rt.running, rt.error, rt.ended])

  // Coming back to the page: the shown project is read again, so the Export
  // sheet's line follows a clip deleted meanwhile (FL2 F4).
  useEffect(() => {
    void refreshShownProject()
  }, [])

  // A click in the editor takes the keys: no button of the page stays
  // focused (Space would press it). Ctrl+F6 in the editor gives them back,
  // and focus goes to "Projects". (Switching to another program and back
  // is not reported as the editor letting go: the main process gives the
  // keys back to the editor then, FL2 D1.)
  const wasFocused = useRef(false)
  useEffect(() => {
    if (rt.focused) {
      ;(document.activeElement as HTMLElement | null)?.blur?.()
    } else if (wasFocused.current && !appPointerRecent()) {
      // The keys came back without a click in the app: Ctrl+F6.
      projectsButton.current?.focus()
    }
    wasFocused.current = rt.focused
  }, [rt.focused])

  // Ctrl+F6 in the app's page gives the keys to the editor again, the way
  // back from Ctrl+F6 in the editor (FL2 D1), while it is shown. Given when
  // F6 is let go: an F6 still down would reach the editor, which takes
  // Ctrl+F6 as leaving again.
  const editorUp = wanted && rt.visible
  useEffect(() => {
    if (!editorUp) return
    let pressed = false
    const onDown = (e: KeyboardEvent): void => {
      if (e.key !== 'F6') return
      pressed = e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey
      if (pressed) e.preventDefault()
    }
    const onUp = (e: KeyboardEvent): void => {
      if (e.key !== 'F6' || !pressed) return
      pressed = false
      e.preventDefault()
      void bridge?.focus()
    }
    document.addEventListener('keydown', onDown)
    document.addEventListener('keyup', onUp)
    return () => {
      document.removeEventListener('keydown', onDown)
      document.removeEventListener('keyup', onUp)
    }
  }, [bridge, editorUp])

  const shownProject = store.shownId && store.project?.id === store.shownId ? store.project : null
  const publish = publishState(shownProject)
  // Why Publish is off, or null when it is on. Off only for the clip's own
  // state: the publishing setup is read when it is clicked.
  const exportWhy: string | null = rt.exporting
    ? t('Wait for the export to finish.')
    : publish === 'export_first'
      ? t('Export first: Publish sends the clip in your Library.')
      : publish === 'kept_beside'
        ? t(
            'Your last export was saved next to this clip, not as its video, because the clip was changed meanwhile. Publish would send the other video, so it is off.'
          )
        : publish === 'clip_changed'
          ? t(
              'This clip was changed after your export, so its video in your Library is not your edit. Publish would send that video, so it is off.'
            )
          : null
  const publishOff = exportWhy
  const openPublish = async (): Promise<void> => {
    // Off (aria-disabled, so Tab still reaches it and its reason is read
    // out): the click does nothing (F1 U-6).
    if (publishOff !== null) return
    const id = shownProject?.id
    const clipId = shownProject?.clip?.id
    const videoId = shownProject?.clip?.video_id
    if (!id || clipId === undefined || !videoId) return
    // Read again first: the clip may have changed since the strip was drawn.
    await rereadShownProject(id)
    const now = editorStore()
    if (now.shownId !== id || publishState(now.project) !== 'ready') return
    let clips: Clip[]
    try {
      clips = await api.clips(videoId)
    } catch {
      // Not read (Clips Kitty busy or starting): the clip may well be there.
      if (editorStore().shownId === id) {
        showEditorNotice({ text: t('Clips Kitty could not read the clip. Try again in a moment.'), failure: true })
      }
      return
    }
    if (editorStore().shownId !== id) return
    const clip = clips.find((c) => c.id === clipId)
    if (clip) {
      // Through the provider set up now, as the Clips page's window is.
      const setup = await readPublishSetup()
      if (editorStore().shownId !== id) return
      setPublishSetup(setup)
      setPublishClip(clip)
      return
    }
    showEditorNotice({ text: t('This clip is no longer in the Library.'), failure: true })
    // Read again, so the project says its clip is gone and Publish goes away.
    await rereadShownProject(id)
  }

  // Other languages (KF): there for a clip project whose clip is there (as
  // Publish is). Each language becomes a tab of this project.
  const openLanguages = (): void => {
    if (shownProject?.id) setLanguagesOpen(true)
  }

  const notes = noteSentences(store.project?.notes)
  const showNotes = notes.length > 0 && !store.notesSeen
  const status = stateText(store)
  const announced = exportAnnouncement(store)
  const covered = holds > 0

  let message: { text: string; tooltip?: string; button: string; action: () => void } | null = null
  // While a project is being opened (none shown yet) the area only says
  // "Opening the editor…": what the editor said of an earlier run is not news.
  if (!store.shownId) {
    // An open that failed says why in the strip, and here the way back (F1 F-1).
    message =
      store.openError && !store.opening
        ? {
            text: t('Go back to your projects to open another one.'),
            button: `← ${t('Projects')}`,
            action: () => void closeEditorProject()
          }
        : null
  } else if (rt.software) {
    message = {
      text: t('The editor cannot use this PC’s graphics card, so it cannot be shown here.'),
      tooltip: t('Updating the graphics driver usually fixes this.'),
      button: t('Try again'),
      action: () => void restartEditor()
    }
  } else if (rt.error === 'cannot_start' || rt.error === 'missing' || rt.error === 'no_project') {
    message = {
      text: rt.error === 'missing' ? t('The editor is not installed with this copy of Clips Kitty.') : t('The editor could not start.'),
      tooltip: t('If it keeps happening, restart Clips Kitty.'),
      button: t('Try again'),
      action: () => void restartEditor()
    }
  } else if (rt.ended === 'unexpected') {
    message = {
      text: t('The editor closed unexpectedly. Your work is saved as you go.'),
      button: t('Open again'),
      action: () => void ensureEditorShown()
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="h-9 shrink-0 flex items-center gap-3 px-3 border-b border-raised/60 bg-surface text-sm">
        <button
          ref={projectsButton}
          type="button"
          className="px-2 py-0.5 rounded-md bg-raised hover:bg-raised/70"
          onClick={() => void closeEditorProject()}
          title={
            store.shownId
              ? t('Save and close this project')
              : store.opening
                ? t('Stop opening it and go back to your projects. A project being made is still made and stays in Recent projects.')
                : t('Back to your projects')
          }
        >
          ← {t('Projects')}
        </button>
        {store.shownId && (
          <span className="font-medium truncate max-w-[16rem]" title={store.project?.name}>
            {store.project?.name}
          </span>
        )}
        {store.opening && (
          <span className="flex items-center gap-2 min-w-0 text-muted" role="status">
            <span className="truncate">
              {store.opening.name
                ? t('Getting “{name}” ready…').replace('{name}', store.opening.name)
                : t('Getting the project ready…')}{' '}
              {Math.round(store.opening.progress * 100)}%
            </span>
            <span className="w-24 h-1.5 shrink-0 rounded bg-raised overflow-hidden" aria-hidden="true">
              <span
                className="block h-full bg-accent transition-all"
                style={{ width: `${Math.round(Math.min(1, Math.max(0.02, store.opening.progress)) * 100)}%` }}
              />
            </span>
          </span>
        )}
        {!store.shownId && !store.opening && store.openError && (
          <span role="alert" className="text-amber-400 truncate" title={store.openError}>
            {store.openError}
          </span>
        )}
        {showNotes && (
          <span className="text-muted truncate" title={notes.join('\n')}>
            {t('Some things from the clip are not carried over.')}{' '}
            <button
              type="button"
              className="underline"
              onClick={markNotesSeen}
              aria-label={t('Dismiss: {notice}').replace('{notice}', t('Some things from the clip are not carried over.'))}
            >
              ✕
            </button>
          </span>
        )}
        {/* The notice's live regions are always there, so a notice that
            appears (an export taken in) is read out: role="status", or
            role="alert" when something went wrong. They are also what shows
            it, so its words are there once (F1 U-5). */}
        <span role="status" className="text-accent truncate empty:hidden" title={store.notice?.tooltip}>
          {store.notice && !store.notice.failure ? store.notice.text : ''}
        </span>
        <span role="alert" className="text-amber-400 truncate empty:hidden" title={store.notice?.tooltip}>
          {store.notice?.failure ? store.notice.text : ''}
        </span>
        {store.notice && (
          <span className="shrink-0">
            {store.notice.revealProject && (
              <button
                type="button"
                className="ml-2 underline"
                onClick={() => void window.studio.editor?.reveal(store.notice?.revealProject ?? '', -1)}
              >
                {t('Show in folder')}
              </button>
            )}{' '}
            <button
              type="button"
              className="underline text-muted"
              onClick={clearEditorNotice}
              aria-label={t('Dismiss: {notice}').replace('{notice}', store.notice.text)}
            >
              ✕
            </button>
          </span>
        )}
        {/* Only with a project shown: during an open the cards themselves are
            up (App.tsx draws them while no project is shown). */}
        {store.shownId && approvals > 0 && (
          <button type="button" className="px-2 py-0.5 rounded-md bg-accent/20 text-accent shrink-0" onClick={onShowApprovals}>
            {t('Clip approvals')} ({approvals})
          </button>
        )}
        {/* Publish and the project's state together at the right (mockup 03). */}
        <span className="ml-auto flex items-center gap-3 shrink-0">
          {publish !== 'none' && (
            <button
              ref={languagesButton}
              type="button"
              className="px-2 py-0.5 rounded-md bg-raised hover:bg-raised/70"
              onClick={openLanguages}
              title={t(
                'Make a tab in this project for each language, with the captions translated and, if you want, the voice dubbed. Edit and export each tab like the rest of the editor.'
              )}
            >
              {t('Other languages…')}
            </button>
          )}
          {publish !== 'none' && (
            <button
              ref={publishButton}
              type="button"
              className="px-2 py-0.5 rounded-md bg-accent/20 text-accent hover:bg-accent/30 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed"
              onClick={() => void openPublish()}
              aria-disabled={publishOff !== null}
              title={publishOff || t('Send this clip to your social accounts, as you last exported it.')}
            >
              ↗ {t('Publish')}
            </button>
          )}
          <span className="text-muted">{status}</span>
        </span>
        {/* An export's start and each quarter, not every poll of it. */}
        <span className="sr-only" aria-live="polite">
          {announced}
        </span>
      </div>
      <div
        ref={area}
        // Behind a window of the app's own (Publish), Tab never reaches it.
        tabIndex={covered ? -1 : 0}
        role="application"
        aria-label={t('Editor. Press Enter to use it, Ctrl+F6 to come back.')}
        className="flex-1 min-h-0 relative bg-base outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            void bridge?.focus()
          }
        }}
      >
        {message ? (
          <div className="absolute inset-0 grid place-items-center p-6">
            <div className="text-center space-y-3 max-w-md">
              <p title={message.tooltip}>{message.text}</p>
              <button type="button" className="btn-accent !py-1.5 text-sm" onClick={message.action} title={message.tooltip}>
                {message.button}
              </button>
            </div>
          </div>
        ) : (
          (!store.shownId || !rt.visible) &&
          !covered && (
            <p className="absolute inset-0 grid place-items-center text-muted text-sm" role="status">
              {t('Opening the editor…')}
            </p>
          )
        )}
      </div>
      {publishClip && publishSetup && (
        <PublishAllDialog
          clips={[publishClip]}
          provider={publishSetup.provider}
          providerReady={publishSetup.ready}
          youtube={publishSetup.youtube}
          onClose={() => setPublishClip(null)}
          returnFocus={publishButton}
        />
      )}
      {languagesOpen && shownProject && (
        <OtherLanguagesDialog
          projectId={shownProject.id}
          name={shownProject.name}
          clipId={shownProject.clip?.id}
          onClose={() => setLanguagesOpen(false)}
          returnFocus={languagesButton}
        />
      )}
    </div>
  )
}

export default function Editor({
  approvals,
  onShowApprovals
}: {
  approvals: number
  onShowApprovals: () => void
}): JSX.Element {
  const store = useEditorStore()
  // Entered again (the sidebar, another page): an old failed open's reason
  // goes, so the page never stays on a failure the person left (F1).
  useEffect(() => {
    clearOpenError()
  }, [])
  // A project being opened goes straight to its strip and area (FL3): the
  // lists show only with nothing shown or being opened. An open that failed
  // says why in the area, with "Projects" as the way back (F1).
  return store.shownId || store.opening || store.openError ? (
    <ProjectView approvals={approvals} onShowApprovals={onShowApprovals} />
  ) : (
    <ProjectLists />
  )
}
