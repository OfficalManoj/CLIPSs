import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { openAISetup } from '../lib/aiSetup'
import { api, type AgentModelList } from '../lib/api'
import {
  chatState,
  plainReply,
  subscribeChat,
  updateChat,
  type ChatSchedule,
  type ChatTurn
} from '../lib/assistantChat'
import { t } from '../lib/i18n'
import { CHECKING, isChecking, pullPercent } from '../lib/modelPull'
import { useEvents } from '../lib/useEvents'
import AssistantModelPicker from './AssistantModelPicker'
import { seedOptions } from './queue/AddVideos'

/** Ask Clips Kitty to do things in plain language.
 *
 *  A model reads the request and calls the app's own tools: find a video,
 *  list its clips, plan uploads. It is the same tool list the MCP server gives
 *  an outside agent, so there is one set of descriptions to keep honest
 *  rather than two.
 *
 *  The model is the box's own, chosen in its picker (AssistantModelPicker):
 *  Ollama, then OpenRouter, then any provider with the person's own key. It
 *  never changes the model that picks clips.
 *
 *  It plans posts but never posts (server/agent.py HUMAN_ONLY): a plan comes
 *  back as a card here, and only a click on that card posts or schedules.
 *
 *  The same box sits on the Dashboard and the Clip Editor, with one
 *  conversation between them (lib/assistantChat). `where` says where it is,
 *  so "this clip" on the Clip Editor means the clip that is open there, and
 *  `onAnswered` lets that page show what a request changed.
 */
