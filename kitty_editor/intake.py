"""Taking the editor's exports back in.

The editor's Export writes into the project's own out/ folder. When an
export has finished, the desktop app tells the engine the file's NAME (never
a path), and take_in moves it to where it belongs:

- a clip's project, the clip is there and still on the file the project
  stands on: the export becomes the clip's video (data/clips/projects/), the
  clip's old file is removed only after that worked;
- the clip was changed since (rendered again, for example): the export is
  kept beside it in data/clips/projects/ and the clip is left alone;
- no clip to re-point (a whole video, a file, a deleted clip): the export
  goes to the person's export folder under the name typed in the editor's
  Export sheet, which starts as "<project name> - <date>".

A failure before the clip's row is changed leaves the clip exactly as it
was; nothing is ever deleted on a failure.

Standard library only when imported.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import date
from pathlib import Path

from kitty_editor import registry

SETTLE = 3.0        # seconds a file left in out/ must have been still before recovery takes it
# How long POST /editor/projects/{id}/exports waits while the same project's
# exports are being taken in (a reopen's recover, K-D17): it then answers
# with what that run did, never "busy" for an export it took in.
LOCK_WAIT = 120.0
# The editor's own work files (concat-export's lib.rs names them so while it renders).
_WORK_FILE = re.compile(r"^\..*\.concat-(?:video|audio|muxed)\.[^.]+$", re.IGNORECASE)

_BUSY: dict[str, threading.Lock] = {}
_BUSY_GUARD = threading.Lock()


class BadName(ValueError):
    """Not a plain export file name."""


class NoSuchExport(LookupError):
    """No such file in the project's out/ folder."""


class Busy(RuntimeError):
    """This project's export is being taken in right now."""


class BadFolder(ValueError):
    """The export folder can't be used."""


class Failed(RuntimeError):
    """The export could not be taken in; everything is as it was."""


def _project_lock(project_id: str) -> threading.Lock:
    with _BUSY_GUARD:
        return _BUSY.setdefault(project_id, threading.Lock())


def unfinished(name: str) -> bool:
    """One of the editor's own work files while it renders:
    .<name>.concat-video.<ext>, .concat-audio. or .concat-muxed."""
    return _WORK_FILE.match(name) is not None


def checked_name(file) -> str:
    """The export's file name, when it is one the editor's export sheet can
    write (its file_name(): trimmed, not only dots, none of / \\ : * ? " < > |
    or a control character) plus a video ending the editor exports: never a
    folder part or a drive, never one of the editor's unfinished work files,
    at most 255 characters (a file name's limit on Windows). Dots are fine
    anywhere ("Wait... what happened.mp4", ".intro.mp4"): the file is then
    looked for only directly in out/ (_in_out)."""
    name = str(file or "")
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    if (not name or len(name) > 255 or name != name.strip() or not stem.strip(". ")
            or any(ch in name for ch in '/\\:*?"<>|') or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or unfinished(name) or not name.lower().endswith(registry.EXPORT_SUFFIXES)):
        raise BadName(name)
    return name


def _in_out(out: Path, name: str) -> Path | None:
    """out/<name> when it is a regular file directly in out/, else None."""
    candidate = out / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve().parent != out.resolve():
            return None
    except OSError:
        return None
    return candidate


def export_folder(raw) -> Path | None:
    """The person's export folder from the app's settings, made if need be.
    None when none was given; BadFolder for one that can't be used (a
    network share, a relative path, one that can't be made)."""
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw)
    if text.startswith("\\\\") or text.startswith("//") or "\x00" in text:
        raise BadFolder("Clips Kitty can't save into a network folder.")
    target = Path(text)
    if not target.is_absolute():
        raise BadFolder("Clips Kitty can't save into that folder.")
    try:
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BadFolder("Clips Kitty can't save into that folder.") from e
    return target


def _move(source: Path, target: Path) -> None:
    """Move a file. Across drives it is copied, the copy's size checked
    against the source's, and only then is the source removed."""
    try:
        os.replace(source, target)
        return
    except OSError:
        pass
    size = source.stat().st_size
    try:
        shutil.copy2(source, target)
        if target.stat().st_size != size:
            raise OSError("the copy is not complete")
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    try:
        source.unlink()
    except OSError:
        pass        # the copy is whole; the leftover in out/ is taken for a repeat and answered as one


