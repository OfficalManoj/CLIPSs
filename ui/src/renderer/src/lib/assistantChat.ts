/** The Ask Clips Kitty conversation, kept outside any page.
 *
 *  The box sits on the Dashboard and on the Clip Editor. Each page mounts its
 *  own copy of the component, so the conversation lives here instead: what
 *  was said, the plan card and whether a request is running carry over when
 *  you change page, and a reply that comes back while you are on another
 *  page still lands in the conversation.
 *
 *  No imports beyond types, so tests run it under Node as it is.
 */
import type { PublishPlanItem } from './types'

export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  steps?: { tool: string; result: string }[]
}

/** A social-platform schedule is a different plan from a YouTube one:
 *  different confirm, different wording. Null means the YouTube kind. */
export interface ChatSchedule {
  platforms: string[]
  every_hours: number
  /** A daily budget. 0 means a flat every_hours gap instead. */
  per_day: number
  gap_hours: number
  /** When the first post goes out, as the plan worked it out. Empty means now. */
  start_at: string
  hashtags: string[]
}

export interface ChatState {
  turns: ChatTurn[]
  busy: boolean
  plan: PublishPlanItem[] | null
  planNote: string
  schedule: ChatSchedule | null
}

const EMPTY: ChatState = { turns: [], busy: false, plan: null, planNote: '', schedule: null }

let state: ChatState = EMPTY
const listeners = new Set<() => void>()

export function chatState(): ChatState {
  return state
}

export function subscribeChat(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Change part of the conversation; every box showing it updates. */
export function updateChat(change: Partial<ChatState> | ((now: ChatState) => Partial<ChatState>)): void {
  state = { ...state, ...(typeof change === 'function' ? change(state) : change) }
  for (const listener of [...listeners]) listener()
}

export function resetChat(): void {
  updateChat(EMPTY)
}

/** A reply as plain words. The model is told not to use Markdown, but a
 *  small local one still writes it now and then, and the box shows text as
 *  it comes: "**15**" and "*   **[7]**" reached the person as stars. */
export function plainReply(text: string): string {
  return String(text ?? '')
    .split('\n')
    .map((line) => {
      let out = line
        // Headings: "## Your clips" -> "Your clips".
        .replace(/^\s{0,3}#{1,6}\s+/, '')
        // Bullets: "*   item", "- item", "+ item" -> "• item".
        .replace(/^(\s*)[*+-]\s+/, '$1• ')
      out = out
        // Bold and italics with stars or underscores, then inline code.
        .replace(/\*\*(.+?)\*\*/g, '$1')
        .replace(/__(.+?)__/g, '$1')
        .replace(/(^|[\s(])\*(\S(?:.*?\S)?)\*(?=[\s).,:;!?]|$)/g, '$1$2')
        .replace(/`([^`]+)`/g, '$1')
      return out
    })
    .join('\n')
}
