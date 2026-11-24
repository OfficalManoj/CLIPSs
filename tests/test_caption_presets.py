"""Caption presets (config/caption_presets.json) are Clips Kitty Editor's
text presets, and the caption style validators take them (analysis/clip_edit.py
for the AI edit assistant, server/mcp.py for MCP clients).

- The file holds the editor's 45 presets in its order, each with its whole
  resolved look; every family it names is one of the app's fonts.
- The validators accept a preset id, a size, a place and a safe zone, and
  refuse (MCP, with the list) or drop (the AI edit) anything else.

Standard library only: these run in CI's light install.
"""

import json
from pathlib import Path

import pytest

from analysis.clip_edit import _clean_caption_style
from server import mcp
from video import caption_presets, fonts

ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads((ROOT / "config" / "caption_presets.json").read_text(encoding="utf-8"))
PRESETS = DOC["presets"]
LOOK_KEYS = {"fontFamily", "fontSize", "fontWeight", "italic", "color", "opacity", "strokeWidth", "strokeColor",
             "shadow", "background", "backgroundRadius", "backgroundPaddingX", "backgroundPaddingY", "lineHeight",
             "tracking"}


def test_the_editors_45_presets_in_its_order():
    assert len(PRESETS) == 45
    assert [p["id"] for p in PRESETS[:3]] == ["default", "concat.poster", "concat.bold-caption"]
    assert PRESETS[-1]["id"] == "concat.greek"
    assert len({p["id"] for p in PRESETS}) == 45
    assert DOC["source"] == "94aa48ffac8a983e1fde6f305f3788cb424f7850"
    assert caption_presets.ids() == [p["id"] for p in PRESETS]


@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
def test_every_preset_is_whole_and_in_the_apps_fonts(preset):
    assert preset["name"] and preset["sample"] and preset["language"]
    assert LOOK_KEYS <= set(preset["style"])
    assert fonts.face(preset["style"]["fontFamily"], preset["style"]["fontWeight"]) is not None
    # Both validators accept the id as it is.
    assert _clean_caption_style({"preset": preset["id"]}) == {"preset": preset["id"]}
    assert mcp._caption_style({"preset": preset["id"]}) == {"preset": preset["id"]}


# ---- the validators -----------------------------------------------------------------------------


def test_the_ai_edit_takes_a_preset_size_place_and_safe_zone():
    assert _clean_caption_style({"preset": "CONCAT.IMPACT", "size": "Large", "position": "top",
                                 "safe_zone": "tiktok", "words_per_caption": 9}) == {
        "preset": "concat.impact", "size": "large", "position": "top", "safe_zone": "tiktok",
        "words_per_caption": 6}


def test_the_ai_edit_drops_what_the_renderer_does_not_know():
    assert _clean_caption_style({"preset": "wobbly", "font": "Arial", "animation": "pop", "size": "huge",
                                 "highlight": True, "color": "#FFFFFF"}) == {}


def test_the_ai_edit_prompt_lists_every_preset_and_no_old_choice():
    prompt = (ROOT / "config" / "prompts" / "edit_clip.txt").read_text(encoding="utf-8")
    for p in PRESETS:
        assert f'"{p["id"]}"' in prompt
    for gone in ('"font":', '"animation":', '"uppercase":', '"karaoke"', '"font_size":'):
        assert gone not in prompt


def test_mcp_takes_a_preset_by_id_or_name():
    assert mcp._caption_style({"preset": "Bold caption"}) == {"preset": "concat.bold-caption"}
    assert mcp._caption_style({"size": "Small", "safe_zone": "youtube", "position": "centre"}) == {
        "size": "small", "safe_zone": "shorts", "position": "middle"}


def test_mcp_refuses_what_the_renderer_does_not_know_with_the_choices():
    with pytest.raises(ValueError, match="No caption look called 'Papyrus'"):
        mcp._caption_style({"preset": "Papyrus"})
    with pytest.raises(ValueError, match="Caption size 'huge' is not one of: small, medium, large"):
        mcp._caption_style({"size": "huge"})
    with pytest.raises(ValueError, match="Caption safe zone 'snapchat' is not one of: off, tiktok, reels, shorts"):
        mcp._caption_style({"safe_zone": "snapchat"})


def test_mcp_tells_the_model_about_the_presets():
    schema = next(t for t in mcp.TOOLS if t["name"] == "queue_video")["inputSchema"]["properties"]
    style = schema["caption_style"]["properties"]
    assert style["preset"]["enum"] == [p["id"] for p in PRESETS]
    assert style["size"]["enum"] == ["small", "medium", "large"]
    assert style["safe_zone"]["enum"] == list(caption_presets.SAFE_ZONE_CHOICES)
    assert not {"font", "font_size", "color", "uppercase", "highlight", "animation"} & set(style)


def test_every_validator_takes_a_dragged_spot_and_refuses_one_off_the_frame():
    from video_editor.project.env import AppEnv

    assert _clean_caption_style({"x": 0.25, "y": 0.8, "position": "top"}) == {"position": "top", "x": 0.25, "y": 0.8}
    assert _clean_caption_style({"x": "left", "y": True}) == {}
    assert mcp._caption_style({"x": 0.25, "y": 1}) == {"x": 0.25, "y": 1.0}
    for bad in ({"x": 1.5}, {"y": -0.1}, {"x": "left"}, {"y": True}):
        with pytest.raises(ValueError):
            mcp._caption_style(bad)
    schema = json.dumps([t["inputSchema"] for t in mcp.TOOLS])
    assert '"x": {"type": "number", "minimum": 0, "maximum": 1' in schema
    env = AppEnv(db=None, data_dir=ROOT)
    assert env.caption_style({"preset": "concat.impact", "x": 0.3, "y": 0.123456}) == {
        "preset": "concat.impact", "x": 0.3, "y": 0.1235}
    assert env.caption_style({"x": None}) == {}
    for bad in ({"x": 2}, {"y": "low"}, {"y": False}):
        with pytest.raises(ValueError):
            env.caption_style(bad)
    prompt = (ROOT / "config" / "prompts" / "edit_clip.txt").read_text(encoding="utf-8")
    assert '"x", "y"' in prompt
