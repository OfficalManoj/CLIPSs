"""Editor suite step KG: a gaming clip's layout, Letterbox and Center as the
editor's own picture lanes (kitty_editor/sections.py).

A1: every lane, rebuilt by the editor's own placement rule (crop, then
contain-fit, then scale, stretch and offset: concat stage.rs footprint),
lands on the auto render's Plan (gaming.layout.plan) within a pixel, for
all 11 layouts. A2: placing the lanes on a saved project keeps the cuts,
speed, captions, music and language tabs, and puts the section lanes
directly above the main lane."""

import copy
import json

import pytest

from gaming import layout
from kitty_editor import languages, sections

CAM = [0.02, 0.05, 0.25, 0.30]
CAM2 = [0.73, 0.05, 0.25, 0.30]
UI = [0.40, 0.0, 0.20, 0.06]        # under the editor's 10% crop floor on its height


def _settings(preset: str, **more) -> dict:
    return {"cam": CAM, "cam2": CAM2, "ui_box": UI, "preset": preset, "panels": [], **more}


def _heads(w: int, h: int) -> dict:
    return {"cam": (w * 0.14, h * 0.06, h * 0.2), "cam2": (w * 0.86, h * 0.07, h * 0.22)}


def _target(e, role: str) -> tuple:
    """Where the auto render shows a lane's picture (compose.py)."""
    dx, dy, dw, dh = e.dest
    if role.endswith("_fill"):
        return e.dest
    if e.fit == "contain":
        sw, sh = e.src[2], e.src[3]
        k = min(dw / sw, dh / sh)
        rw, rh = sw * k, sh * k
        ry = {"top": dy, "bottom": dy + dh - rh}.get(e.anchor, dy + (dh - rh) / 2)
        return dx + (dw - rw) / 2, ry, rw, rh
    if e.shift:
        return dx, dy + e.shift, dw, dh - e.shift
    return e.dest


CASES = [(p, None) for p in layout.PRESETS] + [("split", "fit"), ("half", "fit"), ("game_ui", "fit")]


@pytest.mark.parametrize("size", [(1920, 1080), (2560, 1440)])
@pytest.mark.parametrize("preset, fit", CASES)
def test_every_lane_lands_where_the_auto_render_puts_it(preset, fit, size):
    w, h = size
    settings = _settings(preset, **({"game_fit": fit} if fit else {}))
    plan = layout.plan(w, h, settings, _heads(w, h))
    lanes = sections.gaming_specs(settings, w, h, _heads(w, h))
    assert lanes[0]["role"] == "game" and lanes[0]["main"] and not any(spec["main"] for spec in lanes[1:])
    roles = [spec["role"] for spec in lanes]
    assert sorted(r for r in roles if not r.endswith("_fill")) == sorted(
        e.role for e in plan.elements if e.role != "bg")
    for spec in lanes:
        e = plan.element(spec["role"].removesuffix("_fill"))
        want = _target(e, spec["role"])
        got = sections.shown_box(spec, w, h)
        assert all(abs(a - b) <= 1.0 for a, b in zip(got, want)), (spec["role"], got, want)
        # The crop is the source box itself, unless it is under the floor.
        crop = spec["crop"] or {"left": 0, "top": 0, "right": 0, "bottom": 0}
        if not spec["masks"] or spec["masks"][0]["source"]["shape"] == "circle":
            src = e.src
            assert abs(crop["left"] - src[0] / w) < 0.001 and abs(crop["top"] - src[1] / h) < 0.001
            assert abs(1 - crop["right"] - (src[0] + src[2]) / w) < 0.001
        assert crop["left"] + crop["right"] <= 0.9 and crop["top"] + crop["bottom"] <= 0.9
        assert 0.05 <= spec["scale"] <= 8 and abs(spec["offsetX"]) <= 1 and abs(spec["offsetY"]) <= 1
        assert spec["background"] is None or spec["role"] == "game"
    # The blur backdrop only where the game is shown whole (Blurred, a stack set to fit).
    whole = plan.element("game").fit == "contain"
    assert (lanes[0]["background"] == {"kind": "blur", "amount": 1.0}) == whole


def test_circle_facecams_are_circle_masks_and_square_boxes():
    for preset, roles in (("circle_cam", ["cam"]), ("dual_cam", ["cam", "cam2"])):
        lanes = {s["role"]: s for s in sections.gaming_specs(_settings(preset), 1920, 1080, _heads(1920, 1080))}
        for role in roles:
            mask = lanes[role]["masks"][0]
            assert mask["source"]["shape"] == "circle" and mask["feather"] == 0 and mask["mode"] == "add"
            assert mask["source"]["width"] == pytest.approx(1.0) and mask["source"]["height"] == pytest.approx(1.0)
            assert mask["x"] == pytest.approx(0) and mask["y"] == pytest.approx(0)


