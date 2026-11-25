"""A clip's picture as the editor's own layers, straight from the SOURCE
video (editor suite step KG).

A gaming clip opens with each part of its layout (the game, the webcam, a
second webcam, the Game UI) as a picture lane of its own, cut from the
source and placed where the auto clip has it: the same Plan
gaming/compose.py renders (gaming.layout.plan), restated as the editor's
crop, scale, offset, stretch, masks and blur. Letterbox and Center are one
lane from the source too. Nothing is rendered first, so these open fast;
only Auto (the tracker's moving crop) still needs a framed base video
(kitty_editor/build.py render_base).

- mode():  which way a clip's project is made ("gaming", "letterbox",
           "center", or "base" for everything else);
- specs(): the lanes, bottom (the main lane) first;
- place(): puts them on a saved project, in the project file itself, once
           the editor has let go of it (as languages.apply does).

Links (step KG-link, the editor fork's links.rs): every piece of a column
(the main lane's piece and its copies on the section lanes) carries one
time link, so cutting, deleting, trimming, speeding or moving one does it to
all of them; every caption title carries one look link, so stretching,
moving or restyling one caption does it to every caption. Both are plain
keys on the clip in concat.json, which older editors keep and ignore.

The placement rule, the editor's own (concat stage.rs footprint): the crop
is applied first, what is left is contain-fitted to the canvas, then scaled
about its centre (stretched per axis) and moved by offsets that are
fractions of the canvas.

Standard library only when imported; gaming.* is imported inside functions
(kitty_editor/__init__.py).
"""

from __future__ import annotations

import copy

W_OUT, H_OUT = 1080, 1920       # gaming.layout.OUT_W / OUT_H: every Short's canvas
ROLE = "kittyRole"              # the tag on Clips Kitty's own picture lanes and clips
MAIN_ROLES = ("game", "picture")
NAMES = {"game": "Gameplay", "cam": "Webcam", "cam2": "Second webcam", "ui": "Game UI",
         "cam_fill": "Webcam blur", "cam2_fill": "Webcam blur"}
CROP_FLOOR = 0.102              # the editor keeps at least a tenth of a picture (Crop::tidy)
BLUR = {"kind": "blur", "amount": 1.0}     # about compose.py's sigma-96 wash (DESIGN §2)
FILL_RADIUS = 50.0              # concat.gaussian-blur's largest radius
FRAMINGS = ("track", "letterbox", "center")
# gaming/run.py LAYOUT_KEYS (that module loads OpenCV, which the routes
# never need): the settings a layout is drawn from.
LAYOUT_KEYS = ("cam", "cam_position", "game_align", "game_box", "game_fit", "preset", "order", "divider",
               "safe", "ui_box", "cam2", "panels", "places")
# Transform numbers a placement sets: a person's keys on them would fight it.
_PLACED_KEYS = ("scale", "offsetX", "offsetY", "stretchX", "stretchY")
# The editor fork's link keys (concat-project links.rs). A time link's value
# is the main lane piece's own clip id (ids are unique, so it never collides);
# the caption titles share CAPTION_LOOK.
TIME_LINK = "kittyTimeLink"
LOOK_LINK = "kittyLookLink"
CAPTION_LOOK = "captions"


# ---- which way a clip's project is made ------------------------------------------------------

def _no_layout(opts: dict, config: dict) -> bool:
    """A clip whose picture is not a 9:16 Short of the source: Longform,
    Vertical Live, Podcast, a sport, or a run with the vertical crop off."""
    from core import modes

    clips = config.get("clips") or {}
    return bool(opts.get("profile") or not clips.get("vertical", True)
                or modes.is_vertical_live(opts) or modes.is_vertical_live(config)
                or opts.get("podcast") or clips.get("podcast")
                or modes.sport(opts) or modes.sport(config))


