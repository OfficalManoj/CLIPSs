"""Other languages as tabs of the clip's editor project (step KF, part A).

"Other languages..." on the Editor page makes one timeline tab per language
in the SAME editor project: a copy of the clip's own tab with its captions
translated in place (the editor's own font, size, colour and place) and,
when dubbing is on, the spoken translation on a lane of its own. The person
edits it live in the editor and exports it with the editor's Export.

Two steps, because the editor window holds the project while it is shown:

- prepare (the engine's `editor_languages` job, while the person keeps
  editing): reads the project as saved, translates the caption titles of
  the clip's own tab, speaks them when asked, and writes what it made to
  <project home>/languages/pending.json;
- apply (POST /editor/projects/{id}/languages/apply, with the editor's
  window closed for a few seconds): copies the clip's tab as it is NOW once
  per language, puts the translated words on the copies' caption titles,
  and adds the voice lane through a headless editor.

Standard library only when imported (kitty_editor/__init__.py).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from kitty_editor import registry

PENDING = "pending.json"
BACKUP = "concat.json.kf-backup"
# The clip's own sound under a dubbed voice: multilingual/dub.py DUCK, the
# level dubbing has always used. The person changes it on the clip.
UNDER_VOICE = 0.12
NO_CAPTIONS = "This project has no captions to translate."
NOT_APPLIED = "The language tabs could not be added. Your project is as it was."
HELD = "The editor still has this project open."


class Held(RuntimeError):
    """Another program (the editor's window) has the project open."""


class Failed(RuntimeError):
    """The tabs could not be added; the project is as it was."""


def folder(data_dir, project_id: str) -> Path:
    return registry.folder(data_dir, project_id) / "languages"


def _project_file(manifest: dict) -> Path:
    return Path(str(manifest.get("project_dir") or "")) / "concat.json"


def read_project(manifest: dict) -> dict:
    project = json.loads(_project_file(manifest).read_text(encoding="utf-8"))
    if not isinstance(project, dict):
        raise ValueError("not a project")
    return project


def _timelines(project: dict) -> list[dict]:
    return [t for t in project.get("timelines") or () if isinstance(t, dict)]


def source_timeline(project: dict, manifest: dict) -> dict | None:
    """The clip's own tab: the one recorded when the project was made, else
    the first that is not a language tab."""
    tabs = _timelines(project)
    wanted = manifest.get("source_timeline")
    for timeline in tabs:
        if wanted and timeline.get("id") == wanted:
            return timeline
    languages = manifest.get("languages") or {}
    return next((t for t in tabs if t.get("id") not in languages), None)


def _words(item: dict):
    text = item.get("text")
    return text.get("content") if isinstance(text, dict) else text


def caption_items(timeline: dict, manifest: dict) -> list[dict]:
    """The caption titles of a tab, in time order: every text item on the
    lane(s) holding the clip's captions. The lanes are those of the text
    items matching the project's caption record (intake.edited_captions'
    rule: both ends within 0.05 s); with no record, the lane with the most
    text items when it has two or more."""
    texts = [c for c in timeline.get("clips") or () if isinstance(c, dict) and c.get("kind") == "text"
             and isinstance(_words(c), str)]
    record = (manifest.get("captions") or {}).get("follow") if isinstance(manifest.get("captions"), dict) else None
    lanes: set[str] = set()
    try:
        for line in (record or {}).get("lines") or ():
            start, end = float(line["start"]), float(line["end"])
            for item in texts:
                a = float(item.get("start") or 0.0)
                if abs(a - start) < 0.05 and abs(a + float(item.get("duration") or 0.0) - end) < 0.05:
                    lanes.add(str(item.get("trackId")))
    except (KeyError, TypeError, ValueError):
        lanes = set()
    if not lanes:
        counts: dict[str, int] = {}
        for item in texts:
            counts[str(item.get("trackId"))] = counts.get(str(item.get("trackId")), 0) + 1
        best = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))
        if best[1] >= 2:
            lanes = {best[0]}
    return sorted((c for c in texts if str(c.get("trackId")) in lanes), key=lambda c: float(c.get("start") or 0.0))


def tabs(data_dir, project_id: str) -> dict:
    """The languages this project has a tab for (each tab still in the
    saved project), and those made but not yet added."""
    manifest = registry.load(data_dir, project_id)
    try:
        present = {t.get("id") for t in _timelines(read_project(manifest))}
    except (OSError, ValueError, TypeError):
        present = set()
    have = [{"timeline": tab, "language": code} for tab, code in (manifest.get("languages") or {}).items()
            if tab in present]
    pending = _read_pending(data_dir, project_id)
    return {"tabs": have, "pending": sorted((pending or {}).get("languages") or {})}


