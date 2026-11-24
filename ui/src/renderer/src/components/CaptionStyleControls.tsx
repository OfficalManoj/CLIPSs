import { useRef, useState } from 'react'
import type { CaptionPreset, CaptionSafeZone, CaptionSize, CaptionSpot, CaptionStyle } from '../lib/types'
import { t } from '../lib/i18n'
import { captionPlace, presetCss, safeZoneArea, useBundledFonts, useCaptionPresets } from '../lib/captionLook'
import { dragLayer, type Box, type Targets } from '../lib/layerDrag'
import PlatformOverlay from './PlatformOverlay'

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
export function captionStyleOf<S extends Required<CaptionStyle>>(style: S): S {
  const raw = style as S & Record<string, unknown>
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

/** A caption style with a dragged spot, if it has one. */
type Placed = Required<CaptionStyle> & CaptionSpot

const PREVIEW_H = 320 // the preview frame's height in px (9:16)
const PREVIEW_W = (PREVIEW_H * 9) / 16
const W = 1080 // the frame the places are worked out on
const H = 1920
const SNAP_PX = 6 // screen px that count as lined up
const STEP = 0.01 // an arrow key's nudge, a share of the frame (Shift: BIG_STEP)
const BIG_STEP = 0.05
const ROWS = ['top', 'middle', 'bottom'] as const

const fixed = (v: number): number => Math.round(v * 10000) / 10000
const has = (v: number | null | undefined): v is number => typeof v === 'number'

/** A live preview of the burned captions on a 9:16 frame: the preset's look
 *  at the chosen size, drawn as the editor draws a title, with the chosen
 *  app's own buttons faded over it. The captions can be dragged anywhere
 *  (snapping to the middle, the edges, the Top, Middle and Bottom rows and
 *  the app's edges; Alt places them freely) or moved with the arrow keys.
 *  Where they are put is where they stay: when the app's buttons cover
 *  that spot they get an amber outline, and a dashed one shows where the
 *  clips burn them, just clear (video/caption_presets.py). */
function CaptionPreview({
  style,
  preset,
  onPlace
}: {
  style: Placed
  preset: CaptionPreset | undefined
  onPlace: (spot: CaptionSpot | null) => void
}): JSX.Element {
  const frameRef = useRef<HTMLDivElement>(null)
  const textRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ box: Box; ox: number; oy: number } | null>(null)
  const [moving, setMoving] = useState<CaptionSpot | null>(null) // saved when the drag ends
  const [guides, setGuides] = useState<Targets>({ xs: [], ys: [] })

  const words = ['your', 'captions', 'look', 'like', 'this', 'onscreen']
  const text = words.slice(0, Math.max(1, Math.min(6, style.words_per_caption))).join(' ')
  const spot: CaptionSpot = moving ?? { x: style.x, y: style.y }
  const look = preset?.style
  const zone = safeZoneArea(style.safe_zone)
  // Where the clips burn them (inside the frame and clear of the app), and
  // where they were put (inside the frame only).
  const burn = captionPlace({ ...style, ...spot }, W, H, look)
  const free = captionPlace({ ...style, ...spot, safe_zone: 'off' }, W, H, look)
  const shownX = has(spot.x) ? free.column : burn.column
  const shownY = has(spot.y) ? free.offset : burn.offset
  const covered =
    (has(spot.x) && Math.abs(free.column - burn.column) * W >= 1) ||
    (has(spot.y) && Math.abs(free.offset - burn.offset) * H >= 1)
  const emPx = burn.em * PREVIEW_H
  const wide = `${((W - 2 * burn.side) / W) * 100}%`

  /** A spot held inside the frame, as the burn holds it (4 places). */
  const inFrame = (x: number, y: number): { x: number; y: number } => {
    const at = captionPlace({ ...style, x, y, safe_zone: 'off' }, W, H, look)
    return { x: fixed(0.5 + at.column), y: fixed(0.5 + at.offset) }
  }

  // Lines to snap to: the frame's middle and edges, the Top, Middle and
  // Bottom rows, and the edges of the app's buttons and bars.
  const targets = (): Targets => {
    const xs = [0, W / 2, W]
    const ys = [0, H / 2, H]
    for (const position of ROWS) ys.push(H * (0.5 + captionPlace({ ...style, position, x: null, y: null }, W, H).offset))
    if (zone) {
      xs.push(W - zone.right)
      ys.push(zone.top, H - zone.bottom)
    }
    return { xs, ys }
  }

  const start = (e: React.PointerEvent<HTMLDivElement>): void => {
    const frame = frameRef.current?.getBoundingClientRect()
    const box = textRef.current?.getBoundingClientRect()
    if (!frame || !box || e.button !== 0) return
    const k = W / frame.width
    drag.current = {
      box: [(box.left - frame.left) * k, (box.top - frame.top) * k, box.width * k, box.height * k],
      ox: e.clientX,
      oy: e.clientY
    }
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  const move = (e: React.PointerEvent<HTMLDivElement>): void => {
    const d = drag.current
    const frame = frameRef.current?.getBoundingClientRect()
    if (!d || !frame) return
    const k = W / frame.width
    const { box, guides: lines } = dragLayer(d.box, 'move', (e.clientX - d.ox) * k, (e.clientY - d.oy) * k, {
      aspect: null,
      canvas: [W, H],
      minW: 0,
      minH: 0,
      targets: e.altKey ? null : targets(), // hold Alt to place freely
      tol: SNAP_PX * k
    })
    setGuides(lines)
    setMoving(inFrame((box[0] + box[2] / 2) / W, (box[1] + box[3] / 2) / H))
  }
  const end = (): void => {
    if (drag.current && moving) onPlace(moving)
    drag.current = null
    setMoving(null)
    setGuides({ xs: [], ys: [] })
  }
  const nudge = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    const step = e.shiftKey ? BIG_STEP : STEP
    const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0
    const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0
    if (!dx && !dy) return
    e.preventDefault()
    const next = inFrame(0.5 + shownX + dx, 0.5 + shownY + dy)
    // Up and down alone keep a caption that was never moved across in the middle.
    onPlace({ x: has(style.x) || dx ? next.x : null, y: next.y })
  }

  return (
    <div className="space-y-2">
      <div
        ref={frameRef}
        className="relative rounded-lg bg-gradient-to-br from-slate-600 via-slate-700 to-slate-900 overflow-hidden mx-auto select-none"
        style={{ width: PREVIEW_W, height: PREVIEW_H }}
        aria-label={t('Caption style example')}
      >
        {zone && (
          <div className="absolute inset-0 opacity-40 pointer-events-none">
            <PlatformOverlay platform={style.safe_zone} />
          </div>
        )}
        {preset && covered && (
          // Where the clips burn the captions: just clear of the app.
          <div
            className="absolute flex justify-center pointer-events-none"
            style={{
              left: `${(0.5 + burn.column) * 100}%`,
              top: `${(0.5 + burn.offset) * 100}%`,
              width: wide,
              transform: 'translate(-50%, -50%)'
            }}
            aria-hidden="true"
          >
            <p
              className="text-center rounded-sm outline-dashed outline-2 outline-amber-400"
              style={{ ...presetCss(preset.style, emPx, PREVIEW_H), color: 'transparent', WebkitTextStroke: undefined, textShadow: undefined, background: 'transparent' }}
            >
              {text}
            </p>
          </div>
        )}
        {(guides.xs.length > 0 || guides.ys.length > 0) && (
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {guides.xs.map((gx) => (
              <line key={`x${gx}`} x1={gx} x2={gx} y1={0} y2={H} stroke="#F472B6" strokeWidth={6} />
            ))}
            {guides.ys.map((gy) => (
              <line key={`y${gy}`} x1={0} x2={W} y1={gy} y2={gy} stroke="#F472B6" strokeWidth={6} />
            ))}
          </svg>
        )}
        {preset && (
          <div
            className="absolute flex justify-center pointer-events-none"
            style={{
              left: `${(0.5 + shownX) * 100}%`,
              top: `${(0.5 + shownY) * 100}%`,
              width: wide,
              transform: 'translate(-50%, -50%)'
            }}
          >
            <div
              ref={textRef}
              role="button"
              tabIndex={0}
              aria-label={t('Captions. Move them with the arrow keys, or hold Shift for bigger steps.')}
              title={
                covered
                  ? t(
                      "The app's buttons and text cover this spot. Your clips put the captions just clear of them, where the dashed outline is."
                    )
                  : t(
                      'Drag the captions anywhere. They snap to the middle, the edges and the Top, Middle and Bottom rows. Hold Alt to place them freely.'
                    )
              }
              className={`pointer-events-auto cursor-move touch-none rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                covered ? 'outline outline-2 outline-amber-400' : ''
              }`}
              onPointerDown={start}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={end}
              onKeyDown={nudge}
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
          </div>
        )}
      </div>
      {(has(style.x) || has(style.y)) && (
        <div className="flex justify-center">
          <button
            type="button"
            className="btn-ghost !py-1 !px-3 text-xs"
            onClick={() => onPlace(null)}
            title={t('Puts the captions back in the middle, at the Position chosen above.')}
          >
            {t('Reset position')}
          </button>
        </div>
      )}
    </div>
  )
}

