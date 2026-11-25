"""The Layout picker on the Editor page (editor suite step KG): a clip's
project rebuilt in another layout, its cuts, captions and the rest kept.

Two steps, like the language tabs (kitty_editor/languages.py), because the
editor's window holds the project while it is shown:

- prepare (the engine's `editor_layout` job, while the person keeps
  editing): works out the new lanes (kitty_editor/sections.py) and makes
  what they need: the heads in a gaming layout's webcams when they are not
  known, or a framed base video for Auto (the tracker's crop). Kept in
  <project home>/layout/pending.json;
- apply (POST /editor/projects/{id}/layout/apply, with the editor's window
  closed for a few seconds): the main lane is pointed at the new video
  (the source, or Auto's base), its pieces' times moved between the two
  clocks, the old section lanes taken off and the new ones placed. Only
  picture lanes change: cuts, speed, captions, titles, music and language
  tabs stay as they are.

Standard library only when imported (kitty_editor/__init__.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from kitty_editor import languages, registry, sections

PENDING = "pending.json"
BACKUP = "concat.json.kg-backup"
NOT_APPLIED = "The layout could not be changed. Your project is as it was."
HELD = "The editor still has this project open."
SOURCE_GONE = "This clip's original video isn't in Clips Kitty any more, so its layout can't be changed here."


class Held(RuntimeError):
    """Another program (the editor's window) has the project open."""


class Failed(RuntimeError):
    """The layout could not be changed; the project is as it was."""


def folder(data_dir, project_id: str) -> Path:
    return registry.folder(data_dir, project_id) / "layout"


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def _read_pending(data_dir, project_id: str) -> dict | None:
    try:
        found = json.loads((folder(data_dir, project_id) / PENDING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("lanes"), list) else None


def has_pending(data_dir, project_id: str) -> bool:
    return _read_pending(data_dir, project_id) is not None


def choice_of(body: dict) -> dict:
    """The picker's answer as the manifest keeps it: {"kind": "gaming",
    "gaming": settings} or {"kind": "framing", "crop": ...}. ValueError for
    anything else."""
    gaming, crop = body.get("gaming"), body.get("crop")
    if isinstance(gaming, dict) and crop is None:
        from gaming.layout import PRESETS

        if gaming.get("preset") is not None and gaming.get("preset") not in PRESETS:
            raise ValueError("That layout doesn't exist.")
        return {"kind": "gaming", "gaming": {k: gaming[k] for k in sections.LAYOUT_KEYS if k in gaming}}
    if crop in sections.FRAMINGS and gaming is None:
        return {"kind": "framing", "crop": crop}
    raise ValueError("Pick one layout.")


def prepare(data_dir, project_id: str, body: dict, *, row, config: dict, progress=None) -> dict:
    """Work out the new layout's lanes for the project of clip `row` and make
    what they need; kept in layout/pending.json. Raises ValueError with a
    sentence for the person when it can't be done."""
    from core.paths import cached_source
    from kitty_editor import build, fastbase

    manifest = registry.load(data_dir, project_id)
    choice = choice_of(body)
    opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
    source = cached_source(Path(data_dir) / "downloads", row["video_id"])
    if source is None or not Path(source).is_file():
        raise ValueError(SOURCE_GONE)
    start, length = fastbase.window(row, opts, config)
    info = build.probe(source)
    if not info["video"]:
        raise ValueError(SOURCE_GONE)
    width, height = int(info["width"]), int(info["height"])
    pending = {"layout": choice, "start": start, "length": length}
    if choice["kind"] == "gaming":
        settings = choice["gaming"]
        cached = manifest.get("heads") if isinstance(manifest.get("heads"), dict) else {}
        heads = sections.heads_from(opts, settings, width, height, cached)
        if heads is None:
            if progress:
                progress("Finding the faces in the webcams", 0, 1)
            found = sections.look_for_heads(source, settings, width, height, (start, length),
                                            (config.get("tracking") or {}).get("detector") or "yolov8n-pose.pt")
            pending["heads"] = found
            heads = sections.heads_px(found, width, height)
        pending.update(video=str(Path(source)), clock="source",
                       lanes=sections.gaming_specs(settings, width, height, heads))
    elif choice["crop"] in ("letterbox", "center"):
        pending.update(video=str(Path(source)), clock="source",
                       lanes=sections.framing_specs(choice["crop"], width, height))
    else:
        # Auto: the tracker's framing, rendered as the clip's base video.
        if progress:
            progress("Framing the clip", 0, 1)
        tracked = sections.gaming_of(opts) is None and opts.get("crop", "track") == "track"
        # "gaming": None keeps a gaming job's split off (core.pipeline D33).
        plain = {**{k: v for k, v in opts.items() if k != "gaming"}, "crop": "track", "gaming": None}
        auto_row = {k: row[k] for k in row.keys()}
        auto_row["render_opts"] = json.dumps(plain)
        if not tracked:
            # The clip's own file has another layout: no tracked crop to read back from it.
            auto_row["path"] = None
        home = registry.folder(data_dir, project_id)
        made = build.render_base(auto_row, config, data_dir, home / "work-layout")
        if made is None:
            raise ValueError(SOURCE_GONE)
        target = registry.unique_target(home / "media", registry.safe_name(f"{manifest.get('name') or 'Clip'} auto",
                                                                        "Clip auto"))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(made, target)
        pending.update(video=str(target), clock="base", lanes=sections.framing_specs("track", 1080, 1920),
                       base_key=build.base_key(auto_row, config, data_dir))
    _write_json(folder(data_dir, project_id) / PENDING, pending)
    return {"layout": choice}


def _forget_dropped(project: dict, old_video, video: Path, media_folder: Path, keep: set) -> None:
    """Take the old layout's video out of the project's bin when the server
    deletes that file after the change (a video in the project's own media
    folder, kitty_editor_api _drop_old_video), so the editor does not ask
    for a missing file on the next open. An item a piece still uses stays."""
    if not old_video or registry.same_path(old_video, video):
        return
    old = Path(str(old_video))
    try:
        if old.parent.resolve() != media_folder.resolve():
            return
    except OSError:
        return
    gone = set(sections.media_ids_for(project, old)) - keep
    if not gone:
        return
    used = {str(clip.get("mediaId")) for timeline in project.get("timelines") or () if isinstance(timeline, dict)
            for clip in timeline.get("clips") or () if isinstance(clip, dict)}
    project["media"] = [item for item in project.get("media") or ()
                        if not (isinstance(item, dict) and str(item.get("id")) in gone - used)]


def _clock(manifest: dict) -> str:
    """Whether the project's pieces count the source's time ("source", made
    from the source in step KG) or the base video's ("base")."""
    return "source" if manifest.get("clock") == "source" else "base"


def apply(data_dir, project_id: str, *, exe: str, open_session=None) -> dict:
    """Rebuild the saved project in the pending layout. Held when the editor
    (or any other program) has the project open: nothing changes. Failed
    when anything goes wrong on the way: the project file is put back as it
    was and pending.json is kept for the next try. Returns {"layout",
    "video_file", "old_video"} ({} when nothing was pending)."""
    if open_session is None:
        from kitty_editor.build import open_session
    pending = _read_pending(data_dir, project_id)
    if pending is None:
        return {}
    manifest = registry.load(data_dir, project_id)
    home = registry.folder(data_dir, project_id)
    path = Path(str(manifest.get("project_dir") or "")) / "concat.json"
    project_dir = str(path.parent)
    video = Path(str(pending["video"]))
    from kitty_editor import automation

    try:
        with open_session(exe, home, [folder(data_dir, project_id), video.parent]) as probe:
            probe.tool("project_open", path=project_dir)
            probe.tool("project_close", path=project_dir, save=False)
    except automation.EditorError as e:
        raise Held(HELD) from e

    backup = path.with_name(BACKUP)
    original = path.read_bytes()
    backup.write_bytes(original)
    try:
        if not video.is_file():
            raise ValueError("the new layout's video is gone")
        project = json.loads(original.decode("utf-8"))
        old_ids = set(sections.media_ids_for(project, manifest.get("video_file") or ""))
        new_ids = sections.media_ids_for(project, video)
        if not new_ids:
            with open_session(exe, home, [folder(data_dir, project_id), video.parent]) as editor:
                editor.tool("project_open", path=project_dir)
                editor.tool("media_import", path=project_dir, file=str(video.resolve()))
                editor.tool("project_save", path=project_dir)
                editor.tool("project_close", path=project_dir, save=True)
            project = json.loads(path.read_text(encoding="utf-8"))
            new_ids = sections.media_ids_for(project, video)
            if not new_ids:
                raise ValueError("the new layout's video was not taken in")
        counter = [languages._highest(project)]

        def mint(prefix: str) -> str:
            counter[0] += 1
            return f"{prefix}{counter[0]}"

        shift = 0.0
        before, after = _clock(manifest), pending.get("clock", "source")
        if before != after:
            shift = float(pending["start"]) if after == "source" else -float(pending["start"])
        for timeline in project.get("timelines") or ():
            if not isinstance(timeline, dict):
                continue
            ours = old_ids | set(new_ids)
            lane = sections.main_lane(timeline, ours)
            for clip in timeline.get("clips") or ():
                if (isinstance(clip, dict) and str(clip.get("trackId")) == str(lane)
                        and str(clip.get("mediaId")) in ours):
                    clip["sourceStart"] = round(max(0.0, float(clip.get("sourceStart") or 0.0) + shift), 6)
                    clip["mediaId"] = new_ids[0]
        sections.place(project, pending["lanes"], set(new_ids), mint, captions=manifest.get("captions"))
        old_video = manifest.get("video_file")
        _forget_dropped(project, old_video, video, home / "media", set(new_ids))
        _write_json(path, project)

        def note(m: dict) -> None:
            m.update(layout=pending["layout"], video_file=str(video), clock=after)
            if pending.get("heads"):
                m["heads"] = {**(m.get("heads") or {}), **pending["heads"]}
            if after == "base":
                m["base_key"] = pending.get("base_key")
            else:
                m.pop("base_key", None)
            m["notes"] = [n for n in m.get("notes") or () if n != "framing_baked"] + (
                ["framing_baked"] if after == "base" else [])

        registry.update(data_dir, project_id, note)
    except Exception as e:
        try:
            path.write_bytes(original)
        except OSError:
            pass
        raise Failed(NOT_APPLIED) from e
    try:
        (folder(data_dir, project_id) / PENDING).unlink()
    except OSError:
        pass
    return {"layout": pending["layout"], "video_file": str(video), "old_video": old_video}
