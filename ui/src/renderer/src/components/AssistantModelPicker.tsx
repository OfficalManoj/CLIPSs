import { useRef } from 'react'
import { openAISetup } from '../lib/aiSetup'
import {
  MANAGE,
  pickerSections,
  SETTINGS_TIP,
  settingsProvider,
  shortName,
  type PickerGroup
} from '../lib/agentModels'
import { t } from '../lib/i18n'

/** The Ask Clips Kitty box's own model list, like the one in VS Code's AI
 *  chat panel: Ollama first, then OpenRouter, then the direct providers.
 *
 *  Choosing here changes only the box's model. The model that picks clips,
 *  the sidebar switcher and the Dashboard chip stay as they were set. */
export default function AssistantModelPicker({
  groups,
  value,
  loading,
  disabled,
  onChoose,
  onOpen
}: {
  groups: PickerGroup[]
  value: string
  loading: boolean
  disabled?: boolean
  onChoose: (spec: string) => void
  /** The list is read again when it is opened, so a key added in Settings
   *  shows up without restarting. */
  onOpen: () => void
}): JSX.Element {
  const sections = pickerSections(groups)
  const known = sections.some((s) => s.options.some((o) => o.value === value))
  const opened = useRef(0)

  return (
    <select
      className="input !w-auto max-w-[16rem] !py-0.5 !px-1.5 text-[11px] text-muted"
      aria-label={t('Model for this box')}
      title={t(
        'The AI model this box uses. It only changes this box: your clips are still picked by the model chosen in the sidebar.'
      )}
      value={value}
      disabled={disabled}
      onFocus={() => {
        // At most every 15 seconds: focus also fires on every click back in.
        if (Date.now() - opened.current > 15000) {
          opened.current = Date.now()
          onOpen()
        }
      }}
      onChange={(e) => {
        const provider = settingsProvider(e.target.value)
        if (provider) {
          openAISetup(provider)
          return
        }
        onChoose(e.target.value)
      }}
    >
      {!known && (
        <option value={value}>
          {value ? shortName(value, groups) : loading ? t('Loading models…') : t('Choose a model')}
        </option>
      )}
      {sections.map((section) => (
        <optgroup key={section.label} label={section.label}>
          {section.options.map((option) => (
            <option
              key={option.value}
              value={option.value}
              disabled={option.disabled}
              title={t(option.tooltip)}
            >
              {option.name ? `${option.name} · ${t(option.badge)}` : t(option.badge)}
            </option>
          ))}
        </optgroup>
      ))}
      {loading && groups.length > 0 && (
        <option value="loading" disabled>
          {t('Loading models…')}
        </option>
      )}
      <option value={MANAGE} title={t(SETTINGS_TIP)}>
        {t('Manage models in Settings')}
      </option>
    </select>
  )
}