def test_a_webcam_moved_down_for_the_top_bar_has_a_blur_of_itself_under_it():
    settings = _settings("split")
    heads = {"cam": (1920 * 0.14, 1080 * 0.052, 1080 * 0.2)}         # high in the webcam
    plan = layout.plan(1920, 1080, settings, heads)
    assert plan.element("cam").shift > 0
    lanes = sections.gaming_specs(settings, 1920, 1080, heads)
    roles = [s["role"] for s in lanes]
    assert roles.index("cam_fill") == roles.index("cam") - 1       # under it
    fill = lanes[roles.index("cam_fill")]
    assert fill["videoEffects"] == [{"id": "concat.gaussian-blur", "params": {"radius": 50.0}, "enabled": True}]
    assert fill["name"] == "Webcam blur"


def test_a_box_under_the_crop_floor_gets_a_grown_crop_and_a_box_mask():
    lanes = {s["role"]: s for s in sections.gaming_specs(_settings("game_ui"), 1920, 1080)}
    ui = lanes["ui"]
    crop = ui["crop"]
    assert 1 - crop["top"] - crop["bottom"] == pytest.approx(sections.CROP_FLOOR, abs=1e-4)
    assert ui["masks"][0]["source"]["shape"] == "rectangle"


def test_letterbox_and_center_are_one_lane_from_the_source():
    letterbox = sections.framing_specs("letterbox", 1920, 1080)
    assert len(letterbox) == 1 and letterbox[0]["scale"] == 1.0 and letterbox[0]["background"]["kind"] == "blur"
    assert sections.shown_box(letterbox[0], 1920, 1080) == pytest.approx((0, 960 - 303.75, 1080, 607.5))
    center = sections.framing_specs("center", 1920, 1080)
    assert center[0]["background"] is None
    x, y, w, h = sections.shown_box(center[0], 1920, 1080)
    assert h == pytest.approx(1920) and x == pytest.approx(540 - w / 2) and w > 1080


def test_the_mode_and_the_layout_the_picker_shows():
    config = {"clips": {"vertical": True}}
    g = {"cam": [0.1, 0.1, 0.2, 0.2], "preset": "split", "by": "video", "layout": "split",
         "used_preset": "circle_cam", "used_cam": [0.11, 0.1, 0.2, 0.2]}
    shown = sections.layout_of({"gaming": g}, None, config)
    assert shown == {"kind": "gaming", "gaming": {"cam": [0.11, 0.1, 0.2, 0.2], "preset": "circle_cam"}}
    assert sections.mode({"gaming": g}, None, config) == "gaming"
    assert sections.mode({"gaming": g}, None, config, source_ok=False) == "base"
    assert sections.mode({"crop": "letterbox"}, None, config) == "letterbox"
    assert sections.mode({"crop": "center"}, None, config) == "center"
    assert sections.mode({}, None, config) == "base"
    assert sections.mode({"gaming": g}, {"layout": {"kind": "framing", "crop": "center"}}, config) == "center"
    assert sections.layout_of({}, {"layout": {"kind": "framing", "crop": "letterbox"}}, config)["crop"] == "letterbox"
    for opts, cfg in (({"profile": "longform"}, config), ({"vertical_live": True}, config),
                      ({"podcast": True}, config), ({"sport": "basketball"}, config),
                      ({}, {"clips": {"vertical": False}})):
        assert sections.layout_of(opts, None, cfg) is None, opts
        assert sections.mode(opts, None, cfg) == "base"


def test_the_layout_keys_are_the_gaming_renders():
    run = pytest.importorskip("gaming.run", exc_type=ImportError)
    assert sections.LAYOUT_KEYS == run.LAYOUT_KEYS