def gaming_of(opts: dict) -> dict | None:
    """The gaming settings a clip was rendered with (render_opts["gaming"],
    gaming/run.py), as gaming.layout.plan takes them: the webcam and layout
    it actually used. None for a clip not rendered in a gaming layout."""
    g = opts.get("gaming")
    if not isinstance(g, dict) or not g:
        return None
    settings = {k: g[k] for k in LAYOUT_KEYS if k in g}
    if "used_cam" in g:
        settings["cam"] = g["used_cam"]
    if g.get("used_preset") and g.get("preset") is None:
        # A layout the app picked by itself stores no preset: the render
        # (gaming.layout.resolve) drew it with the game whole and the webcam
        # on top unless told otherwise. Keep that look when the preset it
        # used is written in, or the plan would zoom the game to fill.
        settings.setdefault("order", "game_top" if g.get("cam_position") == "bottom" else "cam_top")
        settings.setdefault("game_fit", settings.get("game_fit") or "fit")
    if g.get("used_preset"):
        settings["preset"] = g["used_preset"]
    return settings


def layout_of(opts: dict, manifest: dict | None, config: dict) -> dict | None:
    """What the Layout picker shows for a clip's project:
    {"kind": "gaming", "gaming": settings}, {"kind": "framing", "crop":
    "track" | "letterbox" | "center"}, or None for a clip with no layout to
    pick (Longform, Vertical Live, Podcast, sport). A choice made in the
    picker (manifest["layout"]) wins over the clip's own."""
    if _no_layout(opts, config):
        return None
    chosen = (manifest or {}).get("layout")
    if isinstance(chosen, dict):
        if chosen.get("kind") == "gaming" and isinstance(chosen.get("gaming"), dict):
            return {"kind": "gaming", "gaming": dict(chosen["gaming"])}
        if chosen.get("kind") == "framing" and chosen.get("crop") in FRAMINGS:
            return {"kind": "framing", "crop": chosen["crop"]}
    gaming = gaming_of(opts)
    if gaming is not None:
        return {"kind": "gaming", "gaming": gaming}
    crop = opts.get("crop")
    return {"kind": "framing", "crop": crop if crop in ("letterbox", "center") else "track"}


def mode(opts: dict, manifest: dict | None, config: dict, source_ok: bool = True) -> str:
    """"gaming", "letterbox" or "center" for a clip whose project is made
    from the source with no base render; "base" for every other clip (the
    tracker's crop, and the layouts this step leaves as they were) and when
    the source video is gone (the clip's own file is used then)."""
    if not source_ok:
        return "base"
    shown = layout_of(opts, manifest, config)
    if shown is None:
        return "base"
    if shown["kind"] == "gaming":
        return "gaming"
    return shown["crop"] if shown["crop"] in ("letterbox", "center") else "base"


# ---- the placement rule -----------------------------------------------------------------------

def _grown(lo: float, size: float, full: float) -> tuple[float, float]:
    """A span at least CROP_FLOOR of the frame, centred on the given one and
    kept inside the frame."""
    want = CROP_FLOOR * full
    if size >= want:
        return lo, size
    start = lo + size / 2 - want / 2
    return min(max(0.0, start), full - want), want


def place_box(src: tuple, rect: tuple, width: int, height: int, *, circle: bool = False,
              canvas: tuple = (W_OUT, H_OUT)) -> dict:
    """The editor's numbers that show source box `src` (x, y, w, h px of a
    width x height video) exactly over canvas rect `rect` (x, y, w, h px):
    {"crop", "scale", "offsetX", "offsetY", "stretchY", "masks"}.

    A box under the editor's crop floor gets a crop grown round it and a
    rectangle mask down to the box; a circle gets a circle mask."""
    cw_out, ch_out = canvas
    sx, sy, sw, sh = (float(v) for v in src)
    rx, ry, rw, rh = (float(v) for v in rect)
    cx, cwid = _grown(sx, sw, width)
    cy, chei = _grown(sy, sh, height)
    crop = {"left": round(cx / width, 6), "top": round(cy / height, 6),
            "right": round(max(0.0, 1 - (cx + cwid) / width), 6),
            "bottom": round(max(0.0, 1 - (cy + chei) / height), 6)}
    fit = min(cw_out / cwid, ch_out / chei)
    scale = rw / (sw * fit)
    stretch = rh / (sh * fit * scale)
    centre_x = rx + rw / 2 - ((sx + sw / 2) - (cx + cwid / 2)) * fit * scale
    centre_y = ry + rh / 2 - ((sy + sh / 2) - (cy + chei / 2)) * fit * scale * stretch
    masks = []
    grown = abs(cwid - sw) > 1e-6 or abs(chei - sh) > 1e-6
    if grown or circle:
        short = min(cwid, chei)
        masks.append({"id": "kitty", "name": "Clips Kitty", "mode": "add",
                      "source": {"kind": "shape", "shape": "circle" if circle else "rectangle",
                                 "width": round(sw / short, 6), "height": round(sh / short, 6), "roundness": 0.0},
                      "x": round(((sx + sw / 2) - (cx + cwid / 2)) / cwid, 6),
                      "y": round(((sy + sh / 2) - (cy + chei / 2)) / chei, 6),
                      "rotation": 0.0, "scale": 1.0, "feather": 0.0, "expand": 0.0, "opacity": 1.0})
    return {"crop": None if all(v <= 0 for v in crop.values()) else crop,
            "scale": round(scale, 6), "offsetX": round((centre_x - cw_out / 2) / cw_out, 6),
            "offsetY": round((centre_y - ch_out / 2) / ch_out, 6),
            "stretchY": round(stretch, 6) if abs(stretch - 1) > 1e-4 else 1.0, "masks": masks}


