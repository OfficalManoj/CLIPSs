/** "Other languages…" on the Editor page (step KF): new tabs in the same
 *  editor project, one per language, with the captions translated in place
 *  (the editor's own font, size, colour and place) and, when asked, the
 *  translation spoken on a lane of its own. The words are checked and fixed
 *  on the tab's titles in the editor itself, and the tab is exported with
 *  the editor's own Export.
 *
 *  The editor is hidden while this window is open (the page holds the
 *  cover). Closing it keeps the work going. */
import { useEffect, useRef, useState, type RefObject } from 'react'
import { api, editorApi } from '../../lib/api'
import { makeLanguageTabs } from '../../lib/editor'
import { activeLocale, t } from '../../lib/i18n'
import { useDialogFocus } from '../../lib/useDialogFocus'
import { useEvents } from '../../lib/useEvents'
import type { VoiceOption } from '../../lib/types'
import GlossaryEditor from '../GlossaryEditor'

const REMEMBER = 'multilingual-languages'
const VOICES = 'multilingual-voices'
// Translation wants multilingual strength, and Qwen is better at it than
// the clipping model. Offered, never required.
const RECOMMENDED = 'qwen2.5:7b'
/** Input types that are controls, not somewhere text is typed. */
const NOT_TYPING = new Set(['range', 'checkbox', 'radio', 'color', 'button', 'submit', 'reset', 'file'])

/** Escape in a text field gives up what is typed there, never the window. */
function typing(): boolean {
  const el = document.activeElement as HTMLElement | null
  return (
    !!el &&
    (el.tagName === 'TEXTAREA' ||
      el.isContentEditable ||
      (el instanceof HTMLInputElement && !NOT_TYPING.has(el.type)))
  )
}

/** A language's name in the app's own language ("Spanish", "español"). */
function displayName(code: string, englishName: string): string {
  try {
    const name = new Intl.DisplayNames([activeLocale()], { type: 'language' }).of(code)
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : englishName
  } catch {
    return englishName
  }
}

function remembered<T>(key: string, fallback: T): T {
  try {
    return (JSON.parse(localStorage.getItem(key) ?? '') as T) ?? fallback
  } catch {
    return fallback
  }
}