def _read_pending(data_dir, project_id: str) -> dict | None:
    try:
        found = json.loads((folder(data_dir, project_id) / PENDING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("languages"), dict) else None


def has_pending(data_dir, project_id: str) -> bool:
    return bool((_read_pending(data_dir, project_id) or {}).get("languages"))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def prepare(data_dir, project_id: str, languages, *, translate, speak=None, progress=None) -> dict:
    """Translate (and speak) the clip tab's caption titles for each
    language and keep the result in languages/pending.json.

    `translate(lines, code)` gives the lines with their text translated
    (multilingual.translate.translate_lines); `speak(lines, code, out)` writes
    the spoken lines to `out` and returns it, or None (no dub). The lines are
    [{"start", "end", "text"}] on the tab's own clock. Raises ValueError with
    a sentence for the person when there is nothing to do."""
    manifest = registry.load(data_dir, project_id)
    project = read_project(manifest)
    timeline = source_timeline(project, manifest)
    if timeline is None:
        raise ValueError("This project has no timeline to copy.")
    items = caption_items(timeline, manifest)
    if not items and speak is None:
        raise ValueError(NO_CAPTIONS)
    lines = [{"start": float(c.get("start") or 0.0),
              "end": float(c.get("start") or 0.0) + float(c.get("duration") or 0.0),
              "text": " ".join(str(_words(c)).split())} for c in items]
    home = folder(data_dir, project_id)
    home.mkdir(parents=True, exist_ok=True)
    pending = _read_pending(data_dir, project_id) or {"languages": {}}
    pending["source"] = timeline.get("id")
    codes = list(languages)
    for n, code in enumerate(codes):
        if progress:
            progress(f"Translating into {code}", n, len(codes))
        texts, said = {}, None
        if lines:
            done = translate(lines, code)
            for item, line in zip(items, done):
                texts[str(item["id"])] = str(line.get("text") or "")
        if speak is not None and lines:
            out = home / f"{code}-voice.m4a"
            made = speak([{**line, "text": texts.get(str(item["id"]), line["text"])}
                          for item, line in zip(items, lines)], code, out)
            said = str(made) if made else None
        pending["languages"][code] = {"texts": texts, "voice": said}
    _write_json(home / PENDING, pending)
    return {"languages": codes}


_NUMBER = re.compile(r"(\d+)$")


def _highest(project: dict) -> int:
    """The highest number any id in the file ends with: the editor's one
    counter is raised past every id when it loads a project (IdMint)."""
    top = 0

    def see(value) -> None:
        nonlocal top
        found = _NUMBER.search(str(value or ""))
        if found:
            top = max(top, int(found.group(1)))

    for item in project.get("media") or ():
        if isinstance(item, dict):
            see(item.get("id"))
    for timeline in _timelines(project):
        see(timeline.get("id"))
        for track in timeline.get("tracks") or ():
            if isinstance(track, dict):
                see(track.get("id"))
        for clip in timeline.get("clips") or ():
            if isinstance(clip, dict):
                see(clip.get("id"))
    return top


def _music_media(project: dict, home: Path) -> set[str]:
    """The media ids of the music Clips Kitty put in (media/music.*)."""
    out = set()
    for item in project.get("media") or ():
        try:
            path = Path(str(item.get("path") or ""))
            if path.stem.lower() == "music" and registry.same_path(path.parent, home / "media"):
                out.add(str(item["id"]))
        except (AttributeError, KeyError, TypeError, OSError):
            continue
    return out


def _tab_for(source: dict, code: str, texts: dict, mint, music: set[str], ducked: bool) -> dict:
    from multilingual.languages import english_name
    from video.caption_presets import font_for_language

    tab = copy.deepcopy(source)
    tab["id"] = mint("tl")
    tab["name"] = english_name(code)
    lanes = {}
    for track in tab.get("tracks") or ():
        if isinstance(track, dict):
            lanes[track.get("id")] = track["id"] = mint("t")
    for clip in tab.get("clips") or ():
        if not isinstance(clip, dict):
            continue
        old = str(clip.get("id"))
        clip["id"] = mint("c")
        clip["trackId"] = lanes.get(clip.get("trackId"), clip.get("trackId"))
        words = texts.get(old)
        if clip.get("kind") == "text" and isinstance(words, str) and words.strip():
            text = clip.get("text")
            if isinstance(text, dict):
                text["content"] = words
                family = str(text.get("fontFamily") or "Hanken Grotesk")
                text["fontFamily"] = font_for_language(family, code)
            else:
                clip["text"] = words
            clip["name"] = words.splitlines()[0][:40] or clip.get("name")
        elif ducked and clip.get("kind") in ("video", "audio") and str(clip.get("mediaId")) not in music:
            clip["volume"] = round(float(clip.get("volume", 1.0) or 0.0) * UNDER_VOICE, 4)
    return tab


def apply(data_dir, project_id: str, *, exe: str, open_session=None) -> dict:
    """Add the pending language tabs to the saved project. Held when the
    editor (or any other program) has the project open: nothing changes.
    Failed when anything goes wrong on the way: the project file is put
    back as it was and pending.json is kept for the next try."""
    if open_session is None:
        from kitty_editor.build import open_session
    pending = _read_pending(data_dir, project_id)
    if not pending or not pending.get("languages"):
        return {"added": []}
    manifest = registry.load(data_dir, project_id)
    home = registry.folder(data_dir, project_id)
    path = _project_file(manifest)
    project_dir = str(path.parent)
    from kitty_editor import automation

    # Whether anything holds the project: the editor's own lock, asked of a
    # headless editor (it knows a lock left by a crash from a live one).
    try:
        with open_session(exe, home, [folder(data_dir, project_id)]) as probe:
            probe.tool("project_open", path=project_dir)
            probe.tool("project_close", path=project_dir, save=False)
    except automation.EditorError as e:
        raise Held(HELD) from e

    backup = path.with_name(BACKUP)
    original = path.read_bytes()
    backup.write_bytes(original)
    try:
        project = json.loads(original.decode("utf-8"))
        source = source_timeline(project, manifest)
        if source is None:
            raise ValueError("no timeline to copy")
        counter = [_highest(project)]

        def mint(prefix: str) -> str:
            counter[0] += 1
            return f"{prefix}{counter[0]}"

        known = manifest.get("languages") or {}
        present = {t.get("id") for t in _timelines(project)}
        music = _music_media(project, home)
        added: list[tuple[str, str, str | None]] = []       # (tab id, code, voice file)
        for code, made in pending["languages"].items():
            if any(c == code and tab in present for tab, c in known.items()):
                continue            # it has a tab already
            voice = made.get("voice") if isinstance(made, dict) else None
            voice = voice if voice and Path(voice).is_file() else None
            tab = _tab_for(source, code, (made or {}).get("texts") or {}, mint, music, ducked=voice is not None)
            project["timelines"].append(tab)
            added.append((tab["id"], code, voice))
        if added:
            first = next(t for t in project["timelines"] if t.get("id") == added[0][0])
            project["activeTimelineId"] = first["id"]
            # The flat mirror of the active tab every save writes (doc.rs).
            project["tracks"], project["clips"] = first.get("tracks"), first.get("clips")
            if isinstance(first.get("video"), dict):
                project["video"] = first["video"]
            _write_json(path, project)
            voiced = [a for a in added if a[2]]
            if voiced:
                with open_session(exe, home, [folder(data_dir, project_id)]) as editor:
                    editor.tool("project_open", path=project_dir)
                    for tab_id, _code, voice in voiced:
                        editor.tool("edit_apply", path=project_dir, command={"op": "selectTimeline", "timelineId": tab_id})
                        media = editor.tool("media_import", path=project_dir, file=str(Path(voice).resolve()))["createdId"]
                        lane = editor.tool("track_add", path=project_dir, bottom=True)["createdId"]
                        editor.tool("clip_add", path=project_dir, mediaId=media, trackId=lane, start=0.0)
                    editor.tool("edit_apply", path=project_dir,
                                command={"op": "selectTimeline", "timelineId": added[0][0]})
                    editor.tool("project_save", path=project_dir)
                    editor.tool("project_close", path=project_dir, save=True)

        def note(m: dict) -> None:
            m.setdefault("languages", {}).update({tab: code for tab, code, _ in added})
            m.setdefault("source_timeline", source.get("id"))

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
    return {"added": [{"timeline": tab, "language": code, "voice": bool(voice)} for tab, code, voice in added]}


def language_of_tab(manifest: dict, timeline) -> str | None:
    """The language code of a language tab, None for any other."""
    if not timeline:
        return None
    return (manifest.get("languages") or {}).get(str(timeline))