def footprint(spec: dict, width: int, height: int, canvas: tuple = (W_OUT, H_OUT)) -> tuple:
    """Where a lane spec's picture lands on the canvas (x, y, w, h px), by
    the editor's rule (stage.rs footprint): for checks."""
    cw_out, ch_out = canvas
    crop = spec.get("crop") or {"left": 0, "top": 0, "right": 0, "bottom": 0}
    pw = width * max(0.1, 1 - crop["left"] - crop["right"])
    ph = height * max(0.1, 1 - crop["top"] - crop["bottom"])
    fit = min(cw_out / pw, ch_out / ph)
    w = pw * fit * spec["scale"] * spec.get("stretchX", 1.0)
    h = ph * fit * spec["scale"] * spec.get("stretchY", 1.0)
    x = cw_out / 2 + spec["offsetX"] * cw_out - w / 2
    y = ch_out / 2 + spec["offsetY"] * ch_out - h / 2
    return x, y, w, h


def shown_box(spec: dict, width: int, height: int, canvas: tuple = (W_OUT, H_OUT)) -> tuple:
    """The part of the canvas a lane actually shows: its footprint, cut
    down to its mask's box when it has one (for checks)."""
    x, y, w, h = footprint(spec, width, height, canvas)
    if not spec.get("masks"):
        return x, y, w, h
    crop = spec.get("crop") or {"left": 0, "top": 0, "right": 0, "bottom": 0}
    pw = width * (1 - crop["left"] - crop["right"])
    ph = height * (1 - crop["top"] - crop["bottom"])
    mask = spec["masks"][0]
    short = min(pw, ph)
    mw, mh = mask["source"]["width"] * short / pw, mask["source"]["height"] * short / ph     # of the picture
    mx, my = 0.5 + mask["x"] - mw / 2, 0.5 + mask["y"] - mh / 2
    return x + mx * w, y + my * h, mw * w, mh * h


# ---- the lanes -----------------------------------------------------------------------------

def _lane(role: str, numbers: dict, **more) -> dict:
    out = {"role": role, "name": NAMES.get(role, ""), "main": role in MAIN_ROLES,
           "background": None, "videoEffects": [], **numbers}
    out.update(more)
    return out


def gaming_specs(settings: dict, width: int, height: int, heads: dict | None = None) -> list[dict]:
    """A gaming clip's lanes, bottom first: the game on the main lane, then
    each other part in the order the auto render lays them down (a webcam
    moved down for the platform's top bar gets a blur of itself under it).
    `heads`: {"cam": (centre x, top, chin) px} as gaming.layout.plan takes."""
    from gaming import layout

    plan = layout.plan(width, height, settings, heads or {})
    game = plan.element("game")
    contain = game.fit == "contain"
    specs = []
    if contain:
        sx, sy, sw, sh = game.src
        dx, dy, dw, dh = game.dest
        k = min(dw / sw, dh / sh)
        rw, rh = sw * k, sh * k
        ry = {"top": dy, "bottom": dy + dh - rh}.get(game.anchor, dy + (dh - rh) / 2)
        rect = (dx + (dw - rw) / 2, ry, rw, rh)
    else:
        rect = game.dest
    specs.append(_lane("game", place_box(game.src, rect, width, height), background=dict(BLUR) if contain else None))
    for e in plan.elements:
        if e.role in ("game", "bg"):
            continue
        src, rect = e.src, e.dest
        if e.shift:
            dx, dy, dw, dh = e.dest
            specs.append(_lane(f"{e.role}_fill", place_box(e.src, e.dest, width, height),
                               videoEffects=[{"id": "concat.gaussian-blur", "params": {"radius": FILL_RADIUS},
                                              "enabled": True}]))
            keep = (dh - e.shift) / dh
            src = (e.src[0], e.src[1], e.src[2], e.src[3] * keep)
            rect = (dx, dy + e.shift, dw, dh - e.shift)
        specs.append(_lane(e.role, place_box(src, rect, width, height, circle=e.shape == "circle")))
    return specs


