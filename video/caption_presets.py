"""Caption styles are the editor's text presets.

Auto clips burn their captions with Clips Kitty Editor's own looks: its text
presets (config/caption_presets.json) in its own fonts (assets/fonts/), both
copied from our Concat fork by scripts/sync_editor_presets.py. A caption
style says which preset, how big and where, as the editor's Captions sheet
does (concat/src/panes/captions.rs: sizes Small 0.04, Medium 0.05, Large
0.065 of the frame's height; places Bottom 0.35, Centre 0, Top -0.35 from
the frame's centre):

    {"preset": "concat.bold-caption", "size": "medium", "position": "bottom",
     "words_per_caption": 3, "second_speaker": false,
     "second_speaker_color": "#5CE1FF", "safe_zone": "tiktok"}

resolve() turns one into what the ASS file needs, so the burned caption is the
editor's: the same font file, the same em, outline, shadow, plate and place.
normalize() reads any saved style, an old one too (font, font_size, colour,
uppercase, highlight, animation: those are gone), as one of these.

Standard library only.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from video import alphabets
from video import fonts as bundled

PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "caption_presets.json"

SIZES = {"small": 0.04, "medium": 0.05, "large": 0.065}           # captions.rs CAPTION_SIZES
PLACES = {"bottom": 0.35, "middle": 0.0, "top": -0.35}            # captions.rs CAPTION_OFFSETS
DEFAULT_PRESET = "concat.bold-caption"
DEFAULT = {
    "preset": DEFAULT_PRESET,
    "size": "medium",
    "position": "bottom",
    "words_per_caption": 3,
    "second_speaker": False,
    "second_speaker_color": "#5CE1FF",
    # The editor's Bottom place (0.35) lands in the apps' own bottom bar;
    # TikTok's zone puts the block's foot at y=1480 of 1920, where captions
    # always ended (MarginV 440), clear of the buttons column too.
    "safe_zone": "tiktok",
}

# The area each app covers with its own buttons and text, in px on a
# 1080x1920 Short (video/captions.py SAFE_ZONES is this; gaming/layouts.json
# has the same values, a test keeps them equal).
SAFE_ZONES = {
    "tiktok": {"top": 180, "bottom": 420, "right": 190},
    "reels": {"top": 245, "bottom": 400, "right": 205},
    "shorts": {"top": 250, "bottom": 350, "right": 190},
}
SAFE_ZONE_CHOICES = ("off", *SAFE_ZONES)
SAFE_CLEARANCE = 20     # px@1920 between a caption and the app's own text
INK_SPREAD = 9          # px@1080 the outline and shadow reach past the letters, at the sides
SIDE_MARGIN = 60        # px each side, as captions always had

# The shadow the editor draws under a title (concat-text/src/lib.rs): black
# at alpha 150, offset 0.05 em across and 0.07 em down.
SHADOW_X, SHADOW_Y = 0.05, 0.07
SHADOW_ALPHA = 150

# Languages the editor has a font for, by the editor's own language presets.
LANGUAGE_FONTS = {
    **dict.fromkeys(("hi", "mr", "ne", "sa"), "Noto Sans Devanagari"),
    "bn": "Noto Sans Bengali",
    "th": "Noto Sans Thai",
    **dict.fromkeys(("ar", "fa", "ur"), "Noto Sans Arabic UI"),
    "he": "Secular One",
    "ja": "Kosugi Maru",
    **dict.fromkeys(("ko", "zh", "yue"), "Noto Sans CJK SC"),
    "vi": "Montserrat",
    "el": "Inter",
}
# A writing system's font when the preset's has not every letter of it.
SCRIPT_FONTS = {"cyrillic": "Montserrat", "cyrillic-ext": "Montserrat", "greek": "Inter",
                "vietnamese": "Montserrat", "thai": "Noto Sans Thai", "latin": "Montserrat",
                "latin-ext": "Montserrat"}
# For a language the editor has no font for (Tamil, Telugu...): Windows' own.
WINDOWS_FALLBACK = "Nirmala UI"

# Saved styles from before: the old font decides the preset, the old size
# (px at 1920 tall, in that font) the nearest of the three sizes.
_OLD_FONT_PRESETS = {
    "bebas neue": "concat.poster",
    "anton": "concat.impact", "impact": "concat.impact",
    "bangers": "concat.comic", "comic sans ms": "concat.comic",
    "georgia": "concat.editorial",
}
_OLD_PRESET_FONTS = {"classic": "Arial", "bold_pop": "Poppins ExtraBold", "clean_fade": "Barlow ExtraBold",
                     "karaoke": "Archivo Black", "bounce": "Titan One", "slide_up": "Bebas Neue"}
_OLD_SCALES = {"anton": 1.7334, "bangers": 1.757, "bebas neue": 1.3, "archivo black": 1.347}
_ARIAL_SCALE = 1.117


@lru_cache(maxsize=1)
def _load() -> tuple[dict, ...]:
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(p for p in data.get("presets") or () if isinstance(p, dict) and isinstance(p.get("id"), str)
                 and isinstance(p.get("style"), dict))


def presets() -> list[dict]:
    """Every preset, in the editor's order."""
    return list(_load())