def test_a_layout_the_app_picked_opens_as_it_was_drawn():
    # No preset stored (the app chose Half itself): the render drew the game
    # whole with the webcam on top (gaming.layout.resolve), so the editor does too.
    auto = sections.gaming_of({"gaming": {"cam": [0.1, 0.1, 0.2, 0.2], "used_preset": "half"}})
    assert auto["preset"] == "half" and auto["game_fit"] == "fit" and auto["order"] == "cam_top"
    below = sections.gaming_of({"gaming": {"cam": [0.1, 0.1, 0.2, 0.2], "cam_position": "bottom",
                                           "game_fit": "fill", "used_preset": "half"}})
    assert below["order"] == "game_top" and below["game_fit"] == "fill"
    chosen = sections.gaming_of({"gaming": {"cam": [0.1, 0.1, 0.2, 0.2], "preset": "half", "used_preset": "half"}})
    assert "game_fit" not in chosen and "order" not in chosen
    layout = pytest.importorskip("gaming.layout", exc_type=ImportError)
    g = {"cam": [0.1, 0.1, 0.2, 0.2]}
    drawn = layout.resolve(g)
    assert {k: drawn[k] for k in ("preset", "game_fit", "order")} == {k: auto[k] for k in ("preset", "game_fit", "order")}


def test_heads_come_from_the_render_or_the_cache_else_they_are_looked_for():
    g = {"cam": [0.1, 0.1, 0.2, 0.2], "used_cam": [0.1, 0.1, 0.2, 0.2], "heads_norm": {"cam": [0.2, 0.15, 0.25]}}
    settings = sections.gaming_of({"gaming": g})
    assert sections.heads_from({"gaming": g}, settings, 1920, 1080) == {"cam": (384.0, 162.0, 270.0)}
    moved = {**settings, "cam": [0.5, 0.1, 0.2, 0.2]}
    assert sections.heads_from({"gaming": g}, moved, 1920, 1080) is None
    cached = {"cam": {"box": [0.5, 0.1, 0.2, 0.2], "head": [0.6, 0.12, 0.2]}}
    assert sections.heads_from({"gaming": g}, moved, 1920, 1080, cached)["cam"] == pytest.approx((1152, 129.6, 216))
    nobody = {"cam": {"box": [0.5, 0.1, 0.2, 0.2], "head": None}}
    assert sections.heads_from({"gaming": g}, moved, 1920, 1080, nobody) == {}


# ---- A2: the lanes on a saved project -------------------------------------------------------------

def _video(cid, track, start, dur, source_start, **more):
    return {"id": cid, "trackId": track, "mediaId": "m1", "kind": "video", "name": "source.mp4", "start": start,
            "duration": dur, "sourceStart": source_start, "speed": 1.25, "volume": 0.8, "scale": 1.0,
            "offsetX": 0.0, "offsetY": 0.0, "rotation": 0.0, "opacity": 1.0, "fadeIn": 0.2, "fadeOut": 0.0,
            "videoEffects": [], "filters": [], "preservePitch": True, **more}


def _text(cid, track, start, dur, words):
    return {"id": cid, "trackId": track, "mediaId": "", "kind": "text", "name": words, "start": start,
            "duration": dur, "text": {"content": words}}


def _project() -> dict:
    def tab(tid, prefix):
        clips = [_video(f"{prefix}2", f"{prefix}T4", 0.0, 2.4, 104.0), _video(f"{prefix}3", f"{prefix}T4", 2.4, 4.0, 110.0)]
        clips += [_text(f"{prefix}10", f"{prefix}t6", 0.0, 2.0, "hello"), _text(f"{prefix}11", f"{prefix}t6", 2.0, 2.0, "there")]
        clips.append({"id": f"{prefix}20", "trackId": f"{prefix}t9", "mediaId": "m3", "kind": "audio", "start": 0.0,
                      "duration": 6.4, "volume": 0.2})
        tracks = [{"id": t, "muted": False, "visible": True}
                  for t in (f"{prefix}t9", f"{prefix}T1", f"{prefix}T4", f"{prefix}t6")]
        return {"id": tid, "name": tid, "tracks": tracks, "clips": clips,
                "video": {"width": 1080, "height": 1920, "rateNum": 60, "rateDen": 1}}

    tabs = [tab("TL1", "a"), tab("tl30", "b")]
    return {"name": "Clip", "media": [{"id": "m1", "path": "D:/v/source.mp4"}, {"id": "m3", "path": "D:/p/music.mp3"}],
            "timelines": tabs, "activeTimelineId": "TL1", "tracks": tabs[0]["tracks"], "clips": tabs[0]["clips"]}


def _mint_for(project):
    counter = [languages._highest(project)]

    def mint(prefix):
        counter[0] += 1
        return f"{prefix}{counter[0]}"
    return mint, counter[0]


