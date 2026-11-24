import type { CaptionPreset, CaptionSafeZone, CaptionSize, CaptionStyle } from '../lib/types'
import { t } from '../lib/i18n'
import { captionPlace, presetCss, safeZoneArea, useBundledFonts, useCaptionPresets } from '../lib/captionLook'

/** video/caption_presets.py DEFAULT: the editor's Bold caption, Medium, at the Bottom,
 *  clear of TikTok's buttons and text (it ends at y=1480, where captions always did). */
export const DEFAULT_CAPTION_STYLE: Required<CaptionStyle> = {
  preset: 'concat.bold-caption',
  size: 'medium',
  position: 'bottom',
  words_per_caption: 3,
  second_speaker: false,
  second_speaker_color: '#5CE1FF',
  safe_zone: 'tiktok'
}

/** Fonts on every stock Windows install, for the watermark's font picker
 *  (captions use the editor's presets and fonts only). */
export const CAPTION_FONTS = [
  'Arial',
  'Arial Black',
  'Impact',
  'Verdana',
  'Tahoma',
  'Trebuchet MS',
  'Segoe UI',
  'Georgia',
  'Comic Sans MS',
  'Courier New'
]

/** The editor's Captions sheet: Small, Medium, Large. */
const SIZES: [CaptionSize, string][] = [
  ['small', 'Small'],
  ['medium', 'Medium'],
  ['large', 'Large']
]

/** video/caption_presets.py SAFE_ZONE_CHOICES. */
const SAFE_ZONES: [CaptionSafeZone, string][] = [
  ['off', 'Off'],
  ['tiktok', 'TikTok'],
  ['reels', 'Instagram Reels'],
  ['shorts', 'YouTube Shorts']
]

// A style saved before captions used the editor's presets still has its old
// font and size: they decide the preset and size, as
// video/caption_presets.py normalize() does on the engine.
const OLD_KEYS = ['font', 'font_size', 'color', 'uppercase', 'highlight', 'highlight_color', 'animation']
const OLD_FONT_PRESETS: Record<string, string> = {
  'bebas neue': 'concat.poster',
  anton: 'concat.impact',
  impact: 'concat.impact',
  bangers: 'concat.comic',
  'comic sans ms': 'concat.comic',
  georgia: 'concat.editorial'
}
const OLD_SCALES: Record<string, number> = { anton: 1.7334, bangers: 1.757, 'bebas neue': 1.3, 'archivo black': 1.347 }

/** The style as the controls show it: an old saved one as its preset. */
export function captionStyleOf(style: Required<CaptionStyle>): Required<CaptionStyle> {
  const raw = style as Required<CaptionStyle> & Record<string, unknown>
  if (!('font' in raw) && !('font_size' in raw)) return style
  const font = String(raw.font ?? '').toLowerCase()
  const out = { ...style }
  if (style.preset === DEFAULT_CAPTION_STYLE.preset) out.preset = OLD_FONT_PRESETS[font] ?? DEFAULT_CAPTION_STYLE.preset
  const px = Number(raw.font_size)
  if (style.size === DEFAULT_CAPTION_STYLE.size && Number.isFinite(px)) {
    const em = px / (OLD_SCALES[font] ?? 1.117) / 1920
    out.size = em < 0.045 ? 'small' : em < 0.0575 ? 'medium' : 'large'
  }
  if (style.safe_zone === 'off') out.safe_zone = DEFAULT_CAPTION_STYLE.safe_zone // old captions ended at 1480 too
  for (const k of OLD_KEYS) delete (out as Record<string, unknown>)[k]
  return out
}

const PREVIEW_H = 240 // the preview frame's height in px (9:16)
const PREVIEW_W = (PREVIEW_H * 9) / 16

/** A live preview of the burned captions: the preset's look at the chosen
 *  size and place on a 9:16 frame, drawn as the editor draws a title, with
 *  the safe zone shaded when one is on. */
function CaptionPreview({
  style,
  preset
}: {
  style: Required<CaptionStyle>
  preset: CaptionPreset | undefined
}): JSX.Element {
  const words = ['your', 'captions', 'look', 'like', 'this', 'onscreen']
  const text = words.slice(0, Math.max(1, Math.min(6, style.words_per_caption))).join(' ')
  const place = captionPlace(style, 1080, 1920)
  const zone = safeZoneArea(style.safe_zone)
  const emPx = place.em * PREVIEW_H
  const side = (place.side / 1080) * PREVIEW_W
  return (
    <div
      className="relative rounded-lg bg-gradient-to-br from-slate-600 via-slate-700 to-slate-900 overflow-hidden mx-auto"
      style={{ width: PREVIEW_W, height: PREVIEW_H }}
      aria-label={t('Caption style example')}
      title={zone ? t('The shaded areas are where the app puts its own buttons and text.') : undefined}
    >
      {zone && (
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox="0 0 1080 1920"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <g
            fill="white"
            fillOpacity={0.13}
            stroke="white"
            strokeOpacity={0.35}
            strokeWidth={6}
            strokeDasharray="24 16"
          >
            <rect x={0} y={0} width={1080} height={zone.top} />
            <rect x={0} y={1920 - zone.bottom} width={1080} height={zone.bottom} />
            <rect x={1080 - zone.right} y={zone.top} width={zone.right} height={1920 - zone.top - zone.bottom} />
          </g>
        </svg>
      )}
      {preset && (
        <div
          className="absolute flex justify-center"
          style={{
            left: side,
            right: side,
            top: `${(0.5 + place.offset) * 100}%`,
            transform: 'translateY(-50%)'
          }}
        >
          <p className="text-center" style={presetCss(preset.style, emPx, PREVIEW_H)}>
            {text}
            {/* The other speaker's caption, in its colour: the two colours
                side by side are what is being chosen. */}
            {style.second_speaker && (
              <span className="block" style={{ color: style.second_speaker_color }}>
                {t('second speaker')}
              </span>
            )}
          </p>
        </div>
      )}
    </div>
  )
}