def preset(preset_id: str | None) -> dict | None:
    return next((p for p in _load() if p["id"] == preset_id), None)


def ids() -> list[str]:
    return [p["id"] for p in _load()]


def _hex6(value) -> str | None:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text):
        return "#" + text.upper()
    return None


def _old_size(style: dict) -> str:
    try:
        px = float(style.get("font_size"))
    except (TypeError, ValueError):
        return DEFAULT["size"]
    em = px / _OLD_SCALES.get(str(style.get("font") or "").strip().casefold(), _ARIAL_SCALE) / 1920
    return "small" if em < 0.045 else "medium" if em < 0.0575 else "large"


def normalize(style) -> dict:
    """Any saved caption style as the one shape: a preset and the editor
    sheet's size and place, words per caption, the second speaker's colour
    and the safe zone. A style from before (no "preset") is mapped by its
    font and size; its colour, case, highlight and animation are dropped.
    An unknown preset is the default one."""
    if not isinstance(style, dict):
        return dict(DEFAULT)
    out = dict(DEFAULT)
    chosen = style.get("preset")
    # A style from before, laid over today's defaults by a screen that
    # remembered it (its old font is still there): the old font and size win
    # over the defaults it was merged with.
    old = "font" in style or "font_size" in style
    if isinstance(chosen, str) and preset(chosen) is not None and not (old and chosen == DEFAULT_PRESET):
        out["preset"] = chosen
    elif chosen is None or chosen in _OLD_PRESET_FONTS or old:
        font = str(style.get("font") or _OLD_PRESET_FONTS.get(chosen) or "").strip().casefold()
        out["preset"] = _OLD_FONT_PRESETS.get(font, DEFAULT_PRESET)
    if style.get("size") in SIZES and not (old and style["size"] == DEFAULT["size"]):
        out["size"] = style["size"]
    elif old and "font_size" in style:
        out["size"] = _old_size(style)
    if style.get("position") in PLACES:
        out["position"] = style["position"]
    try:
        out["words_per_caption"] = max(1, min(10, int(style.get("words_per_caption", DEFAULT["words_per_caption"]))))
    except (TypeError, ValueError):
        pass
    if isinstance(style.get("second_speaker"), bool):
        out["second_speaker"] = style["second_speaker"]
    out["second_speaker_color"] = _hex6(style.get("second_speaker_color")) or DEFAULT["second_speaker_color"]
    if style.get("safe_zone") in SAFE_ZONE_CHOICES and not (old and style["safe_zone"] == "off"):
        out["safe_zone"] = style["safe_zone"]   # an old style with no zone ended at 1480 too: TikTok's
    return out


def font_for_language(family: str, language: str) -> str:
    """The family captions in `language` are set in: `family` when its file
    has every letter the language is written with, else the font the
    editor's own preset for that language uses. A language the editor has
    no font for (Tamil, Telugu, Gujarati...) takes Windows' Nirmala UI."""
    lang = alphabets.base_language(language)
    letters = alphabets.letters_for(lang)
    if letters is None:
        target = LANGUAGE_FONTS.get(lang)
        if target is None:
            return WINDOWS_FALLBACK
        return family if str(family).casefold() == target.casefold() else target
    file = bundled.face(family)
    if file is not None:
        has = bundled.covers(file["family"], letters)
        if has is None:     # the file cannot be read: go by the manifest
            has = alphabets.script_of(lang) in (file.get("scripts") or ())
        if has:
            return family
    return LANGUAGE_FONTS.get(lang) or SCRIPT_FONTS.get(alphabets.script_of(lang), "Montserrat")


def _offset(style: dict, em: float, canvas: tuple[int, int]) -> float:
    """Where the caption block's centre sits, as a share of the frame's
    height from its centre: the sheet's place, moved clear of an app's
    buttons and text when a safe zone is on (a portrait clip only). A
    one-line block reaches 0.6 em above and below its centre."""
    position = style.get("position") if style.get("position") in PLACES else "bottom"
    offset = PLACES[position]
    zone = SAFE_ZONES.get(style.get("safe_zone")) if canvas[1] > canvas[0] else None
    if zone and position != "middle":
        edge = zone["bottom"] if position == "bottom" else zone["top"]
        inside = 0.5 - (edge + SAFE_CLEARANCE) / 1920 - 0.6 * em
        offset = min(offset, inside) if position == "bottom" else max(offset, -inside)
    return round(offset, 4)