def test_placing_keeps_the_cuts_and_puts_the_sections_right_above_the_main_lane():
    project = _project()
    untouched = {t["id"]: [copy.deepcopy(c) for c in t["clips"] if c["kind"] != "video"] for t in project["timelines"]}
    mint, highest = _mint_for(project)
    lanes = sections.gaming_specs(_settings("game_ui"), 1920, 1080, _heads(1920, 1080))
    sections.place(project, lanes, ["m1"], mint)
    for timeline in project["timelines"]:
        ids = [t["id"] for t in timeline["tracks"]]
        main = next(t for t in timeline["tracks"] if t.get("kittyRole") == "game")
        at = ids.index(main["id"])
        above = timeline["tracks"][at + 1: at + len(lanes)]
        assert [t["kittyRole"] for t in above] == [s["role"] for s in lanes[1:]]     # bottom first, as stored
        assert ids[-1].endswith("t6")                                               # captions stay on top
        by_lane = {}
        for c in timeline["clips"]:
            if c["kind"] == "video":
                by_lane.setdefault(c["trackId"], []).append(c)
        timing = {lane: [(c["start"], c["duration"], c["sourceStart"], c["speed"]) for c in sorted(cs, key=lambda c: c["start"])]
                  for lane, cs in by_lane.items()}
        assert len(timing) == len(lanes) and len({tuple(v) for v in timing.values()}) == 1
        for c in timeline["clips"]:
            if c["kind"] != "video":
                continue
            if c["trackId"] == main["id"]:
                assert not c.get("muted") and c["kittyRole"] == "game" and c["volume"] == 0.8
            else:
                assert c["muted"] is True and "background" not in c and int(c["id"][1:]) > highest
                assert c["name"] in ("Webcam", "Game UI", "Webcam blur")
        assert [c for c in timeline["clips"] if c["kind"] != "video"] == untouched[timeline["id"]]
    assert project["tracks"] is project["timelines"][0]["tracks"]


def test_placing_again_takes_the_earlier_sections_off_first():
    project = _project()
    mint, _ = _mint_for(project)
    sections.place(project, sections.gaming_specs(_settings("duo_split"), 1920, 1080), ["m1"], mint)
    sections.place(project, sections.framing_specs("letterbox", 1920, 1080), ["m1"], mint)
    timeline = project["timelines"][0]
    assert len(timeline["tracks"]) == 4
    main = [c for c in timeline["clips"] if c["kind"] == "video"]
    assert [c["id"] for c in main] == ["a2", "a3"]
    assert all(c["kittyRole"] == "picture" and "crop" not in c and c["background"]["kind"] == "blur" for c in main)
    json.dumps(project)


# ---- KG-link: a column's pieces share a time link, the captions a look link ---------------------

def _columns(timeline: dict) -> dict:
    """Every video piece by its time link."""
    out: dict = {}
    for c in timeline["clips"]:
        if c["kind"] == "video":
            out.setdefault(c.get(sections.TIME_LINK), []).append(c)
    return out


def _record(*lines) -> dict:
    return {"follow": {"lines": [{"start": a, "end": b, "text": t} for a, b, t in lines]}}


def test_each_columns_pieces_share_one_time_link_the_main_pieces_id():
    project = _project()
    mint, _ = _mint_for(project)
    lanes = sections.gaming_specs(_settings("game_ui"), 1920, 1080, _heads(1920, 1080))
    sections.place(project, lanes, ["m1"], mint)
    for timeline in project["timelines"]:
        main = next(t["id"] for t in timeline["tracks"] if t.get("kittyRole") == "game")
        columns = _columns(timeline)
        mains = [c for c in timeline["clips"] if c["kind"] == "video" and c["trackId"] == main]
        assert sorted(columns) == sorted(c["id"] for c in mains)
        for piece in mains:
            column = columns[piece["id"]]
            assert len(column) == len(lanes) and len({c["trackId"] for c in column}) == len(lanes)
            assert {(c["start"], c["duration"]) for c in column} == {(piece["start"], piece["duration"])}
        assert all(sections.LOOK_LINK not in c for c in timeline["clips"])     # no record: no look link
    json.dumps(project)


