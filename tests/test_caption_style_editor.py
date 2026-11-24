"""Auto clips' captions are the editor's (video/caption_presets.py, video/captions.py).

- resolve(): a preset at a size and place becomes the ASS style the editor's
  look needs: the font file's own family name and weight, the em times the
  file's libass scale, half the stroke as outline, a plate as BorderStyle 3,
  the shadow on each event, the block's centre where the editor puts it.
- normalize(): an old saved style becomes the nearest preset and size; its
  colour, case, highlight and animation are gone.
- Languages burn in the editor's own font for them.
- kitty_editor.build.caption_look gives the editor the same look.

Standard library only: these run in CI's light install.
"""

import re

import pytest

from core.models import ClipCandidate, Segment
from kitty_editor.build import caption_look
from video import caption_presets as cp
from video.captions import build_captions


def _ass(tmp_path, style=None, canvas=(1080, 1920), language="en", text="one two three") -> str:
    segments = [Segment(start=0.0, end=2.0, text=text)]
    path = build_captions(segments, ClipCandidate(start=0.0, end=2.0, score=0), tmp_path / "c.ass",
                          style=style, canvas=canvas, language=language)
    return path.read_text(encoding="utf-8")


def _style_line(text: str) -> list[str]:
    return next(line for line in text.splitlines() if line.startswith("Style: Default,"))[7:].split(",")


def test_bold_caption_medium_bottom_is_the_editors_caption(tmp_path):
    text = _ass(tmp_path)
    s = _style_line(text)
    assert s[1] == "Montserrat Black" and s[7] == "0"                # its own Black file, no faux bold
    assert s[2] == str(round(0.05 * 1920 * 1.562))                     # em x the file's libass scale
    assert float(s[16]) == pytest.approx(0.012 * 1920 / 2)             # the outer half of the stroke
    assert s[15] == "1" and s[17] == "0" and s[18] == "5"
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    # TikTok's safe zone by default: the block ends at y=1480, where captions always did.
    assert events and all("\\an5\\pos(540,1422)" in e for e in events)
    assert all("\\xshad4.8\\yshad6.72" in e for e in events)          # the editor's shadow, 0.05 / 0.07 em
    assert "&H69000000" in text                                        # black at alpha 150


def test_a_plate_preset_is_a_box_and_a_clean_one_has_no_shadow(tmp_path):
    s = _style_line(_ass(tmp_path, {"preset": "concat.block"}))
    assert s[15] == "3" and s[5] == "&H004AE1FF"                       # the yellow plate
    clean = _ass(tmp_path, {"preset": "concat.clean"})
    assert "\\xshad" not in clean and _style_line(clean)[1] == "Inter SemiBold"


def test_a_landscape_frame_scales_by_its_height(tmp_path):
    text = _ass(tmp_path, canvas=(1920, 1080))
    assert _style_line(text)[2] == str(round(0.05 * 1080 * 1.562))
    assert "\\pos(960,918)" in text


def test_sizes_and_places_are_the_captions_sheets():
    for size, em in (("small", 0.04), ("medium", 0.05), ("large", 0.065)):
        assert cp.resolve({"size": size})["em"] == pytest.approx(em * 1920)
    assert cp.resolve({"position": "top"})["y"] == round(1920 * 0.15)
    assert cp.resolve({"position": "middle"})["y"] == 960


def test_a_safe_zone_keeps_a_bottom_caption_clear_of_each_app(tmp_path):
    for zone, area in cp.SAFE_ZONES.items():
        r = cp.resolve({"safe_zone": zone, "size": "large"})
        bottom_of_block = r["y"] + 0.6 * r["em"]
        assert bottom_of_block <= 1920 - area["bottom"] - cp.SAFE_CLEARANCE + 0.5, zone
        assert r["margin"] >= area["right"]
    assert cp.resolve({"safe_zone": "tiktok"}, (1920, 1080))["y"] == round(1080 * 0.85)     # off for landscape


