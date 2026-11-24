/** The keyboard in a window of the app's own (a modal dialog): it starts in
 *  the window, Tab and Shift+Tab stay in it, and Escape leaves it. Closed any
 *  way, the keyboard goes back to what opened it, so a keyboard user keeps
 *  their place.
 *
 *  First written for Make clips again's window (removed from the Clips page
 *  in KC), and used by the Publish window (F1 U-1), which opens over the editor's area on the
 *  Editor page: Tab must never reach that hidden area behind it. */
import { useEffect, useRef, type RefObject } from 'react'

/** What Tab can reach inside the window. */
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** `box` is the window's own box (tabIndex -1, so it can take the focus at
 *  first). `onEscape` is called for Escape; it may do nothing (a window
 *  that is busy). `returnTo` is where the keyboard goes when it closes;
 *  without it, what had the focus when it opened. */
export function useDialogFocus(
  box: RefObject<HTMLElement>,
  onEscape: () => void,
  returnTo?: RefObject<HTMLElement>
): void {
  const escape = useRef(onEscape)
  escape.current = onEscape
  useEffect(() => {
    const opener =
      returnTo?.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    box.current?.focus()
    const key = (e: KeyboardEvent): void => {
      // A window of its own opened over this one (the "changed in the
      // editor" question over Other languages, KC) keeps its own keys.
      const target = e.target
      if (
        target instanceof Element &&
        box.current &&
        !box.current.contains(target) &&
        target.closest('[aria-modal="true"]')
      )
        return
      if (e.key === 'Escape') {
        escape.current()
        return
      }
      if (e.key !== 'Tab' || !box.current) return
      const inside = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.getClientRects().length > 0
      )
      const active = document.activeElement
      if (inside.length === 0) {
        e.preventDefault()
        box.current.focus()
        return
      }
      const first = inside[0]
      const last = inside[inside.length - 1]
      const lost = !(active instanceof Node) || !box.current.contains(active)
      if (e.shiftKey && (lost || active === first || active === box.current)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (lost || active === last)) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('keydown', key)
      if (opener && opener.isConnected && !(opener as HTMLButtonElement).disabled) opener.focus()
    }
  }, [])
}