def test_a_layout_change_keeps_the_links_and_an_existing_link_is_kept():
    project = _project()
    mint, _ = _mint_for(project)
    sections.place(project, sections.gaming_specs(_settings("duo_split"), 1920, 1080), ["m1"], mint)
    first = {c["id"]: c[sections.TIME_LINK] for c in project["timelines"][0]["clips"]
             if c["kind"] == "video" and c.get("kittyRole") == "game"}
    # A piece split in the editor: its tail keeps the column it was given there.
    project["timelines"][0]["clips"].append(
        _video("a40", "aT4", 6.4, 1.0, 120.0, **{sections.TIME_LINK: "a41"}))
    sections.place(project, sections.gaming_specs(_settings("game_ui"), 1920, 1080, _heads(1920, 1080)), ["m1"], mint)
    timeline = project["timelines"][0]
    columns = _columns(timeline)
    assert first == {"a2": "a2", "a3": "a3"}
    assert sorted(columns) == ["a2", "a3", "a41"]
    assert all(len(v) == len(sections.gaming_specs(_settings("game_ui"), 1920, 1080, _heads(1920, 1080)))
               for v in columns.values())
    sections.place(project, sections.framing_specs("letterbox", 1920, 1080), ["m1"], mint)
    columns = _columns(project["timelines"][0])
    assert sorted(columns) == ["a2", "a3", "a41"] and all(len(v) == 1 for v in columns.values())


def test_with_a_caption_record_every_caption_title_shares_the_look_link():
    project = _project()
    for timeline in project["timelines"]:     # a hook and a watermark: titles on lanes of their own
        p = "a" if timeline["id"] == "TL1" else "b"
        timeline["tracks"] += [{"id": f"{p}t7", "muted": False, "visible": True},
                               {"id": f"{p}t8", "muted": False, "visible": True}]
        timeline["clips"] += [_text(f"{p}12", f"{p}t7", 0.0, 3.0, "WATCH THIS"),
                              _text(f"{p}13", f"{p}t8", 0.0, 6.4, "@me")]
    mint, _ = _mint_for(project)
    sections.place(project, sections.framing_specs("center", 1920, 1080), ["m1"], mint,
                   captions=_record((0.0, 2.0, "hello"), (2.0, 4.0, "there")))
    for timeline in project["timelines"]:
        looks = {c["text"]["content"]: c.get(sections.LOOK_LINK) for c in timeline["clips"] if c["kind"] == "text"}
        assert looks == {"hello": "captions", "there": "captions", "WATCH THIS": None, "@me": None}
    assert project["clips"] is project["timelines"][0]["clips"]


def test_link_captions_file_links_every_tab_and_keeps_the_rest(tmp_path):
    project = _project()
    (tmp_path / "concat.json").write_text(json.dumps(project), encoding="utf-8")
    sections.link_captions_file(tmp_path, None)                     # no captions: the file is left alone
    assert json.loads((tmp_path / "concat.json").read_text(encoding="utf-8")) == project
    sections.link_captions_file(tmp_path, {"follow": None})         # no record: the lane with the most titles
    saved = json.loads((tmp_path / "concat.json").read_text(encoding="utf-8"))
    for timeline in saved["timelines"]:
        assert [c.get(sections.LOOK_LINK) for c in timeline["clips"] if c["kind"] == "text"] == ["captions"] * 2
        assert all(sections.LOOK_LINK not in c for c in timeline["clips"] if c["kind"] != "text")
    assert saved["clips"] == saved["timelines"][0]["clips"]
    assert not (tmp_path / "concat.json.tmp").exists()


# ---- A6: the real editor draws the lanes where the auto render does ------------------------------

@pytest.mark.skipif(not __import__("os").environ.get("CLIPS_KITTY_EDITOR_DIR"),
                    reason="needs Clips Kitty Editor named by CLIPS_KITTY_EDITOR_DIR (no window is opened)")