def _recorded(manifest: dict, name: str) -> dict | None:
    for entry in reversed(manifest.get("exports") or []):
        if isinstance(entry, dict) and entry.get("file") == name:
            return entry
    return None


def _answer(entry: dict, again: bool, clip_id=None) -> dict:
    path = Path(entry["path"])
    out = {"result": entry["result"], "path": str(path), "folder": str(path.parent), "file": path.name,
           "seconds": float(entry.get("seconds") or 0), "again": again}
    if entry.get("clip_id") is not None:
        out["clip_id"] = entry["clip_id"]
    if entry["result"] == "kept_beside":
        out["reason"] = "clip_changed"
    if entry.get("captions") in ("followed", "kept"):
        out["captions"] = entry["captions"]
    return out


def edited_captions(project_dir, record: dict, timeline_id=None) -> list[dict] | None:
    """The clip's caption lines with the words now on the editor's titles
    (step KC), from the project's saved file: each line the project was
    given (`record`, build.caption_record) is found again as the one text
    item starting AND ending where it does (within 0.05 s), so the hook or
    a text watermark that starts with it is never taken for a caption. A
    title emptied in the editor drops its line.

    None when any line is not found exactly once that way (moved, trimmed,
    split, cut or deleted in the editor, or a duplicate) or the file can't
    be read: the clip then keeps its lines.

    `timeline_id` is the clip's own tab (step KF: a project may also have
    language tabs); without it, the tab that was active when it was saved."""
    try:
        project = json.loads((Path(project_dir) / "concat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(project, dict):
        return None
    items = project.get("clips")
    wanted = timeline_id or project.get("activeTimelineId")
    for timeline in project.get("timelines") or ():
        if isinstance(timeline, dict) and timeline.get("id") == wanted:
            items = timeline.get("clips")
    texts = []
    for item in items or ():
        try:
            if isinstance(item, dict) and item.get("kind") == "text":
                text = item.get("text")
                words = text.get("content") if isinstance(text, dict) else text
                texts.append((str(item["id"]), float(item["start"]), float(item["start"]) + float(item["duration"]),
                              words))
        except (KeyError, TypeError, ValueError):
            continue
    lines, source = record.get("lines") or [], record.get("source") or []
    if not lines or len(lines) != len(source):
        return None
    # A caption with the same span as the hook or a text watermark (the
    # record's "titles"): with that caption deleted, the other title would
    # be found in its place, so the clip keeps its lines.
    try:
        titles = [(float(a), float(b)) for a, b in record.get("titles") or ()]
        if any(abs(float(line["start"]) - a) < 0.1 and abs(float(line["end"]) - b) < 0.1
               for line in lines for a, b in titles):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    used: set[str] = set()
    out = []
    for line, own in zip(lines, source):
        start, end = float(line["start"]), float(line["end"])
        # Both ends, every time: the hook or a text watermark at 0 s, a
        # trimmed title or the halves of a split title never match.
        found = [x for x in texts if x[0] not in used and abs(x[1] - start) < 0.05 and abs(x[2] - end) < 0.05]
        if len(found) != 1 or not isinstance(found[0][3], str):
            return None
        used.add(found[0][0])
        words = " ".join(found[0][3].split())
        if words:
            out.append({"start": float(own["start"]), "end": float(own["end"]), "text": words})
    return out


def follow_captions(db, row, manifest: dict) -> str | None:
    """After an export became the clip's video: the clip's saved caption
    lines take the words typed on the editor's titles, so translation, the
    MCP clip_captions tool and a translated burn use them (step KC).

    "followed" when the words changed and were saved (the clip's saved
    translations, made from the old words, are then removed), "same" when every
    caption is still in place with the clip's own words (nothing to save),
    "kept" when the project was given the clip's lines one for one but they
    no longer match them or every title was emptied (the clip keeps its
    lines), None when there was
    nothing to compare. "same" is kept on the export's record only: the
    answer leaves it out (_answer), and subtitle files read it
    (subtitles_follow). A
    project whose captions never matched the clip's lines one for one (the
    clip's own cuts or speed had moved them) has no record: None, so no
    notice says the person moved captions they never touched.
    Never raises: the export is the clip's video whatever happens here."""
    try:
        captions = manifest.get("captions")
        if not isinstance(captions, dict):
            return None
        record = captions.get("follow")
        if not isinstance(record, dict):
            return None
        lines = edited_captions(manifest.get("project_dir") or "", record, manifest.get("source_timeline"))
        if not lines:
            return "kept"           # not found, or every title emptied: never "followed" with no lines
        opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
        current = opts.get("caption_lines")
        source = record.get("source") or []
        # The clip's own lines must still be ones the project was given
        # (their times; the words, or a line emptied, may come from a last
        # export of this project).
        if current:
            usable = [x for x in current if isinstance(x, dict) and str(x.get("text") or "").strip()]
            if not usable or not all(
                any(abs(float(a["start"]) - float(b["start"])) <= 0.01
                    and abs(float(a["end"]) - float(b["end"])) <= 0.01 for b in source)
                for a in usable
            ):
                return "kept"
            before = [" ".join(str(x["text"]).split()) for x in usable]
        else:
            before = [" ".join(str(x["text"]).split()) for x in source]
        # Both sides with their spaces and line breaks collapsed, as the
        # edited words are: a double space alone is never "followed".
        if [x["text"] for x in lines] == before:
            return "same"           # the same words, every caption in place: nothing to save
        opts["caption_lines"] = lines
        db.set_clip(int(row["id"]), render_opts=json.dumps(opts))
        _drop_translations(db, int(row["id"]))
        return "followed"
    except Exception:
        return "kept"


def _drop_translations(db, clip_id: int) -> None:
    """The clip's saved translations, made from its words before the edit:
    removed, so no other language goes out with the old words. The next
    translation starts from the new ones. Never raises."""
    try:
        for saved in db.translations_for(clip_id):
            db.delete_translation(clip_id, saved["language"])
    except Exception:
        pass


def subtitles_follow(data_dir, path) -> bool:
    """Whether subtitle files made from a clip's caption lines, at their own
    times, fit its file `path`, an editor export (step KC): True only when
    the take-in that made that export the clip's video recorded its captions
    as followed, or as the same words with every caption still in place.
    False with no such record: the editor moved the captions (a cut, a trim,
    a speed change; "kept"), the project had no caption record (the clip's
    own cuts or speed, captions off), the project or its record is gone.
    Then the person is asked first, as for a burn or a dub. Never raises."""
    try:
        project_id = registry.project_of_export(path, data_dir)
        if not project_id:
            return False
        manifest = registry.load(data_dir, project_id)
        for entry in reversed(manifest.get("exports") or []):
            if (isinstance(entry, dict) and entry.get("result") == "replaced_clip"
                    and registry.same_path(entry.get("path"), path)):
                return entry.get("captions") in ("followed", "same")
    except Exception:
        pass
    return False


def captions_moved(db, found: dict) -> dict:
    """Of these editor-export clips (StateDB.project_export_clips), the
    ones subtitle files alone would not fit: all but those whose last editor
    export kept every caption in place (subtitles_follow). For POST
    /translate (server/api.py project_edits_left_out) and the translate
    job (server/jobs.py)."""
    data_dir = db.data_dir()
    out = {}
    for clip_id, project_id in found.items():
        try:
            row = db.get_clip(int(clip_id))
        except Exception:
            row = None
        if row is None or data_dir is None or not subtitles_follow(data_dir, row["path"]):
            out[clip_id] = project_id
    return out


def take_in(data_dir, project_id: str, file, *, folder=None, open_db, publish=None, clip_lock=None,
            wait: bool | float = False, timeline=None) -> dict:
    """Take the export `file` (a name in the project's out/) in. `folder`
    is the person's export folder (used only when there is no clip to
    re-point); `open_db` gives a StateDB to use and close; `publish` sends
    an event to the app's windows.

    `wait`: False tries the project's lock once, True waits for it, a
    number waits that many seconds; Busy when it is not had. An export
    taken in meanwhile (by recover) is then answered as it was recorded.

    `timeline`: the id of the tab the export was made from, as the editor
    reports it (step KF). A language tab's export, or one of a project with
    language tabs whose tab is not known, never becomes the clip's video: it
    goes to the export folder as "<name> (<Language>)".

    Raises BadName, registry.NotFound, NoSuchExport, Busy, BadFolder or
    Failed; whatever it raises, the clip and its file are as they were."""
    data_dir = Path(data_dir)
    name = checked_name(file)
    manifest = registry.load(data_dir, project_id)
    if manifest.get("damaged"):
        raise NoSuchExport(name)
    lock = _project_lock(project_id)
    if wait is True:
        held = lock.acquire()
    elif wait:
        held = lock.acquire(timeout=max(0.0, float(wait)))
    else:
        held = lock.acquire(blocking=False)
    if not held:
        raise Busy(project_id)
    try:
        return _take_in(data_dir, project_id, name, folder, open_db, publish or (lambda event: None), clip_lock,
                        timeline)
    finally:
        lock.release()


def _take_in(data_dir: Path, project_id: str, name: str, folder, open_db, publish, clip_lock,
             timeline=None) -> dict:
    manifest = registry.load(data_dir, project_id)
    out = registry.folder(data_dir, project_id) / "out"
    source = _in_out(out, name)
    if source is None:
        entry = _recorded(manifest, name)
        if entry is not None:
            return _answer(entry, again=True)       # told twice: harmless
        raise NoSuchExport(name)
    # A playable video or nothing changes: a broken, empty or sound-only file
    # never becomes the clip's video (and the clip's own file is never
    # removed for one). It stays in out/, where the app can find it again.
    seconds = registry.probe_seconds(source)
    if seconds <= 0:
        raise Failed("The export isn't a video Clips Kitty can play.")
    suffix = source.suffix.lower()
    exports = registry.exports_folder(data_dir)
    count = len(manifest.get("exports") or []) + 1
    clip = manifest.get("clip") if manifest.get("kind") == "clip" else None
    tabs = manifest.get("languages") or {}
    language = tabs.get(str(timeline)) if timeline else None
    if tabs and (not timeline or language):
        clip = None         # a language tab's export (or one whose tab isn't known): to the folder

    if isinstance(clip, dict) and clip.get("id") is not None:
        if clip_lock is None:
            from server.jobs import clip_lock  # the lock a re-render of the clip holds
        with clip_lock(clip["id"]):
            db = open_db()
            try:
                row = db.get_clip(int(clip["id"]))
                if row is not None:
                    unchanged = (registry.same_path(row["path"], clip.get("file"))
                                 and registry.stamp(row["path"]) == clip.get("file_stamp"))
                    try:
                        exports.mkdir(parents=True, exist_ok=True)
                        target = registry.unique_target(exports, f"{project_id}-{count}", suffix)
                        _move(source, target)
                    except OSError as e:
                        raise Failed("The export could not be moved into Clips Kitty's folder.") from e
                    if not unchanged:
                        entry = {"file": name, "at": registry.now(), "result": "kept_beside",
                                 "path": str(target), "seconds": seconds, "clip_id": int(row["id"])}
                        _record(data_dir, project_id, entry)
                        publish({"type": "editor_export", "project_id": project_id, "result": "kept_beside",
                                 "path": str(target)})
                        return _answer(entry, again=False)
                    old = Path(row["path"]) if row["path"] else None
                    try:
                        from core.state import _now

                        db.set_clip(int(row["id"]), path=str(target), edited_at=_now())
                    except Exception as e:
                        # The clip is as it was; the export goes back where the app can find it again.
                        try:
                            _move(target, source)
                        except OSError:
                            pass
                        raise Failed("The export could not be saved as this clip's video.") from e
                    # Only now, with the clip on its new file, does the old one go.
                    _discard_old(db, data_dir, old, target, int(row["id"]))
                    entry = {"file": name, "at": registry.now(), "result": "replaced_clip", "path": str(target),
                             "seconds": seconds, "clip_id": int(row["id"])}
                    followed = follow_captions(db, row, manifest)
                    if followed is not None:
                        entry["captions"] = followed

                    def change(m, target=target):
                        m["clip"]["file"] = str(target)
                        m["clip"]["file_stamp"] = registry.stamp(target)
                        m.setdefault("exports", []).append(entry)

                    registry.update(data_dir, project_id, change)
                    publish({"type": "clip_file_replaced", "clip_id": int(row["id"]), "project_id": project_id})
                    publish({"type": "queue"})
                    return _answer(entry, again=False)
            finally:
                db.close()

    # No clip to re-point: a whole video, a file from the PC, or a clip that was deleted.
    where = export_folder(folder)
    if where is None:
        downloads = downloads_folder()
        where = downloads if downloads is not None and downloads.is_dir() else exports
    # The name the person typed in the editor's Export sheet is kept (it
    # starts as "<name> - <date>"); the project's plain name, as an older
    # editor started the sheet with, gets the date added.
    plain = registry.safe_name(f"{manifest.get('export_name') or manifest.get('name') or 'Video'}", "Video")
    typed = registry.safe_name(Path(name).stem, "")
    stem = typed if typed and typed != plain else f"{plain} - {date.today().isoformat()}"
    if language:
        from multilingual.languages import english_name

        tongue = english_name(language)
        stem = typed if typed and typed != plain and typed.endswith(f"({tongue})") else f"{typed or plain} ({tongue})"
    try:
        where.mkdir(parents=True, exist_ok=True)
        target = registry.unique_target(where, stem, suffix)
        _move(source, target)
    except OSError as e:
        raise Failed("The export could not be moved to your export folder.") from e
    entry = {"file": name, "at": registry.now(), "result": "saved_to_folder", "path": str(target),
             "seconds": seconds}
    _record(data_dir, project_id, entry)
    publish({"type": "editor_export", "project_id": project_id, "result": "saved_to_folder", "path": str(target)})
    return _answer(entry, again=False)


def _record(data_dir: Path, project_id: str, entry: dict) -> None:
    registry.update(data_dir, project_id, lambda m: m.setdefault("exports", []).append(entry))


def downloads_folder() -> Path | None:
    """The Downloads folder as Windows knows it (the Downloads known folder,
    which the person may have moved): the folder the app's own Export
    buttons use when Settings has none (Electron's app.getPath("downloads"),
    ui/src/renderer/src/lib/exportFolder.ts). Elsewhere, or when Windows
    can't say, ~/Downloads. None when there is no home folder either."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid as _uuid
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            raw = _uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le      # FOLDERID_Downloads
            guid = _GUID.from_buffer_copy(raw)
            found = ctypes.c_wchar_p()
            shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) == 0:
                try:
                    if found.value:
                        return Path(found.value)
                finally:
                    ole32.CoTaskMemFree(found)
        except (OSError, AttributeError, ValueError):
            pass
    try:
        return Path.home() / "Downloads"
    except (OSError, RuntimeError, KeyError):
        return None


def _discard_old(db, data_dir: Path, old: Path | None, target: Path, clip_id: int) -> None:
    """The clip's old file, when it is inside the data folder, is not the
    new one, and no other clip row still plays it and no run holds it
    (core.remake.file_in_use, as DELETE /clips/{clip_id} asks: two AI
    windows with the same whole-second start and end share one
    clip_<start>-<end>.mp4); and the Quick edit preview of the clip. Called
    with the clip's lock and the database held, after the re-point. Never
    raises."""
    from core.paths import discard, within

    try:
        if old is not None and not registry.same_path(old, target) and old.is_file() and within(data_dir, old):
            from core.remake import file_in_use

            if not file_in_use(db, old):
                discard(old)
        discard(data_dir / "previews" / f"clip_{clip_id}.mp4")
    except Exception:
        pass        # kept: a file left behind is reclaimed later, one deleted can't be had back


def recover(data_dir, project_id: str, *, open_db, publish=None, clip_lock=None, folder=None) -> list[dict]:
    """Exports left in out/ (the app closed between an export's end and its
    report): each taken in, oldest first, into `folder` (the person's
    export folder) when there is no clip to re-point. The editor's
    unfinished work files are never taken, and a file changed in the last
    seconds is left for later. Never raises; what could not be taken in
    stays in out/."""
    try:
        out = registry.folder(data_dir, project_id) / "out"
        files = [f for f in out.iterdir()
                 if f.is_file() and not f.is_symlink() and not unfinished(f.name)
                 and f.name.lower().endswith(registry.EXPORT_SUFFIXES)]
    except (OSError, registry.NotFound):
        return []
    now = time.time()
    waiting = []
    for f in files:
        try:
            changed = f.stat().st_mtime
        except OSError:
            continue
        if now - changed >= SETTLE:
            waiting.append((changed, f.name))
    answers = []
    for _, name in sorted(waiting):
        try:
            answers.append(take_in(data_dir, project_id, name, folder=folder, open_db=open_db,
                                   publish=publish, clip_lock=clip_lock))
        except Exception:
            continue
    return answers