def _spread(base: dict, em: float, h: int) -> float:
    """How far a preset's ink reaches past its letters at the sides, in px
    on a frame h tall: the plate's padding, or the outline and the shadow."""
    if base.get("background"):
        return float(base.get("backgroundPaddingY") or 0.0) * h
    stroke = float(base.get("strokeWidth") or 0.0) * h / 2
    return stroke + (SHADOW_X * em * h if base.get("shadow") else 0.0)


def side_margin(style: dict, canvas: tuple[int, int], spread: float | None = None) -> int:
    """MarginL and MarginR: clear of an app's column of buttons with a safe
    zone (portrait), with room for the ink past the letters (`spread` px, the
    margins bound the letters only), else the 60 px captions always had."""
    zone = SAFE_ZONES.get(style.get("safe_zone")) if canvas[1] > canvas[0] else None
    if zone:
        reach = INK_SPREAD * canvas[0] / 1080 if spread is None else spread
        return math.ceil(max(SIDE_MARGIN, zone["right"]) * canvas[0] / 1080 + reach) + 1
    return SIDE_MARGIN


def look(style, canvas: tuple[int, int] = (1080, 1920), language: str = "en") -> dict:
    """The caption's look in the editor's terms: the preset's resolved style
    (family through font_for_language) at the sheet's size and place.
    fontSize and offsetY are shares of the frame's height, maxWidth of its
    width (the wrap width libass gets from the margins)."""
    style = normalize(style)
    chosen = preset(style["preset"]) or preset(DEFAULT_PRESET) or {"style": {}}
    base = dict(chosen["style"])
    em = SIZES[style["size"]]
    family = font_for_language(str(base.get("fontFamily") or "Hanken Grotesk"), language)
    margin = side_margin(style, canvas, _spread(base, em, canvas[1]))
    return {
        **base,
        "fontFamily": family,
        "fontSize": em,
        "offsetY": _offset(style, em, canvas),
        "maxWidth": round((canvas[0] - 2 * margin) / canvas[0], 5),
        "maxHeight": 0.0,
    }


def _ass_colour(hex_rgb: str, alpha: int = 255) -> str:
    """#RRGGBB[AA] -> &HAABBGGRR, ASS's alpha counting up to transparent."""
    h = str(hex_rgb or "").lstrip("#")
    if len(h) == 8:
        alpha = int(h[6:8], 16)
        h = h[:6]
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{255 - max(0, min(255, alpha)):02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def resolve(style, canvas: tuple[int, int] = (1080, 1920), language: str = "en") -> dict:
    """What the ASS file needs for a caption style on a w x h frame: the
    style line's fields and where each event is placed (\\an5\\pos at the
    block's centre, as the editor places a title)."""
    w, h = canvas
    seen = look(style, canvas, language)
    em = seen["fontSize"] * h
    file = bundled.face(seen["fontFamily"], float(seen.get("fontWeight") or 400), bool(seen.get("italic")))
    if file is not None:
        fontname = file["family"]
        scale = float(file.get("ass_scale") or 1.0)
        bold = "bold" in str(file.get("subfamily") or "").casefold()   # a RIBBI Bold cut only
        italic = bool(seen.get("italic"))
    else:   # a Windows font (WINDOWS_FALLBACK), or the app's fonts missing
        fontname, scale, bold, italic = seen["fontFamily"], 1.117, True, bool(seen.get("italic"))
    plate = str(seen.get("background") or "")
    stroke = float(seen.get("strokeWidth") or 0.0)
    out = {
        "fontname": fontname,
        "family": seen["fontFamily"],
        "fontsize": max(1, round(em * scale)),
        "em": em,
        "bold": bold,
        "italic": italic,
        "primary": _ass_colour(seen.get("color") or "#ffffff", round(255 * float(seen.get("opacity", 1.0)))),
        "spacing": round(float(seen.get("tracking") or 0.0) * h, 2),
        "margin": round((w - seen["maxWidth"] * w) / 2),
        "x": round(w / 2),
        "y": round(h * (0.5 + seen["offsetY"])),
        "shadow": None,
        "back": "&H00000000",
    }
    if plate:
        out.update(border_style=3, outline=round(float(seen.get("backgroundPaddingY") or 0.0) * h, 2),
                   outline_colour=_ass_colour(plate))
    else:
        out.update(border_style=1, outline=round(stroke * h / 2, 2),
                   outline_colour=_ass_colour(seen.get("strokeColor") or "#000000"))
    if seen.get("shadow"):
        out["shadow"] = (round(SHADOW_X * em, 2), round(SHADOW_Y * em, 2))
        out["back"] = _ass_colour("#000000", SHADOW_ALPHA)
    return out


def reload() -> None:
    _load.cache_clear()