@pytest.mark.parametrize("preset", list(layout.PRESETS))
def test_the_real_editor_draws_each_section_where_the_auto_render_does(tmp_path, monkeypatch, preset):
    """A synthetic 1920x1080 60 fps source with a webcam and a UI strip of
    their own, built headless and drawn with the editor's preview_frame; the
    same window rendered by gaming/compose.py. Each section matches (SSIM)
    and sits within about a pixel (phase correlation)."""
    import subprocess

    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from core import binaries
    from gaming import compose
    from kitty_editor import automation, build

    exe = binaries.editor_exe()
    if exe is None:
        pytest.skip("no editor in CLIPS_KITTY_EDITOR_DIR")
    # The headless editor's own settings and logs stay in the test's folder.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    ffmpeg = binaries.ffmpeg()
    source = tmp_path / "source.mp4"
    graph = ("[0:v]scale=1920:1080,setsar=1[bg];[1:v]scale=480:324[cam];[2:v]scale=384:64[ui];"
             "[3:v]scale=480:324[cam2];[bg][cam]overlay=38:54[a];[a][ui]overlay=768:0[b];[b][cam2]overlay=1402:54[v]")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=8",
                    "-f", "lavfi", "-i", "mandelbrot=size=480x324:rate=60", "-f", "lavfi",
                    "-i", "rgbtestsrc=size=384x64:rate=60", "-f", "lavfi",
                    "-i", "testsrc=size=480x324:rate=60", "-filter_complex", graph, "-map", "[v]", "-t", "8",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", str(source)],
                   check=True)
    start, length, w, h = 2.0, 4.0, 1920, 1080
    settings = {"cam": CAM, "cam2": CAM2, "ui_box": UI, "preset": preset, "panels": []}
    heads = {"cam": (w * 0.14, h * 0.07, h * 0.25), "cam2": (w * 0.86, h * 0.07, h * 0.25)}
    lanes = sections.gaming_specs(settings, w, h, heads)
    home = tmp_path / "home"
    home.mkdir()
    plan = build.plan_clip(opts={}, window=length, base_seconds=length, lines=[], canvas=(1080, 1920), captions=False)
    canvas = {"width": 1080, "height": 1920, "fps_num": 60, "fps_den": 1}
    with automation.Session(exe, [home, tmp_path], home / "build.log") as s:
        project_dir = build.apply_clip_plan(s, home, "Clip", canvas, source, plan, window=(start, length), frame=False)
    sections.place_file(project_dir, lanes, source)
    at = 1.5 + 0.5 / 60
    drawn = tmp_path / "editor.png"
    with automation.Session(exe, [home, tmp_path], home / "check.log") as s:
        s.tool("project_open", path=project_dir)
        s.tool("preview_frame", path=project_dir, time=at, width=1080, height=1920, output=str(drawn))
        s.tool("project_close", path=project_dir, save=False)
    cut, auto, auto_png = tmp_path / "cut.mp4", tmp_path / "auto.mp4", tmp_path / "auto.png"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(start), "-i", str(source), "-t", str(length),
                    "-c:v", "libx264", "-crf", "10", str(cut)], check=True)
    from gaming import layout as gl

    compose.render(cut, auto, gl.plan(w, h, settings, heads), normalize=False)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{at:.4f}", "-i", str(auto), "-frames:v", "1",
                    str(auto_png)], check=True)
    ed = cv2.cvtColor(cv2.imread(str(drawn)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    au = cv2.cvtColor(cv2.imread(str(auto_png)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    for spec in lanes:
        if spec["role"].endswith("_fill"):
            continue
        x, y, bw, bh = (int(round(v)) for v in sections.shown_box(spec, w, h))
        x0, y0, x1, y1 = max(0, x + 4), max(0, y + 4), min(1080, x + bw - 4), min(1920, y + bh - 4)
        if spec["masks"] and spec["masks"][0]["source"]["shape"] == "circle":
            cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.35
            x0, x1, y0, y1 = int(cx - r), int(cx + r), int(cy - r), int(cy + r)
        a, b = ed[y0:y1, x0:x1], au[y0:y1, x0:x1]
        (dx, dy), _ = cv2.phaseCorrelate(a, b)
        assert abs(dx) <= 1.5 and abs(dy) <= 1.5, (spec["role"], dx, dy)
        ma, mb = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
        va = cv2.GaussianBlur(a * a, (11, 11), 1.5) - ma * ma
        vb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mb * mb
        cov = cv2.GaussianBlur(a * b, (11, 11), 1.5) - ma * mb
        c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        score = float((((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))).mean())
        print(f"A6 {preset} {spec['role']}: shift {dx:+.2f},{dy:+.2f} px, SSIM {score:.3f}")
        assert score >= 0.9, (spec["role"], score)


@pytest.mark.parametrize("input_i, input_tp, expected", [
    ("-25.0", "-20.0", 3.5481),     # quiet: brought up to -14 LUFS, peak stays under -1.5 dBTP
    ("-25.0", "-6.0", 1.6788),      # quiet with loud moments: held at -1.5 dBTP (no limiter in the export)
    ("-15.0", "0.03", 0.8385),      # already peaking: brought down to -1.5 dBTP
])
def test_the_sound_gain_never_takes_a_peak_past_the_target(monkeypatch, input_i, input_tp, expected):
    import subprocess

    stats = f'{{\n "input_i" : "{input_i}",\n "input_tp" : "{input_tp}",\n "input_lra" : "5.0"\n}}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", stats))
    assert sections.loudness_gain("clip.mp4", 0.0, 10.0) == pytest.approx(expected, abs=1e-3)