@pytest.mark.parametrize(("style", "preset", "size"), [
    ({"font": "Arial", "font_size": 84}, "concat.bold-caption", "small"),
    ({"preset": "classic"}, "concat.bold-caption", "medium"),
    ({"preset": "bold_pop"}, "concat.bold-caption", "medium"),
    ({"preset": "clean_fade"}, "concat.bold-caption", "medium"),
    ({"preset": "karaoke"}, "concat.bold-caption", "medium"),
    ({"preset": "bounce"}, "concat.bold-caption", "medium"),
    ({"preset": "slide_up"}, "concat.poster", "medium"),
    ({"font": "Bebas Neue", "font_size": 120}, "concat.poster", "medium"),
    ({"font": "Anton", "font_size": 140}, "concat.impact", "small"),
    ({"font": "Georgia", "font_size": 130}, "concat.editorial", "large"),
    # the Dashboard's remembered old style, laid over today's defaults
    ({**cp.DEFAULT, "font": "Bangers", "font_size": 84}, "concat.comic", "small"),
    ({"preset": "nope"}, "concat.bold-caption", "medium"),
])
def test_an_old_style_becomes_the_nearest_preset(style, preset, size):
    out = cp.normalize({**style, "uppercase": True, "highlight": True, "animation": "karaoke", "color": "#FF0000",
                        "position": "top", "safe_zone": "reels", "second_speaker": True})
    assert out["preset"] == preset and out["size"] == size
    assert set(out) == set(cp.DEFAULT)
    assert out["position"] == "top" and out["safe_zone"] == "reels" and out["second_speaker"] is True


def test_the_second_speaker_keeps_their_colour(tmp_path):
    lines = [{"start": 0.0, "end": 1.0, "text": "one"}, {"start": 1.0, "end": 2.0, "text": "two", "speaker": 1}]
    segments = [Segment(start=0.0, end=2.0, text="one two")]
    path = build_captions(segments, ClipCandidate(start=0.0, end=2.0, score=0), tmp_path / "c.ass",
                          style={"second_speaker": True, "second_speaker_color": "#112233"}, lines=lines)
    events = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
    assert "\\1c" not in events[0] and "\\1c&H332211&" in events[1]


@pytest.mark.parametrize(("language", "font"), [
    ("hi", "Noto Sans Devanagari"), ("ja", "Kosugi Maru"), ("ar", "Noto Sans Arabic UI"), ("ru", "Montserrat Black"),
    ("ta", "Nirmala UI"),
])
def test_languages_burn_in_the_editors_fonts(tmp_path, language, font):
    assert _style_line(_ass(tmp_path, language=language))[1] == font


def test_auto_clips_and_old_styles_end_above_the_apps_bottom_bar():
    for style in ({}, {"font": "Arial", "font_size": 84, "safe_zone": "off"}):
        r = cp.resolve(style)
        assert cp.normalize(style)["safe_zone"] == "tiktok"
        assert r["y"] + 0.6 * r["em"] <= 1480 + 1 and r["margin"] >= 190


def test_the_editor_gets_the_burned_look():
    for style in ({}, {"preset": "concat.block", "size": "large", "safe_zone": "tiktok"},
                  {"preset": "concat.editorial", "position": "top"}):
        look, row, notes = caption_look(style, (1080, 1920))
        burned = cp.resolve(style)
        preset = cp.preset(cp.normalize(style)["preset"])["style"]
        assert notes == []
        assert look["fontFamily"] == burned["family"] == preset["fontFamily"]
        for key in ("fontWeight", "italic", "color", "strokeWidth", "strokeColor", "shadow", "background"):
            assert look[key] == preset[key], key
        assert look["fontSize"] * 1920 == pytest.approx(burned["em"])
        assert round(1920 * (0.5 + row)) == burned["y"]
        assert look["maxWidth"] == pytest.approx((1080 - 2 * burned["margin"]) / 1080, abs=1e-4)


def test_no_karaoke_highlight_or_animation_is_left():
    source = (cp.PRESETS_PATH.parent.parent / "video" / "captions.py").read_text(encoding="utf-8")
    for gone in ("_karaoke", "_highlighted", "_animated", "karaoke_untimed", "ANIMATIONS", "WINDOWS_FONTS"):
        assert not re.search(rf"\b{gone}\b", source), gone