def framing_specs(crop: str, width: int, height: int) -> list[dict]:
    """Letterbox (the whole frame, fitted, on a blur of itself) or Center (a
    still crop from the middle, filling the Short), on one lane; for "track"
    (Auto, on its framed base) the base shown as it is."""
    if crop == "letterbox":
        return [_lane("picture", {"crop": None, "scale": 1.0, "offsetX": 0.0, "offsetY": 0.0, "stretchY": 1.0,
                                  "masks": []}, background=dict(BLUR))]
    if crop == "center":
        cover = max(W_OUT / width, H_OUT / height) / min(W_OUT / width, H_OUT / height)
        return [_lane("picture", {"crop": None, "scale": round(cover, 6), "offsetX": 0.0, "offsetY": 0.0,
                                  "stretchY": 1.0, "masks": []})]
    return [_lane("picture", {"crop": None, "scale": 1.0, "offsetX": 0.0, "offsetY": 0.0, "stretchY": 1.0,
                              "masks": []})]


def specs(kind: str, *, width: int, height: int, gaming: dict | None = None, heads: dict | None = None) -> list[dict]:
    """The lanes for "gaming" (with `gaming` settings) or a framing."""
    if kind == "gaming":
        return gaming_specs(gaming or {}, width, height, heads)
    return framing_specs(kind, width, height)


# ---- heads (face-safe framing) -----------------------------------------------------------------

def heads_from(opts: dict, settings: dict, width: int, height: int, cached: dict | None = None) -> dict | None:
    """The streamer's head in each webcam in source px, when it is known
    without looking: what the auto render stored (render_opts.gaming
    heads_norm, for the webcam it used), or what an earlier build looked up
    for the same box (`cached`, the manifest's). None when some webcam's
    head is unknown."""
    heads = {}
    g = opts.get("gaming") if isinstance(opts.get("gaming"), dict) else {}
    stored = g.get("heads_norm") if isinstance(g.get("heads_norm"), dict) else {}
    used = gaming_of(opts) or {}
    for role in ("cam", "cam2"):
        box = settings.get(role)
        if not box:
            continue
        found = None
        if role in stored and _same_box(used.get(role), box):
            found = stored[role]
        else:
            entry = (cached or {}).get(role)
            if isinstance(entry, dict) and _same_box(entry.get("box"), box):
                if entry.get("head") is None:
                    continue            # looked for before: nobody in it
                found = entry["head"]
        if found is None:
            return None
        hx, top, chin = (float(v) for v in found)
        heads[role] = (hx * width, top * height, chin * height)
    return heads


def _same_box(a, b) -> bool:
    try:
        return a is not None and b is not None and all(abs(float(x) - float(y)) < 1e-3 for x, y in zip(a, b)) \
            and len(a) == len(b)
    except (TypeError, ValueError):
        return False


def look_for_heads(source, settings: dict, width: int, height: int, window: tuple, detector: str) -> dict:
    """The head in each webcam box of `settings`, looked for in the clip's
    window of the source: {"cam": {"box": [...], "head": [cx, top, chin]
    normalized, or None}} (what heads_from reads back as `cached`). A
    webcam that could not be looked at is left out (looked at again next
    time), and framed without its head meanwhile."""
    out = {}
    for role in ("cam", "cam2"):
        box = settings.get(role)
        if not box:
            continue
        try:
            from gaming import detect

            head = detect.head_in_box(source, tuple(box), detector, window=window)
        except Exception as e:      # framing without the head is still the layout
            print(f"      Editor: couldn't look for the head in the {role} ({type(e).__name__}: {e})")
            continue
        out[role] = {"box": [float(v) for v in box],
                     "head": None if head is None else [head[0] / width, head[1] / height, head[2] / height]}
    return out