export default function Assistant({
  where = '',
  onAnswered
}: {
  where?: string
  onAnswered?: () => void
} = {}): JSX.Element | null {
  const [ready, setReady] = useState<boolean | null>(null)
  const [model, setModel] = useState('')
  const [reason, setReason] = useState('')
  // The provider whose key would fix it, for an "Add your key" button.
  const [settingsFor, setSettingsFor] = useState('')
  // The box's model list and what it is set to. Its own choice, kept by the
  // engine (PUT /agent/model), never the app's model.
  const [models, setModels] = useState<AgentModelList | null>(null)
  const [loadingModels, setLoadingModels] = useState(true)
  // The conversation is shared by every page's box (lib/assistantChat), so
  // it carries over when you change page; only the half-typed input is this
  // copy's own.
  const { turns, busy, plan, planNote, schedule } = useSyncExternalStore(subscribeChat, chatState)
  const addTurn = (turn: ChatTurn): void => updateChat((now) => ({ turns: [...now.turns, turn] }))
  const setBusy = (value: boolean): void => updateChat({ busy: value })
  const setPlan = (value: typeof plan): void => updateChat({ plan: value })
  const setPlanNote = (value: string): void => updateChat({ planNote: value })
  const setSchedule = (value: ChatSchedule | null): void => updateChat({ schedule: value })
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  // The Gemma 4 build to offer when nothing installed can run the box (#121).
  const [install, setInstall] = useState<{ model: string; size_gb: number } | null>(null)
  const [pulling, setPulling] = useState(false)
  const [pullPct, setPullPct] = useState<number | null>(null)
  // Ollama's step, for the checking text once the bytes are in.
  const [pullStatus, setPullStatus] = useState('')
  const [pullError, setPullError] = useState('')
  // Where the download stands with the engine: 'asking' while the POST is on
  // its way, 'running' once the engine has said it is running it (the POST
  // came back, or its record listed it). Only a 'running' download whose
  // record goes missing has stopped; an 'asking' one may not have landed yet.
  const pullStage = useRef<'idle' | 'asking' | 'running'>('idle')

  const showStatus = useCallback(
    (s: { ready: boolean; model: string; reason: string; settings?: string; install?: { model: string; size_gb: number } | null }): void => {
      setReady(s.ready)
      setModel(s.model)
      setReason(s.reason)
      setSettingsFor(s.settings ?? '')
      setInstall(s.install ?? null)
    },
    []
  )

  // Counts picks in the list. An answer that set off before the latest pick
  // is dropped, so a slow reload can't put the old model back.
  const pickSeq = useRef(0)

  const checkStatus = useCallback(
    (spec = ''): void => {
      const seq = pickSeq.current
      api
        .agentStatus(spec)
        .then((s) => {
          if (seq === pickSeq.current) showStatus(s)
        })
        .catch(() => setReady(false))
    },
    [showStatus]
  )

  // The list can take a few seconds (each provider with a key is asked), so
  // the box checks its current model first and the list fills in after.
  const loadModels = useCallback((): void => {
    const seq = pickSeq.current
    setLoadingModels(true)
    api
      .agentModels()
      .then((list) => {
        setModels(list)
        // The first usable model, in order, when the remembered one is gone.
        if (list.default && seq === pickSeq.current) checkStatus(list.default)
      })
      .catch(() => {
        /* engine not answering: the status line already says so */
      })
      .finally(() => setLoadingModels(false))
  }, [checkStatus])

  useEffect(() => {
    checkStatus()
    loadModels()
  }, [checkStatus, loadModels])

  const chooseModel = async (spec: string): Promise<void> => {
    pickSeq.current += 1
    const seq = pickSeq.current
    setModel(spec)
    // "Checking..." until the engine has checked the new model: the box must
    // not look ready for a model it may not be able to run.
    setReady(null)
    setReason('')
    try {
      // Remembered for the box only. Its answer is the check for that model.
      const status = await api.chooseAgentModel(spec)
      if (seq === pickSeq.current) showStatus(status)
    } catch (e) {
      if (seq !== pickSeq.current) return
      setReady(false)
      setReason(e instanceof Error ? e.message : String(e))
    }
  }

  const picker = (
    <AssistantModelPicker
      groups={models?.groups ?? []}
      value={model}
      loading={loadingModels}
      disabled={busy}
      onChoose={(spec) => void chooseModel(spec)}
      onOpen={loadModels}
    />
  )

  // One download's state, from an event or from the engine's record. Worded
  // as the Models page and setup word it (lib/modelPull): never 100% before
  // the model is installed, and the checking step says to keep the app open.
  const showPull = useCallback(
    (p: { status?: string; completed?: number | null; total?: number | null; error?: string | null }): void => {
      if (p.status === 'done') {
        setPulling(false)
        setPullPct(null)
        pullStage.current = 'idle'
        checkStatus()
      } else if (p.status === 'error') {
        setPulling(false)
        setPullPct(null)
        setPullError(p.error || 'unknown error')
        pullStage.current = 'idle'
      } else {
        setPulling(true)
        setPullError('')
        setPullStatus(p.status ?? '')
        setPullPct(isChecking(p.status) ? null : pullPercent(p.completed, p.total))
      }
    },
    [checkStatus]
  )

  // A download runs in the engine and outlives this component, so its state is
  // read from the events rather than remembered from the click: leave the
  // dashboard mid-download and come back, and the next event puts the
  // percentage back on the button.
  useEvents((e) => {
    if (e.type !== 'model_pull' || !install || e.tag !== install.model) return
    showPull(e)
  })

  // The events can miss the one that matters (a reconnect, a window that fell
  // behind), which left the button at its last percentage for good. So the
  // engine's record is read when the box opens and every few seconds while it
  // downloads, as the Models page does.
  const installTag = install?.model
  const catchUp = useCallback(async (): Promise<void> => {
    if (!installTag) return
    const server = await api
      .pullStatus()
      .then((r) => r.pulls)
      .catch(() => null)
    if (!server) return // engine not answering: the next look, or an event, will say
    const mine = server.find((p) => p.tag === installTag)
    if (mine) {
      if (!mine.done) pullStage.current = 'running'
      showPull(mine)
    } else if (pullStage.current === 'running') {
      // Gone from the engine's record while it ran: the engine restarted,
      // and the download with it.
      pullStage.current = 'idle'
      setPulling(false)
      setPullPct(null)
      setPullError('The download stopped. Try again; it carries on where it stopped.')
      checkStatus()
    }
  }, [installTag, showPull, checkStatus])

  useEffect(() => {
    void catchUp()
  }, [catchUp])

  useEffect(() => {
    if (!pulling) return
    const timer = setInterval(() => void catchUp(), 3000)
    return () => clearInterval(timer)
  }, [pulling, catchUp])

  const startInstall = async (): Promise<void> => {
    if (!install || pulling) return
    setPulling(true)
    setPullPct(null)
    setPullStatus('')
    setPullError('')
    pullStage.current = 'asking'
    try {
      await api.pullModel(install.model)
    } catch (e) {
      pullStage.current = 'idle'
      setPulling(false)
      setPullError(e instanceof Error ? e.message : String(e))
      return
    }
    // Unless a "done" or "error" came in first and settled it.
    if (pullStage.current === 'asking') pullStage.current = 'running'
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [turns, busy])

  const send = async (): Promise<void> => {
    const text = input.trim()
    // Read from the store, not this render: another page's box may be busy.
    if (!text || chatState().busy) return
    setInput('')
    // The words so far, before this one is added.
    const prior = chatState().turns
    updateChat({ plan: null, planNote: '', schedule: null, busy: true })
    addTurn({ role: 'user', text })
    try {
      // Only the words go back, not the tool traffic: the model re-reads its
      // own answers, and feeding it every raw tool result again would fill
      // the context with things it already summarised.
      const history = prior.map((turn) => ({ role: turn.role, content: turn.text }))
      // The Generate bar's settings travel with the message. Without
      // them a job asked for in the chat ignored every toggle above it:
      // captions came back burned in however often the box was unticked,
      // because that choice lives in this window and the model never
      // saw it.
      const res = await api.agentChat(text, history, seedOptions(), model, where)
      addTurn({
        role: 'assistant',
        text: plainReply(res.reply) || t('Done.'),
        steps: res.steps?.map((s) => ({ tool: s.tool, result: s.result }))
      })
      // A retitled clip or a new animation shows on the page at once.
      if (res.steps?.length) onAnswered?.()
      if (res.plan?.items?.length) {
        setPlan(res.plan.items)
        setPlanNote((res.plan.warnings || []).join(' '))
        const p = res.plan as unknown as {
          provider?: string
          platforms?: string[]
          every_hours?: number
          per_day?: number
          gap_hours?: number
          start_at?: string
          hashtags?: string[]
        }
        setSchedule(
          p.provider === 'woopsocial'
            ? {
                platforms: p.platforms || ['youtube'],
                every_hours: p.every_hours || 0,
                per_day: p.per_day || 0,
                gap_hours: p.gap_hours || 1,
                start_at: p.start_at || '',
                hashtags: p.hashtags || []
              }
            : null
        )
      }
    } catch (e) {
      addTurn({ role: 'assistant', text: String(e).replace(/^Error:\s*/, '') })
    } finally {
      setBusy(false)
    }
  }

  const confirmPlan = async (): Promise<void> => {
    if (!plan || chatState().busy) return
    setBusy(true)
    try {
      // A social-platform schedule goes through the batch route; a YouTube
      // plan through the one it has always used.
      if (schedule) {
        // Everything the plan showed, so what is sent is what was agreed:
        // the daily budget, and the hashtags, which used to be dropped here.
        const res = await api.woopSocialBatch({
          clip_ids: plan.map((i) => i.clip_id),
          platforms: schedule.platforms,
          every_hours: schedule.every_hours,
          per_day: schedule.per_day || undefined,
          gap_hours: schedule.per_day ? schedule.gap_hours : undefined,
          // The start the card's times were worked out from, so "tomorrow
          // at 9" is not sent as "now".
          start_at: schedule.start_at || undefined,
          hashtags: schedule.hashtags
        })
        const parts = [`${t('Scheduled')} ${res.started.length}.`]
        for (const row of res.skipped) {
          parts.push(`${t('Skipped clip')} ${row.clip_id}: ${row.reason}`)
        }
        addTurn({ role: 'assistant', text: parts.join(' ') })
        setPlan(null)
        setSchedule(null)
        return
      }
      const res = await api.executePublishPlan(plan)
      const parts = [`${t('Uploading')} ${res.started.length}.`]
      for (const row of res.skipped) parts.push(`${t('Skipped clip')} ${row.clip_id}: ${row.reason}`)
      addTurn({ role: 'assistant', text: parts.join(' ') })
      setPlan(null)
    } catch (e) {
      setPlanNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  if (ready === false) {
    return (
      <section className="card h-full overflow-y-auto" aria-label={t('Assistant')}>
        <div className="flex items-baseline justify-between gap-2">
          <p className="font-semibold">{t('Ask Clips Kitty')}</p>
          {picker}
        </div>
        {install ? (
          /* Nothing installed can run the box. Say what it needs and offer it,
             rather than sending someone to another page to choose a build. */
          <>
            {/* One paragraph and one row, so it all fits the box at its
                shortest (132 px) without the button sliding out of view. */}
            <p className="text-sm mt-0.5">
              {t('Install Gemma 4 to use this box.')}{' '}
              <span className="text-xs text-muted">
                {t(
                  'It needs a model that can use tools, like Gemma 4, or choose OpenRouter or another provider in the list. Your clips are still picked by the model you chose.'
                )}
              </span>
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
              <button
                className="btn-accent !px-3 !py-1 text-sm"
                disabled={pulling}
                onClick={startInstall}
              >
                {pulling
                  ? isChecking(pullStatus)
                    ? t(CHECKING)
                    : `${t('Downloading')}${pullPct === null ? '…' : ` ${pullPct}%`}`
                  : t('Install Gemma 4')}
              </button>
              <span className="text-xs text-muted">
                <code>{install.model}</code> · {t('about')} {install.size_gb} GB,{' '}
                {t('one time, stays on your PC')} ·{' '}
                <button
                  className="text-accent hover:underline"
                  onClick={() => window.dispatchEvent(new Event('open-models'))}
                >
                  {t('Models page')}
                </button>
              </span>
            </div>
            {pullError && (
              <p className="text-xs text-warn mt-1">
                {t('Download failed')}: {pullError}
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-muted mt-1">
            {reason || t('No model available.')}
            {settingsFor && (
              <>
                {' '}
                <button
                  className="text-accent hover:underline"
                  title={t('Opens Settings → AI, where you add your key or sign in.')}
                  onClick={() => openAISetup(settingsFor)}
                >
                  {t('Add your key in Settings')}
                </button>
              </>
            )}
          </p>
        )}
      </section>
    )
  }

  return (
    <section
      className="card flex flex-col h-full min-h-0 overflow-hidden"
      aria-label={t('Assistant')}
    >
      <div className="flex items-baseline justify-between gap-2 shrink-0">
        <p className="font-semibold">{t('Ask Clips Kitty')}</p>
        {picker}
      </div>
      <div ref={scrollRef} className="mt-3 flex-1 min-h-0 overflow-y-auto space-y-3 pr-1">
        {turns.length === 0 ? (
          /* Examples rather than a paragraph. The paragraph that was here got
             cut in half whenever the panel was dragged short, and a wall of
             prose is the wrong shape for an empty state anyway: nobody has to
             invent the phrasing if they can start from a real one. Clicking
             fills the box instead of sending, so it can be edited first. */
          <div className="flex flex-wrap gap-1.5 items-baseline">
            <span className="text-sm text-muted mr-0.5">{t('Try:')}</span>
            {[
              t('Clip this stream: '),
              t('Publish all my clips, 5 a day'),
              t('My best clips this week')
            ].map((example) => (
              <button
                key={example}
                onClick={() => setInput(example)}
                className="text-xs px-2 py-1 rounded-full border border-raised text-muted hover:border-accent hover:text-ink transition-colors"
              >
                {example.trim()}
              </button>
            ))}
          </div>
        ) : (
          turns.map((turn, i) => (
            <div key={i} className={turn.role === 'user' ? 'text-right' : ''}>
              <p
                className={
                  turn.role === 'user'
                    ? 'inline-block bg-raised rounded-lg px-2.5 py-1 text-sm text-left'
                    : 'text-sm whitespace-pre-wrap'
                }
              >
                {turn.text}
              </p>
              {turn.steps && turn.steps.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {turn.steps.map((step, j) => (
                    <li key={j} className="text-xs text-muted font-mono">
                      {step.tool}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))
        )}
      </div>

      {plan && (
        // The plan gives way rather than growing: the card clips what does not
        // fit, and what sits below it is the input.
        <div className="mt-2 border border-accent/40 rounded-lg p-2 space-y-1 min-h-0 overflow-y-auto">
          <p className="text-xs font-medium">
            {schedule
              ? `${plan.length} ${t('clips ready to post to')} ${schedule.platforms.join(', ')}${
                  schedule.per_day
                    ? `, ${schedule.per_day} ${t('a day')}`
                    : schedule.every_hours
                      ? `, ${t('one every')} ${schedule.every_hours}h`
                      : ''
                }. ${t('Nothing has been posted yet.')}`
              : `${plan.length} ${t('clips ready to upload. Nothing has been uploaded yet.')}`}
          </p>
          {schedule && schedule.hashtags.length > 0 && (
            <p className="text-[11px] text-muted">
              {t('Adding')} {schedule.hashtags.map((h) => `#${h}`).join(' ')}
            </p>
          )}
          {plan.slice(0, 6).map((item) => (
            <p key={item.clip_id} className="text-[11px] text-muted">
              {item.title.slice(0, 48)} · {item.publish_at || t('as soon as it uploads')}
            </p>
          ))}
          {planNote && <p className="text-[11px] text-warn">{planNote}</p>}
          <div className="flex gap-2 pt-1">
            <button className="btn-accent !py-1 text-xs" disabled={busy} onClick={confirmPlan}>
              {schedule ? t('Schedule these') : t('Upload these')}
            </button>
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy}
              onClick={() => { setPlan(null); setSchedule(null) }}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-3 shrink-0">
        <input
          className="input flex-1"
          value={input}
          disabled={busy || ready === null}
          placeholder={ready === null ? t('Checking…') : t('Ask for something…')}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void send()
          }}
        />
        <button className="btn-accent !py-1" disabled={busy || !input.trim()} onClick={send}>
          {busy ? t('Working…') : t('Ask')}
        </button>
      </div>
    </section>
  )
}