/** The caption style controls: the editor's text presets, the size and place
 *  the editor's Captions sheet offers, words per caption, the second
 *  speaker's colour and a safe zone, with a live preview. Shared between the
 *  Dashboard (style for new clips) and a video's own settings in the queue. */
export default function CaptionStyleControls({
  idPrefix,
  style: given,
  onChange,
  onPick,
  hideWordsPerCaption = false,
  hideSecondSpeaker = false
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** A change as the whole new style. Without it the changed fields go
   *  through onChange one by one, which needs an onChange that builds on the
   *  latest style (a functional state update). */
  onPick?: (style: Required<CaptionStyle>) => void
  hideWordsPerCaption?: boolean
  hideSecondSpeaker?: boolean
}): JSX.Element {
  useBundledFonts() // registers the editor's fonts for the cards and preview
  const presets = useCaptionPresets()
  const style = captionStyleOf(given)
  const chosen = presets.find((p) => p.id === style.preset)

  // Every change writes the whole style in the new shape, so an old saved
  // style's font and colour keys go with the first change.
  const set = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    const next = { ...style, [key]: value }
    if (onPick) {
      onPick(next)
      return
    }
    const old = given as Required<CaptionStyle> & Record<string, unknown>
    for (const k of OLD_KEYS) {
      if (k in old) onChange(k as keyof CaptionStyle, undefined)
    }
    for (const k of Object.keys(next) as (keyof CaptionStyle)[]) {
      if (next[k] !== given[k]) onChange(k, next[k])
    }
  }

  return (
    <>
      <div>
        <p
          className="label mb-1"
          title={t(
            "Clips Kitty Editor's text styles. Your clips' captions are burned in this style, and look the same when you open a clip in the editor."
          )}
        >
          {t('Style')}
          {chosen ? `: ${t(chosen.name)}` : ''}
        </p>
        <div
          className="grid grid-cols-4 sm:grid-cols-5 gap-1.5 max-h-48 overflow-y-auto pr-1"
          role="group"
          aria-label={t('Caption styles')}
        >
          {presets.map((p) => {
            const on = p.id === style.preset
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => set('preset', p.id)}
                aria-pressed={on}
                title={t(p.name)}
                className={`h-12 rounded-lg border flex items-center justify-center overflow-hidden bg-slate-800 transition-colors ${
                  on ? 'border-accent ring-1 ring-accent' : 'border-raised hover:border-muted'
                }`}
              >
                <span style={{ ...presetCss(p.style, 20, 400), whiteSpace: 'nowrap' }}>{p.sample || 'Aa'}</span>
              </button>
            )
          })}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor={`${idPrefix}-size`} className="label">
            {t('Size')}
          </label>
          <select
            id={`${idPrefix}-size`}
            className="input mt-1"
            value={style.size}
            title={t('The same sizes as the Captions sheet in Clips Kitty Editor.')}
            onChange={(e) => set('size', e.target.value as CaptionSize)}
          >
            {SIZES.map(([v, label]) => (
              <option key={v} value={v}>
                {t(label)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-pos`} className="label">
            {t('Position')}
          </label>
          <select
            id={`${idPrefix}-pos`}
            className="input mt-1"
            value={style.position}
            onChange={(e) => set('position', e.target.value as CaptionStyle['position'])}
          >
            <option value="bottom">{t('Bottom')}</option>
            <option value="middle">{t('Middle')}</option>
            <option value="top">{t('Top')}</option>
          </select>
        </div>
        {!hideWordsPerCaption && (
          <div>
            <label htmlFor={`${idPrefix}-words`} className="label">
              {t('Words per caption')}
            </label>
            <select
              id={`${idPrefix}-words`}
              className="input mt-1"
              value={style.words_per_caption}
              onChange={(e) => set('words_per_caption', Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label htmlFor={`${idPrefix}-safe`} className="label">
            {t('Safe zone')}
          </label>
          <select
            id={`${idPrefix}-safe`}
            className="input mt-1"
            value={style.safe_zone}
            title={t(
              'Keeps captions away from the buttons and text the app puts over your video. Only changes vertical clips.'
            )}
            onChange={(e) => set('safe_zone', e.target.value as CaptionSafeZone)}
          >
            {SAFE_ZONES.map(([v, label]) => (
              <option key={v} value={v}>
                {t(label)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!hideSecondSpeaker && (
        <div className="flex items-center gap-3">
          <label
            className="flex items-center gap-2 cursor-pointer text-sm"
            title={t(
              "When two people talk in a clip, the main speaker keeps the style's colour and the other person's captions take this one. A clip with one voice looks the same as always."
            )}
          >
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={style.second_speaker}
              onChange={(e) => set('second_speaker', e.target.checked)}
            />
            {t('Second speaker in another colour')}
          </label>
          {style.second_speaker && (
            <input
              type="color"
              aria-label={t("Second speaker's colour")}
              className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
              value={style.second_speaker_color}
              onChange={(e) => set('second_speaker_color', e.target.value.toUpperCase())}
            />
          )}
        </div>
      )}

      <div>
        <p className="label mb-1">{t('Example')}</p>
        <CaptionPreview style={style} preset={chosen} />
      </div>
    </>
  )
}