def heads_px(found: dict, width: int, height: int) -> dict:
    return {role: (e["head"][0] * width, e["head"][1] * height, e["head"][2] * height)
            for role, e in (found or {}).items() if isinstance(e, dict) and e.get("head")}


# ---- sound -----------------------------------------------------------------------------------

def loudness_gain(source, start: float, length: float) -> float:
    """The volume that brings the window to the renders' loudness target
    (video/encoding.py LOUDNORM), as the base's loudnorm did: 1.0 when it
    can't be measured. Held to 0.25-4, and never past the target's true
    peak (the editor's export has no limiter)."""
    import json
    import re
    import subprocess

    from core.binaries import ffmpeg
    from video.encoding import LOUDNESS_LUFS, LOUDNESS_PEAK

    try:
        done = subprocess.run(
            [ffmpeg(), "-hide_banner", "-nostats", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, length):.3f}",
             "-i", str(source), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        found = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", done.stderr or "")
        stats = json.loads(found.group(0)) if found else {}
        measured = float(stats["input_i"]) if found else None
        peak = float(stats.get("input_tp", "-inf"))
    except Exception:          # no FFmpeg, no sound, an unreadable file: left as it is
        measured, peak = None, float("-inf")
    if measured is None or measured < -70:      # silence, or not measured
        return 1.0
    gain = min(10 ** ((LOUDNESS_LUFS - measured) / 20), 10 ** ((LOUDNESS_PEAK - peak) / 20))
    return round(min(4.0, max(0.25, gain)), 4)


# ---- the project file ------------------------------------------------------------------------

def _timelines(project: dict) -> list[dict]:
    return [t for t in project.get("timelines") or () if isinstance(t, dict)]


