"""The caption controls offer exactly what the renderer burns: Clips Kitty
Editor's text presets at the Captions sheet's sizes and places.

ui/src/renderer/src/components/CaptionStyleControls.tsx lists the sizes and
safe zones by hand, and lib/captionLook.ts places the preview's caption with
video/caption_presets.py's numbers. The UI has no React test harness, so this
reads the TypeScript and compares it with the Python: a choice the UI offers
that the renderer does not know would burn as the default without a word, and
a preview place that differs would show the caption somewhere it will not be.

Also: every user-visible string in the controls goes through t(), the
renderer's CSP gained only font-src, and none of the old controls (fonts,
colour, all caps, highlight, animation) is left.
CI light: reads files and imports video.caption_presets (standard library only).
"""

import importlib.util
import json
import re
from pathlib import Path

from video import caption_presets as cp

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
CONTROLS = (UI / "components" / "CaptionStyleControls.tsx").read_text(encoding="utf-8")
LOOK = (UI / "lib" / "captionLook.ts").read_text(encoding="utf-8")


def _pairs(name: str) -> list[tuple[str, str]]:
    """The [value, label] pairs of a `const NAME: [...][] = [...]` list."""
    match = re.search(rf"const {name}: \[[^\]]+\]\[\] = \[(.*?)\n\]", CONTROLS, re.DOTALL)
    assert match, f"{name} not found in CaptionStyleControls.tsx"
    return re.findall(r"\['([^']+)', '([^']+)'\]", match.group(1))


def _numbers(name: str) -> dict[str, float]:
    block = re.search(rf"const {name} = \{{([^}}]*)\}}", LOOK).group(1)
    return {k: float(v) for k, v in re.findall(r"(\w+): (-?[\d.]+)", block)}


def test_size_and_safe_zone_choices_are_the_renderers():
    assert [v for v, _ in _pairs("SIZES")] == list(cp.SIZES)
    assert tuple(v for v, _ in _pairs("SAFE_ZONES")) == cp.SAFE_ZONE_CHOICES
    assert _pairs("SAFE_ZONES")[0] == ("off", "Off")


def test_default_style_is_the_renderers_default():
    block = re.search(r"export const DEFAULT_CAPTION_STYLE[^=]*= \{(.*?)\n\}", CONTROLS, re.DOTALL).group(1)
    ui = {}
    for key, raw in re.findall(r"^\s*(\w+): (.+?),?$", block, re.MULTILINE):
        raw = raw.rstrip(",")
        ui[key] = json.loads(raw.replace("'", '"')) if raw not in ("true", "false") else raw == "true"
    assert ui == cp.DEFAULT


def test_preview_sizes_and_places_are_the_renderers():
    assert _numbers("CAPTION_SIZES") == cp.SIZES
    assert _numbers("PLACES") == cp.PLACES
    for name in ("SAFE_CLEARANCE", "INK_SPREAD", "SIDE_MARGIN"):
        assert int(re.search(rf"const {name} = (\d+)", LOOK).group(1)) == getattr(cp, name), name
    assert "LAYOUTS.safe_zones[zone]" in LOOK
    layouts = json.loads((ROOT / "gaming" / "layouts.json").read_text(encoding="utf-8"))["safe_zones"]
    for name, zone in cp.SAFE_ZONES.items():
        assert {k: layouts[name][k] for k in zone} == zone


def test_the_old_controls_are_gone():
    # (OLD_KEYS names them only to map an old saved style to its preset.)
    for gone in ("t('Font')", "t('Animation')", "UPPERCASE", "Highlight each word", "Karaoke", "Text colour",
                 "style.font", "style.color", "style.uppercase", "style.highlight", "style.animation"):
        assert gone not in CONTROLS, gone
    # Add Videos builds each change from the render's style, so a change
    # must arrive whole there, not as one onChange per field.
    add = (UI / "components" / "queue" / "AddVideos.tsx").read_text(encoding="utf-8")
    assert "onPick={(style) => putSlotStyle(slot, style)}" in add


def _detector():
    spec = importlib.util.spec_from_file_location("ui_i18n_editor", ROOT / "tests" / "test_ui_i18n_editor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.untranslated


def test_every_string_in_the_controls_goes_through_t():
    assert _detector()(CONTROLS) == []


CSP_BEFORE = (
    "default-src 'self'; connect-src 'self' http://127.0.0.1:8765 ws://127.0.0.1:8765; "
    "media-src http://127.0.0.1:8765; img-src 'self' data: http://127.0.0.1:8765; "
    "style-src 'self' 'unsafe-inline'"
)


def test_csp_gains_only_font_src():
    html = (ROOT / "ui" / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
    assert csp == CSP_BEFORE + "; font-src 'self' http://127.0.0.1:8765"
    dev = (ROOT / "ui" / "vite.config.mts").read_text(encoding="utf-8")
    assert "font-src 'self' http://127.0.0.1:8765" in dev


def test_the_preview_drags_with_the_layout_editors_snapping():
    # A dragged caption's narrowest lines are the renderer's.
    assert float(re.search(r"const MIN_WIDTH = ([\d.]+)", LOOK).group(1)) == cp.MIN_WIDTH
    # Snapping and guides come from the Gaming layout editor's helper, Alt
    # places freely, and the chosen app's own buttons show over the frame.
    assert "dragLayer(" in CONTROLS and "e.altKey ? null : targets()" in CONTROLS
    assert "<PlatformOverlay platform={style.safe_zone} />" in CONTROLS
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "e.shiftKey"):
        assert key in CONTROLS, key
    assert "t('Reset position')" in CONTROLS and "aria-label={t('Captions." in CONTROLS
    assert "\u2014" not in CONTROLS                                          # no em dashes
