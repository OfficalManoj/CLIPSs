/** What the caption previews need to look like the burned captions, which
 *  are Clips Kitty Editor's own text presets in its own fonts
 *  (video/caption_presets.py):
 *
 *  - Fonts: GET /fonts lists the app's font families, each with its files.
 *    Each file is registered with the FontFace API at its own weight and
 *    style, from the engine's own URL, so the preview draws with the very
 *    file the render burns. `display: swap` shows the fallback font while a
 *    face loads.
 *  - Presets: GET /caption-presets (config/caption_presets.json), in the
 *    editor's order.
 *  - Size and place: the editor's Captions sheet (Small 0.04, Medium 0.05,
 *    Large 0.065 of the frame's height; Bottom 0.35, Middle 0, Top -0.35 from
 *    its centre), moved clear of an app's buttons with a safe zone, as
 *    video/caption_presets.py _offset does. A dragged caption (x, y) sits
 *    where it was put, held inside the frame and the zone, its lines
 *    wrapping narrower near an edge (caption_presets.py _across). The safe zones come from
 *    gaming/layouts.json (lib/gamingLayouts.ts), which
 *    video/caption_presets.py SAFE_ZONES equals (tests keep both the same). */
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { api, API_BASE } from './api'
import { LAYOUTS } from './gamingLayouts'
import type { BundledFont, CaptionPreset, CaptionSpot, CaptionStyle, PresetLook } from './types'

let fonts: BundledFont[] = []
let fontsLoading: Promise<BundledFont[]> | null = null
const fontListeners = new Set<(f: BundledFont[]) => void>()

/** Fetch the app's fonts once and register their faces. A failure (the
 *  engine not up yet) is not remembered, so the next caller tries again. */
export function loadBundledFonts(): Promise<BundledFont[]> {
  if (!fontsLoading) {
    fontsLoading = api
      .bundledFonts()
      .then((res) => {
        const list = Array.isArray(res?.fonts) ? res.fonts : []
        for (const f of list) {
          ;(f.urls ?? []).forEach((url, i) => {
            const face = f.faces?.[i]
            try {
              const ff = new FontFace(f.family, `url("${API_BASE}${url}")`, {
                weight: String(face?.weight ?? f.weight ?? 400),
                style: face?.italic ? 'italic' : 'normal',
                display: 'swap'
              })
              document.fonts.add(ff)
              ff.load().catch(() => undefined) // the name stays in the fallback font
            } catch {
              // An odd name or URL: that one face previews in the fallback.
            }
          })
        }
        fonts = list
        fontListeners.forEach((fn) => fn(list))
        return list
      })
      .catch(() => {
        fontsLoading = null
        return fonts
      })
  }
  return fontsLoading
}

/** The app's fonts, [] until they have loaded (or with no engine). */
export function useBundledFonts(): BundledFont[] {
  const [list, setList] = useState<BundledFont[]>(fonts)
  useEffect(() => {
    fontListeners.add(setList)
    loadBundledFonts().then(setList)
    return () => {
      fontListeners.delete(setList)
    }
  }, [])
  return list
}

let presets: CaptionPreset[] = []
let presetsLoading: Promise<CaptionPreset[]> | null = null

/** The editor's text presets, in its order ([] until loaded). */
export function useCaptionPresets(): CaptionPreset[] {
  const [list, setList] = useState<CaptionPreset[]>(presets)
  useEffect(() => {
    let alive = true
    if (!presetsLoading) {
      presetsLoading = api
        .captionPresets()
        .then((res) => {
          presets = (Array.isArray(res?.presets) ? res.presets : []).filter(
            (p) => p && typeof p.id === 'string' && typeof p.name === 'string' && p.style
          )
          return presets
        })
        .catch(() => {
          presetsLoading = null
          return presets
        })
    }
    presetsLoading.then((p) => {
      if (alive) setList(p)
    })
    return () => {
      alive = false
    }
  }, [])
  return list
}

export interface SafeZoneArea {
  top: number
  bottom: number
  right: number
}

/** The area an app covers on a 1080x1920 Short, or null for "off". */
export function safeZoneArea(zone: string | undefined): SafeZoneArea | null {
  if (zone !== 'tiktok' && zone !== 'reels' && zone !== 'shorts') return null
  const z = LAYOUTS.safe_zones[zone]
  return { top: z.top, bottom: z.bottom, right: z.right }
}

export const CAPTION_SIZES = { small: 0.04, medium: 0.05, large: 0.065 } // captions.rs CAPTION_SIZES
const PLACES = { bottom: 0.35, middle: 0, top: -0.35 } // captions.rs CAPTION_OFFSETS
const SAFE_CLEARANCE = 20 // video/caption_presets.py SAFE_CLEARANCE
const INK_SPREAD = 9 // video/caption_presets.py INK_SPREAD
const SIDE_MARGIN = 60 // video/caption_presets.py SIDE_MARGIN