def main_lane(timeline: dict, media_ids) -> str | None:
    """The lane the clip's picture is on: the one tagged as Clips Kitty's
    main lane, else the one with the most pieces of the clip's video."""
    for track in timeline.get("tracks") or ():
        if isinstance(track, dict) and track.get(ROLE) in MAIN_ROLES:
            return track.get("id")
    ids = {str(m) for m in media_ids if m}
    counts: dict[str, int] = {}
    for clip in timeline.get("clips") or ():
        if isinstance(clip, dict) and clip.get("kind") == "video" and str(clip.get("mediaId")) in ids:
            counts[str(clip.get("trackId"))] = counts.get(str(clip.get("trackId")), 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else None


def _set_numbers(clip: dict, spec: dict) -> None:
    for key in ("scale", "offsetX", "offsetY"):
        clip[key] = spec[key]
    clip["rotation"] = 0.0
    clip.pop("stretchX", None)
    if abs(spec.get("stretchY", 1.0) - 1.0) > 1e-9:
        clip["stretchY"] = spec["stretchY"]
    else:
        clip.pop("stretchY", None)
    for key, value in (("crop", spec.get("crop")), ("background", spec.get("background"))):
        if value:
            clip[key] = copy.deepcopy(value)
        else:
            clip.pop(key, None)
    if spec.get("masks"):
        clip["masks"] = copy.deepcopy(spec["masks"])
    else:
        clip.pop("masks", None)
    keys = [k for k in clip.get("keys") or () if isinstance(k, dict) and k.get("property") not in _PLACED_KEYS]
    if keys:
        clip["keys"] = keys
    else:
        clip.pop("keys", None)
    clip[ROLE] = spec["role"]
    if spec.get("name"):
        clip["name"] = spec["name"]


def place(project: dict, lanes: list[dict], media_ids, mint, captions=None) -> dict:
    """The lanes of `lanes` (specs(): the main lane first) on every timeline
    of a saved project, in place, and the project returned.

    On each tab the main lane's pieces are the template: they get the main
    lane's numbers in place (keeping their ids, cuts, speed and sound), and
    every other lane is a new lane directly above it with a copy of each
    piece (a new id from `mint("c")`), muted. Lanes an earlier placement
    added are taken off first. Captions, titles, music and the rest are not
    touched. `media_ids`: the clip video's media items (the main lane is
    found by them when it is not tagged).

    Each main lane piece and its copies share one time link (TIME_LINK):
    the piece's own id, or the link it already has (a piece split in the
    editor keeps its own column through a Layout change). With `captions`
    (the project's caption record), the caption titles get the look link
    too (link_captions)."""
    for timeline in _timelines(project):
        lane = main_lane(timeline, media_ids)
        if lane is None:
            continue
        gone = {str(t.get("id")) for t in timeline.get("tracks") or ()
                if isinstance(t, dict) and t.get(ROLE) and t.get(ROLE) not in MAIN_ROLES}
        tracks = [t for t in timeline.get("tracks") or () if not (isinstance(t, dict) and str(t.get("id")) in gone)]
        clips = [c for c in timeline.get("clips") or () if not (isinstance(c, dict) and str(c.get("trackId")) in gone)]
        template = sorted((c for c in clips if isinstance(c, dict) and str(c.get("trackId")) == str(lane)),
                          key=lambda c: float(c.get("start") or 0.0))
        for clip in template:
            _set_numbers(clip, lanes[0])
            clip[TIME_LINK] = str(clip.get(TIME_LINK) or clip["id"])
        at = next(i for i, t in enumerate(tracks) if isinstance(t, dict) and str(t.get("id")) == str(lane))
        tracks[at][ROLE] = lanes[0]["role"]
        for n, spec in enumerate(lanes[1:], start=1):
            new_lane = {"id": mint("t"), "muted": False, "visible": True, ROLE: spec["role"]}
            # The stack is stored bottom first (the editor's AddTrack): each
            # one directly above the main lane and the lanes put before it.
            tracks.insert(at + n, new_lane)
            for piece in template:
                made = copy.deepcopy(piece)
                made["id"] = mint("c")
                made["trackId"] = new_lane["id"]
                for key in ("detachedFrom", "voice", "enhanced", "cutout", "motion", "stabilize", "filters"):
                    made.pop(key, None)
                made["filters"] = []
                _set_numbers(made, spec)
                made.pop("background", None)
                made["videoEffects"] = copy.deepcopy(spec.get("videoEffects") or [])
                made["muted"] = True
                clips.append(made)
        timeline["tracks"], timeline["clips"] = tracks, clips
        if captions:
            link_captions(timeline, captions)
    _mirror(project)
    return project


def _mirror(project: dict) -> None:
    active = next((t for t in _timelines(project) if t.get("id") == project.get("activeTimelineId")), None)
    if active is not None:
        # The flat mirror of the active tab every save writes (doc.rs).
        project["tracks"], project["clips"] = active.get("tracks"), active.get("clips")


def link_captions(timeline: dict, captions) -> int:
    """One look link on every caption title of a tab (languages.caption_items:
    the titles matching the caption record, or the lane holding the most
    titles), so the editor stretches, moves and restyles them together. The
    hook and the watermark are titles on lanes of their own and are left
    alone. How many titles were linked."""
    from kitty_editor import languages

    items = languages.caption_items(timeline, {"captions": captions})
    for item in items:
        item[LOOK_LINK] = CAPTION_LOOK
    return len(items)


def link_captions_file(project_dir, captions) -> None:
    """link_captions on every tab of a project the editor has just saved and
    closed (a fresh build: nothing else holds it)."""
    import json
    import os
    from pathlib import Path

    if not captions:
        return
    path = Path(project_dir) / "concat.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    for timeline in _timelines(project):
        link_captions(timeline, captions)
    _mirror(project)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def media_ids_for(project: dict, path) -> list[str]:
    """The media items of a project that are this file."""
    from kitty_editor import registry

    out = []
    for item in project.get("media") or ():
        try:
            if isinstance(item, dict) and registry.same_path(item.get("path"), path):
                out.append(str(item["id"]))
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return out


def place_file(project_dir, lanes: list[dict], video, captions=None) -> None:
    """place() on a project the editor has just saved and closed (a fresh
    build: nothing else holds it)."""
    import json
    import os
    from pathlib import Path

    from kitty_editor import languages

    path = Path(project_dir) / "concat.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    counter = [languages._highest(project)]

    def mint(prefix: str) -> str:
        counter[0] += 1
        return f"{prefix}{counter[0]}"

    place(project, lanes, media_ids_for(project, video), mint, captions=captions)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)
