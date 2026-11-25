import type { AIStatus } from './types'

/** The app's model when it runs in the cloud on the person's own key, as
 *  "OpenRouter · google/gemma-4-26b-a4b-it", or "" when it runs on this PC.
 *  The sidebar switcher and the Dashboard chip both show it, so the two can
 *  never disagree about which model picks the clips. */
export function cloudModelLabel(status: AIStatus): string {
  if (status.active.local) return ''
  const label = [...status.providers, ...(status.signin ?? [])].find(
    (p) => p.id === status.active.provider
  )?.label
  return `${label ?? status.active.provider} · ${status.active.model}`
}