const MIN_WIDTH = 0.4 // video/caption_presets.py MIN_WIDTH

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

/** How far a preset's ink reaches past its letters at the sides, in px on
 *  a frame h tall: video/caption_presets.py _spread. */
function inkSpread(look: PresetLook, em: number, h: number): number {
  if (look.background) return (look.backgroundPaddingY || 0) * h
  return ((look.strokeWidth || 0) * h) / 2 + (look.shadow ? 0.05 * em * h : 0)
}

/** A caption's em and where its block's centre sits on a w x h frame: the
 *  offset down from the centre as a share of the height, the column across
 *  from the centre as a share of the width (0 unless dragged), and the
 *  margin each side of that column in the frame's pixels (the lines wrap
 *  at w - 2 * side): video/caption_presets.py look(). `look` is the
 *  preset's, for how far its ink reaches (a dragged caption only). */
export function captionPlace(
  style: Pick<CaptionStyle, 'size' | 'position' | 'safe_zone'> & CaptionSpot,
  w: number,
  h: number,
  look?: PresetLook
): { em: number; offset: number; column: number; side: number } {
  const em = CAPTION_SIZES[style.size ?? 'medium'] ?? CAPTION_SIZES.medium
  const position = style.position === 'top' || style.position === 'middle' ? style.position : 'bottom'
  let offset = PLACES[position]
  let side = SIDE_MARGIN
  let column = 0
  const zone = h > w ? safeZoneArea(style.safe_zone) : null
  if (typeof style.y === 'number') {
    const above = 0.5 - ((zone?.top ?? 0) + SAFE_CLEARANCE) / 1920 - 0.6 * em
    const below = 0.5 - ((zone?.bottom ?? 0) + SAFE_CLEARANCE) / 1920 - 0.6 * em
    offset = clamp(style.y - 0.5, -above, below)
  } else if (zone && position !== 'middle') {
    const edge = position === 'bottom' ? zone.bottom : zone.top
    const inside = 0.5 - (edge + SAFE_CLEARANCE) / 1920 - 0.6 * em
    offset = position === 'bottom' ? Math.min(offset, inside) : Math.max(offset, -inside)
  }
  if (typeof style.x === 'number') {
    const spread = look ? inkSpread(look, em, h) : (INK_SPREAD * w) / 1080
    const left = (SIDE_MARGIN * w) / 1080 + spread
    const right = w - (Math.max(SIDE_MARGIN, zone?.right ?? 0) * w) / 1080 - spread
    const least = (MIN_WIDTH * w) / 2
    const centre = Math.round(right - left < 2 * least ? (left + right) / 2 : clamp(style.x * w, left + least, right - least))
    const half = Math.floor(Math.min(centre - left, right - centre))
    column = centre / w - 0.5
    side = w / 2 - half
  } else if (zone) {
    side = Math.round(((Math.max(SIDE_MARGIN, zone.right) + INK_SPREAD) * w) / 1080)
  }
  return { em, offset, column, side }
}

/** A preset drawn in CSS at `emPx` pixels to the em, `h` the frame's height
 *  in pixels: the editor's font, weight, colour, outline (only its outer
 *  half shows), shadow (0.05 em across, 0.07 em down, black at 150/255),
 *  plate and letter spacing (concat-text/src/lib.rs). */
export function presetCss(look: PresetLook, emPx: number, h: number): CSSProperties {
  const stroke = (look.strokeWidth || 0) * h
  const css: CSSProperties = {
    fontFamily: `'${look.fontFamily}', sans-serif`,
    fontWeight: look.fontWeight || 400,
    fontStyle: look.italic ? 'italic' : 'normal',
    fontSize: `${emPx}px`,
    lineHeight: look.lineHeight || 1.2,
    color: look.color || '#ffffff',
    opacity: look.opacity ?? 1,
    letterSpacing: look.tracking ? `${look.tracking * h}px` : undefined,
    WebkitTextStroke: stroke > 0 ? `${stroke}px ${look.strokeColor || '#000000'}` : undefined,
    paintOrder: 'stroke fill',
    textShadow: look.shadow ? `${0.05 * emPx}px ${0.07 * emPx}px ${0.04 * emPx}px rgba(0,0,0,0.59)` : undefined
  }
  if (look.background) {
    css.background = look.background
    css.borderRadius = `${(look.backgroundRadius || 0) * h}px`
    css.padding = `${(look.backgroundPaddingY || 0) * h}px ${(look.backgroundPaddingX || 0) * h}px`
  }
  return css
}
