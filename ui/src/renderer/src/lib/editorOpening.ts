/** Small rules of the Editor page with no imports, so the tests run them
 *  under Node as they are (tests/test_ui_editor_fl2.py). lib/editor.ts uses
 *  them. */

/** The most the bar says before the editor shows the project: a project is
 *  "ready" (1.0) a moment before the editor has drawn it. */
export const OPENING_MOST = 0.99

/** How far the project being opened is, for its bar.
 *
 *  - Never less than it already said for that project (`before`): the event
 *    stream and the page's own asking can arrive out of order.
 *  - Never 100% while it is opening.
 *  - A build that failed says nothing about how far the next one is: its
 *    progress counts as none, so reopening it starts the bar from the start.
 *  - `restart`: the build started again (a failed project made again), so
 *    what the bar said before is forgotten (FL2 F3: it used to sit at the
 *    failed build's height until the new build passed it). */
export function nextOpeningProgress(
  before: number,
  project: { state?: string; progress?: number },
  restart = false
): number {
  const said = restart ? 0 : before || 0
  const now = project.state === 'failed' ? 0 : project.progress || 0
  return Math.min(OPENING_MOST, Math.max(said, now))
}

export interface DestinationWords {
  replaces: string
  besideChanged: string
  /** With {folder} in it. */
  folder: string
  downloads: string
}

/** The line the editor's Export sheet shows under the name: where Clips
 *  Kitty puts the file. A clip project replaces its clip in the Library
 *  (or is saved beside it when the clip was changed since); once the clip
 *  is gone, or for a whole video or a file, it goes to the export folder
 *  (FL2 F4: the line has to change when the clip is deleted while its
 *  project is open). */
export function exportDestination(
  project: { kind?: string; clip_gone?: boolean; clip_changed?: boolean } | null,
  exportFolder: string,
  words: DestinationWords
): string {
  if (project?.kind === 'clip' && !project.clip_gone) {
    return project.clip_changed ? words.besideChanged : words.replaces
  }
  return exportFolder ? words.folder.replace('{folder}', exportFolder) : words.downloads
}

/** What the Editor page's Publish can do for the shown project (FL3):
 *
 *  - 'none': no button. Not a clip project, or its clip is gone (a whole
 *    video's or a file's export goes to the export folder, not the Library).
 *  - 'export_first': off. Publish sends the clip in the Library, so the
 *    edit has to be exported (taken in as the clip's video) first.
 *  - 'kept_beside': off. The last export was saved next to the clip, which
 *    had been changed meanwhile, so the clip's video is not the edit.
 *  - 'clip_changed': off. The clip was changed after the export replaced
 *    its video, so its video is no longer the edit.
 *  - 'ready': on. The clip's video is the project's last export. */
export type PublishState = 'none' | 'export_first' | 'kept_beside' | 'clip_changed' | 'ready'

export function publishState(
  project: {
    kind?: string
    clip?: { id: number } | null
    clip_gone?: boolean
    clip_changed?: boolean
    exports?: { result: string }[]
  } | null
): PublishState {
  if (project?.kind !== 'clip' || !project.clip || project.clip_gone) return 'none'
  const last = (project.exports ?? []).at(-1)
  if (last?.result === 'kept_beside') return 'kept_beside'
  if (last?.result !== 'replaced_clip') return 'export_first'
  return project.clip_changed ? 'clip_changed' : 'ready'
}
