import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'

/** Thumbnail picker, in the shape YouTube Studio uses: a few frames suggested
 *  from the video, plus your own image.
 *
 *  Two things worth knowing, both surfaced in the UI rather than buried:
 *  a custom thumbnail needs a phone-verified channel, and the Shorts feed
 *  ignores custom thumbnails entirely — they show on the watch page and on
 *  your channel, which is still worth having.
 */

interface Props {
  clipId: number
  /** The clip's media_version: frames are asked for again when the file
   *  changes (an editor export keeps the clip's id). */
  version?: string
  duration: number
  value: string | null
  onChange: (thumbnail: string | null) => void
  disabled?: boolean
}

export default function YouTubeThumbnail({
  clipId,
  version,
  duration,
  value,
  onChange,
  disabled
}: Props): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [chosenAt, setChosenAt] = useState<number | null>(null)
  // Candidates made from the clip itself. Count, not images: each one is
  // fetched by URL like the fixed suggestions are.
  const [generated, setGenerated] = useState(0)
  const [chosenGenerated, setChosenGenerated] = useState<number | null>(null)
  // A frame of your own (KC): the slider's time, and the still shown for it,
  // fetched once the slider rests so dragging does not ask for every frame.
  const [frameAt, setFrameAt] = useState(0)
  const [stillAt, setStillAt] = useState(0)
  // The slider stops a little before the end: a seek to the file's exact
  // length gives no frame.
  const lastFrame = Math.max(0, Math.floor((duration - 0.1) * 10) / 10)
  useEffect(() => {
    const timer = window.setTimeout(() => setStillAt(frameAt), 250)
    return () => window.clearTimeout(timer)
  }, [frameAt])

  // The same three positions Studio offers. Recomputed only when the clip
  // changes, so the <img> elements are not rebuilt on every playhead move.
  const suggestions = useMemo(() => {
    const length = duration > 0 ? duration : 0
    return [0.25, 0.5, 0.75].map((fraction) => length * fraction)
  }, [duration, clipId])

  const pick = async (at: number): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const { thumbnail } = await api.chooseThumbnail(clipId, { t: at })
      onChange(thumbnail)
      setChosenAt(at)
      setChosenGenerated(null)
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const generate = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const { generated: made } = await api.generateThumbnails(clipId)
      setGenerated(made)
      if (made === 0) {
        setNotice(t('No usable frames in this clip. The suggestions above still work.'))
      }
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const pickGenerated = async (index: number): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const { thumbnail } = await api.chooseThumbnail(clipId, { generated: index })
      onChange(thumbnail)
      setChosenGenerated(index)
      setChosenAt(null)
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const upload = async (): Promise<void> => {
    // The dialog runs in Electron's main process, which reads the file there
    // and hands back the bytes. Nothing sends a path to the backend.
    const picked = await window.studio.pickThumbnailImage()
    if (!picked) return
    if ('error' in picked) {
      setNotice(
        picked.error === 'too-large'
          ? t("That image is over YouTube's 2 MB limit for thumbnails.")
          : t('That image could not be read.')
      )
      return
    }
    setBusy(true)
    setNotice('')
    try {
      const { thumbnail } = await api.chooseThumbnail(clipId, { image: picked.data })
      onChange(thumbnail)
      setChosenAt(null)
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="border border-raised/60 rounded-lg p-3 space-y-2">
      <legend className="label px-1">{t('Thumbnail')}</legend>

      <div className="grid grid-cols-3 gap-2">
        {suggestions.map((at, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled || busy || duration <= 0}
            onClick={() => pick(at)}
            aria-label={`${t('Use the frame at')} ${at.toFixed(1)}s`}
            className={`relative rounded-md overflow-hidden border-2 transition-colors ${
              chosenAt === at ? 'border-accent' : 'border-transparent hover:border-raised'
            }`}
          >
            <img
              src={api.clipFrameUrl(clipId, at, version)}
              alt=""
              loading="lazy"
              className="w-full aspect-video object-cover bg-raised"
            />
          </button>
        ))}
      </div>

      {generated > 0 && (
        <div className="grid grid-cols-3 gap-2">
          {Array.from({ length: generated }, (_, i) => (
            <button
              key={`gen-${i}`}
              type="button"
              disabled={disabled || busy}
              onClick={() => pickGenerated(i)}
              aria-label={`${t('Use generated thumbnail')} ${i + 1}`}
              className={`relative rounded-md overflow-hidden border-2 transition-colors ${
                chosenGenerated === i ? 'border-accent' : 'border-transparent hover:border-raised'
              }`}
            >
              <img
                src={api.generatedThumbnailUrl(clipId, i)}
                alt=""
                loading="lazy"
                className="w-full aspect-video object-cover bg-raised"
              />
            </button>
          ))}
        </div>
      )}

      {duration > 0 && (
        <div className="flex items-center gap-3">
          <img
            src={api.clipFrameUrl(clipId, Math.min(stillAt, lastFrame), version)}
            alt=""
            className="w-24 aspect-video object-cover rounded-md bg-raised shrink-0"
          />
          <div className="flex-1 min-w-0 space-y-1">
            <label className="flex items-center gap-2 text-xs">
              <span className="shrink-0">{t('Frame at')}</span>
              <input
                type="range"
                min={0}
                max={lastFrame}
                step={0.1}
                value={Math.min(frameAt, lastFrame)}
                onChange={(e) => setFrameAt(Number(e.target.value))}
                disabled={disabled || busy}
                className="flex-1 min-w-0"
                title={t('Slide to pick any moment of the clip. The picture beside it shows that frame.')}
              />
              <span className="tabular-nums w-12 text-right">{Math.min(frameAt, lastFrame).toFixed(1)}s</span>
            </label>
            <button
              type="button"
              className={`btn-ghost !px-2.5 !py-1 text-xs ${chosenAt === stillAt && chosenAt === Math.min(frameAt, lastFrame) ? 'text-accent' : ''}`}
              disabled={disabled || busy}
              onClick={() => {
                const at = Math.min(frameAt, lastFrame)
                setStillAt(at)
                void pick(at)
              }}
              title={t('Use the frame shown beside the slider as the thumbnail.')}
            >
              {t('Use this frame')}
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-ghost !px-2.5 !py-1 text-xs"
          disabled={disabled || busy}
          onClick={generate}
        >
          {generated > 0 ? t('Make new ones') : t('Make thumbnails')}
        </button>
        <button
          type="button"
          className="btn-ghost !px-2.5 !py-1 text-xs"
          disabled={disabled || busy}
          onClick={upload}
        >
          {t('Upload an image…')}
        </button>
        {value && (
          <button
            type="button"
            className="btn-ghost !px-2.5 !py-1 text-xs"
            disabled={disabled || busy}
            onClick={() => {
              onChange(null)
              setChosenAt(null)
              setChosenGenerated(null)
            }}
          >
            {t('Clear')}
          </button>
        )}
      </div>

      {value && (
        <p className="text-[11px] text-success">{t('Thumbnail ready to upload.')}</p>
      )}
      {notice && <p className="text-[11px] text-error">{notice}</p>}
      <p className="text-[11px] text-muted">
        {t(
          'Custom thumbnails need a phone-verified YouTube channel, and the Shorts feed does not show them - they appear on the watch page and your channel. JPEG or PNG, up to 2 MB.'
        )}
      </p>
    </fieldset>
  )
}
