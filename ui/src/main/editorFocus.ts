// The keyboard when Clips Kitty's window comes back (editor suite FL2, D1).
//
// The editor is a child window inside Clips Kitty's window. When the person
// switches to another program, the system takes the keyboard from the whole
// window, the editor's part included, and the editor reports that it lost
// the keys. That is not the person handing the keys back to the app (Ctrl+F6,
// which moves the page's focus to "← Projects"), so the page must not be told
// "the editor let go" then: Space or Enter would press "← Projects" once the
// window is back. And when the window is activated again (Alt+Tab, the
// taskbar, a click on its title bar), the keys go back where they were: to
// the editor when it had them, or left to the page when one of the app's own
// controls had them (the page puts its own focus back by itself).
//
// The rules, kept apart from Electron so tests/test_ui_editor_fl2.py runs
// them under Node; ui/src/main/editor.ts feeds them the window's own focus
// and blur (activation) events and the editor's polled state:
//
// - left(editorHadKeys): the window was deactivated.
// - back(now): it was activated again; true when the keys go back to the
//   editor (it had them when the window was left).
// - appAsked(kind, now): the page asked for the keys (a pointer press, or a
//   focus that moved inside the page). A press always wins and stops a give
//   back; a focus event the page fires only because the window came back
//   is ignored while the keys are being given back.
// - shown(polled, before, now): what to tell the page about the editor having
//   the keys: unchanged while the window is not active, and while the keys
//   are being given back.

/** How long after the window comes back the editor may take to report the
 *  keys again before the page is told it does not have them. */
export const GIVE_BACK_MS = 1500

/** When, after the window came back, the keys are given to the editor again
 *  if it has not reported them yet (the window's own content takes the focus
 *  a moment after it is activated). */
export const GIVE_BACK_TRIES_MS: readonly number[] = Object.freeze([0, 150, 400, 900])

export type AppAsk = 'pointer' | 'focus'

export interface KeysReturn {
  left(editorHadKeys: boolean): void
  back(now: number): boolean
  appAsked(kind: AppAsk, now: number): boolean
  shown(polled: boolean, before: boolean, now: number): boolean
  /** Whether a give back is still wanted (another try may be made). */
  givingBack(now: number): boolean
  /** The editor went away or was hidden: nothing to give back. */
  reset(): void
}

export function keysReturn(startActive = true): KeysReturn {
  let active = startActive
  let hadKeys = false
  let backAt = 0
  let giving = false
  return {
    left(editorHadKeys: boolean): void {
      active = false
      hadKeys = editorHadKeys
      giving = false
    },
    back(now: number): boolean {
      active = true
      backAt = now
      giving = hadKeys
      hadKeys = false
      return giving
    },
    appAsked(kind: AppAsk, now: number): boolean {
      if (kind === 'pointer') {
        giving = false
        return true
      }
      return !(giving && now - backAt < GIVE_BACK_MS)
    },
    shown(polled: boolean, before: boolean, now: number): boolean {
      if (!active) return before
      if (polled) {
        giving = false
        return true
      }
      if (giving && now - backAt < GIVE_BACK_MS) return before
      giving = false
      return false
    },
    givingBack(now: number): boolean {
      return active && giving && now - backAt < GIVE_BACK_MS
    },
    reset(): void {
      hadKeys = false
      giving = false
    }
  }
}
