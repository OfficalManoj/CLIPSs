/** The Ask Clips Kitty box's model picker, as plain data.
 *
 *  Like the model list in VS Code's AI chat panel: one dropdown, grouped
 *  Ollama first, then OpenRouter, then the direct providers and plan
 *  sign-ins, in the order the engine sends them (GET /agent/models). A group
 *  with no key or sign-in is one row that opens Settings → AI.
 *
 *  No imports, so tests run it with plain Node. The English words come back
 *  as they are; the component passes each through t().
 */

export interface PickerModel {
  spec: string
  name: string
  tools: boolean
  note: string
}

export interface PickerGroup {
  id: string
  label: string
  settings: boolean
  message: string
  models: PickerModel[]
}

export interface PickerOption {
  value: string
  name: string
  /** "Uses tools" or "No tools", or a group's message. */
  badge: string
  tooltip: string
  disabled: boolean
}

export interface PickerSection {
  label: string
  options: PickerOption[]
}

/** The value of a row that opens Settings → AI on that provider. */
export const SETTINGS_PREFIX = 'settings:'
/** The last row: every key and model is managed in Settings → AI. */
export const MANAGE = `${SETTINGS_PREFIX}openrouter`

export const USES_TOOLS = 'Uses tools'
export const NO_TOOLS = 'No tools'
export const USES_TOOLS_TIP = 'This model can use tools, so it can run Clips Kitty for you from this box.'
export const NO_TOOLS_TIP = "This model can't use tools, so it can't run Clips Kitty from this box. Choose one marked Uses tools."
export const SETTINGS_TIP = 'Opens Settings → AI, where you add your key or sign in.'

export function settingsProvider(value: string): string {
  return value.startsWith(SETTINGS_PREFIX) ? value.slice(SETTINGS_PREFIX.length) : ''
}

export function pickerSections(groups: PickerGroup[]): PickerSection[] {
  return groups.map((group) => {
    if (group.models.length > 0) {
      return {
        label: group.label,
        options: group.models.map((m) => ({
          value: m.spec,
          name: m.name,
          badge: m.tools ? USES_TOOLS : NO_TOOLS,
          tooltip: [m.tools ? USES_TOOLS_TIP : NO_TOOLS_TIP, m.note].filter(Boolean).join(' '),
          disabled: false
        }))
      }
    }
    // One row saying why it is empty. Opens Settings when that is the fix.
    return {
      label: group.label,
      options: [
        {
          value: group.settings ? `${SETTINGS_PREFIX}${group.id}` : `empty:${group.id}`,
          name: '',
          badge: group.message,
          tooltip: group.settings ? SETTINGS_TIP : group.message,
          disabled: !group.settings
        }
      ]
    }
  })
}

/** The short name shown on the closed picker: the model's own name, without
 *  the provider in front ("gemma4:e4b", "Google: Gemma 4 26B A4B"). */
export function shortName(spec: string, groups: PickerGroup[]): string {
  for (const group of groups) {
    const found = group.models.find((m) => m.spec === spec)
    if (found) return found.name
  }
  const slash = spec.indexOf('/')
  return slash >= 0 ? spec.slice(slash + 1) : spec
}