/** The caption style controls: the editor's text presets, the size and place
 *  the editor's Captions sheet offers, words per caption, the second
 *  speaker's colour and a safe zone, with a live preview the captions can
 *  be dragged on. Shared between the
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
  style: Placed
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** A change as the whole new style. Without it the changed fields go
   *  through onChange one by one, which needs an onChange that builds on the
   *  latest style (a functional state update). */
  onPick?: (style: Placed) => void
  hideWordsPerCaption?: boolean
  hideSecondSpeaker?: boolean
}): JSX.Element {
  useBundledFonts() // registers the editor's fonts for the cards and preview
  const presets = useCaptionPresets()
  const style = captionStyleOf(given)
  const chosen = presets.find((p) => p.id === style.preset)

  // Every change writes the whole style in the new shape, so an old saved
  // style's font and colour keys go with the first change.
  const commit = (next: Placed): void => {
    if (onPick) {
      onPick(next)
      return
    }
    const old = given as Placed & Record<string, unknown>
    for (const k of OLD_KEYS) {
      if (k in old) onChange(k as keyof CaptionStyle, undefined)
    }
    const fields = new Set<string>([...Object.keys(next), 'x', 'y'])
    for (const k of fields) {
      const value = (next as unknown as Record<string, unknown>)[k]
      if (value !== old[k]) onChange(k as keyof CaptionStyle, value as never)
    }
  }
  const set = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => commit({ ...style, [key]: value })
  /** A dragged spot, or null to go back to the named position. */
  const place = (spot: CaptionSpot | null): void => {
    const next: Placed = { ...style }
    delete next.x
    delete next.y
    if (spot && typeof spot.x === 'number') next.x = spot.x
    if (spot && typeof spot.y === 'number') next.y = spot.y
    commit(next)
  }
  const dragged = typeof style.y === 'number'

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
            value={dragged ? 'dragged' : style.position}
            title={t('Top, Middle and Bottom are the rows Clips Kitty Editor offers. You can also drag the captions in the example below.')}
            onChange={(e) => {
              // A named row takes the place of a dragged one.
              const next: Placed = { ...style, position: e.target.value as Placed['position'] }
              delete next.y
              commit(next)
            }}
          >
            {dragged && (
              <option value="dragged" disabled>
                {t('Where you dragged it')}
              </option>
            )}
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
        <CaptionPreview style={style} preset={chosen} onPlace={place} />
      </div>
    </>
  )
}