export default function OtherLanguagesDialog({
  projectId,
  name,
  clipId,
  onClose,
  returnFocus
}: {
  projectId: string
  name: string
  clipId?: number
  onClose: () => void
  returnFocus?: RefObject<HTMLElement>
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  useDialogFocus(
    box,
    () => {
      if (!typing()) onClose()
    },
    returnFocus
  )
  const [langs, setLangs] = useState<{ code: string; name: string; can_dub: boolean }[]>([])
  const [canDub, setCanDub] = useState(false)
  const [have, setHave] = useState<string[]>([])
  const [picked, setPicked] = useState<string[]>(() => remembered<string[]>(REMEMBER, []))
  const [dubIn, setDubIn] = useState(false)
  const [voiceFor, setVoiceFor] = useState<Record<string, string>>(() =>
    remembered<Record<string, string>>(VOICES, {})
  )
  const [voiceList, setVoiceList] = useState<
    Record<string, { voices: VoiceOption[]; default: string | null; message: string }>
  >({})
  const [installed, setInstalled] = useState<string[]>([])
  const [transModel, setTransModel] = useState('')
  const [pulling, setPulling] = useState(false)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    api
      .languages()
      .then((r) => {
        setLangs(r.languages)
        setCanDub(r.dubbing_available)
      })
      .catch(() => {})
    editorApi
      .languages(projectId)
      .then((r) => setHave(r.tabs.map((x) => x.language)))
      .catch(() => {})
    api.models().then((m) => setInstalled(m.installed.map((i) => i.name))).catch(() => {})
    api.settings().then((st) => setTransModel(st.translation_model || '')).catch(() => {})
  }, [projectId])

  // Each ticked language's voices, once dubbing is on.
  useEffect(() => {
    if (!canDub || !dubIn) return
    picked
      .filter((c) => langs.find((l) => l.code === c)?.can_dub && !voiceList[c])
      .forEach((c) => {
        api
          .voicesFor(c)
          .then((r) =>
            setVoiceList((prev) => ({ ...prev, [c]: { voices: r.voices, default: r.default, message: r.message ?? '' } }))
          )
          .catch(() => {})
      })
  }, [picked, canDub, dubIn, langs])

  // What the job is doing, as it says it.
  useEvents((e) => {
    if (e.type === 'progress' && e.stage === 'multilingual' && e.message) {
      if ((e as { project_id?: string }).project_id === projectId) setStep(e.message)
    }
  })

  const chosen = picked.filter((c) => !have.includes(c) && langs.some((l) => l.code === c))
  const shownVoice = (c: string): string => {
    const got = voiceList[c]
    const mine = got && !got.voices.some((v) => v.id === voiceFor[c]) ? '' : (voiceFor[c] ?? '')
    return mine || got?.default || ''
  }

  const toggle = (code: string): void => {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      localStorage.setItem(REMEMBER, JSON.stringify(next))
      return next
    })
  }

  const make = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    setStep(t('Starting…'))
    const voices: Record<string, string> = {}
    if (dubIn) {
      for (const c of chosen) if (shownVoice(c)) voices[c] = shownVoice(c)
    }
    const made = [...chosen]
    const failed = await makeLanguageTabs(projectId, { languages: made, dub: dubIn, voices }, setStep)
    setBusy(false)
    setStep('')
    if (failed) setNotice(failed)
    else setHave((prev) => [...prev, ...made])
  }

  const pickModel = async (model: string): Promise<void> => {
    await api.patchSettings({ translation_model: model })
    setTransModel(model)
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Other languages')}
    >
      <div ref={box} tabIndex={-1} className="card w-full max-w-xl max-h-[90vh] overflow-y-auto outline-none space-y-3">
        <div className="flex items-center gap-3">
          <h3 className="font-semibold text-lg truncate">
            {t('Other languages')}: {name}
          </h3>
          <button
            type="button"
            className="ml-auto btn-ghost !py-1 text-sm shrink-0"
            onClick={onClose}
            title={t('Close this window and go back to the editor. The language tabs keep being made.')}
          >
            {t('Close')}
          </button>
        </div>
        <p className="text-xs text-muted">
          {t('Each language gets its own tab in this project, with the captions translated in the same style.')}
        </p>

        <div className="flex gap-1.5 flex-wrap">
          {langs.map((l) => {
            const done = have.includes(l.code)
            const on = done || picked.includes(l.code)
            return (
              <button
                key={l.code}
                type="button"
                disabled={done || busy}
                aria-pressed={on}
                onClick={() => toggle(l.code)}
                className={`px-2.5 py-1 rounded-full text-xs border disabled:opacity-50 ${
                  on ? 'border-accent text-accent bg-accent/10' : 'border-raised text-muted hover:text-ink'
                }`}
                title={done ? t('Already has a tab. Delete the tab in the editor to make it again.') : undefined}
              >
                {displayName(l.code, l.name)}
              </button>
            )
          })}
        </div>

        {canDub && (
          <label
            className="flex items-center gap-2 text-sm"
            title={t('Also speak the translation with a voice, on a lane of its own. The clip’s own sound is turned down.')}
          >
            <input type="checkbox" checked={dubIn} disabled={busy} onChange={(e) => setDubIn(e.target.checked)} />
            {t('Dub the voice')}
          </label>
        )}
        {dubIn &&
          chosen.map((c) => {
            const lang = langs.find((l) => l.code === c)
            if (!lang?.can_dub) {
              return (
                <p
                  key={c}
                  className="text-xs text-muted"
                  title={t('There is no voice for this language, so its tab gets the translated captions only.')}
                >
                  {displayName(c, lang?.name ?? c)}: {t('no voice')}
                </p>
              )
            }
            return (
              <div key={c} className="flex items-center gap-2 text-xs">
                <span className="text-muted w-24 shrink-0">{displayName(c, lang.name)}</span>
                <select
                  className="input !w-56 !py-1 text-xs"
                  value={shownVoice(c)}
                  aria-label={t('Voice')}
                  onChange={(e) => {
                    const next = { ...voiceFor, [c]: e.target.value }
                    setVoiceFor(next)
                    localStorage.setItem(VOICES, JSON.stringify(next))
                  }}
                >
                  {!voiceList[c]?.default && <option value="">{t('Default voice')}</option>}
                  {(voiceList[c]?.voices ?? []).map((v) => (
                    <option key={v.id} value={v.id} disabled={v.ready === false}>
                      {[v.name, v.country, v.quality].filter(Boolean).join(' · ')}
                    </option>
                  ))}
                </select>
                {voiceList[c]?.message && <span className="text-warn">{t(voiceList[c].message)}</span>}
              </div>
            )
          })}

        {clipId !== undefined && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted">{t('Words to keep as they are')}</summary>
            <div className="pt-2">
              <GlossaryEditor clipId={clipId} />
            </div>
          </details>
        )}

        <p className="text-xs text-muted border-t border-raised/60 pt-2">
          {transModel ? (
            <>
              {t('Translating with')} <code>{transModel}</code>{' '}
              <button type="button" className="text-accent hover:underline" onClick={() => void pickModel('')}>
                {t('use the clipping model instead')}
              </button>
            </>
          ) : installed.some((n) => n.startsWith('qwen')) ? (
            <>
              {t('Qwen is installed and translates noticeably better than the clipping model.')}{' '}
              <button
                type="button"
                className="text-accent hover:underline"
                onClick={() => void pickModel(installed.find((n) => n.startsWith('qwen')) ?? RECOMMENDED)}
              >
                {t('Use it for translation')}
              </button>
            </>
          ) : (
            <>
              {t('Translations use your clipping model. For better quality, install')} <code>{RECOMMENDED}</code> (
              {t('about 4.7 GB, one time, stays on your PC')}){' '}
              <button
                type="button"
                className="text-accent hover:underline disabled:opacity-50"
                disabled={pulling}
                onClick={async () => {
                  setPulling(true)
                  try {
                    await api.pullModel(RECOMMENDED)
                    await pickModel(RECOMMENDED)
                  } catch (e) {
                    setNotice(`${t('Install failed')}: ${e instanceof Error ? e.message : String(e)}`)
                  } finally {
                    setPulling(false)
                  }
                }}
              >
                {pulling ? t('Installing…') : t('Install & use')}
              </button>
            </>
          )}
        </p>

        <div className="flex items-center gap-3">
          <button
            type="button"
            className="btn-accent !py-1 text-sm"
            disabled={busy || chosen.length === 0}
            onClick={() => void make()}
            title={chosen.length === 0 ? t('Pick at least one language') : undefined}
          >
            {t('Make language tabs')}
          </button>
          {busy && (
            <span className="text-xs text-muted" role="status">
              {step}
            </span>
          )}
        </div>
        {busy && <p className="text-xs text-muted">{t('The editor reopens with the new tabs in a few seconds.')}</p>}
        {notice && (
          <p className="text-xs text-warn" role="alert">
            {notice}
          </p>
        )}
      </div>
    </div>
  )
}
