"""What the project operations ask the rest of Clips Kitty (ops.Env), for
real: the caption fonts, the colour filters, the Gaming layouts, the export
presets and encoders, and which media a project may name.

Every import of another part of the app is inside the function that needs
it, so loading the project package loads none of them.
"""

from __future__ import annotations

import re
from pathlib import Path

from video_editor.project import model
from video_editor.project.ops import Env

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
# Fonts every Windows has, for text items and watermarks (captions use the
# editor's presets and fonts only).
_WINDOWS_FONTS = ("Arial", "Arial Black", "Impact", "Verdana", "Tahoma", "Trebuchet MS", "Segoe UI",
                  "Georgia", "Comic Sans MS", "Courier New")


class AppEnv(Env):
    """`db` is the app's StateDB (or anything with its `.conn`), `data_dir`
    its data folder. `probe(path)` reads a video file's size, frame rate and
    length (video.export_presets.probe by default) and `encoder_for(codec,
    pref)` says what this PC encodes with (video.encoding.encoder_for); both
    can be replaced, and a test replaces them."""

    def __init__(self, db=None, data_dir=None, probe=None, encoder_for=None):
        self.db = db
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self._probe = probe
        self._encoder_for = encoder_for

    # ---- media ----

    def media(self, ref: dict) -> dict:
        if "library" in ref:
            return self._library(ref["library"])
        if "video_id" in ref:
            return self._video(ref["video_id"])
        if "asset" in ref:
            return self._asset(ref["asset"])
        raise ValueError("names no media")

    def _library(self, item_id: str) -> dict:
        from video import audio_library

        entry = audio_library.entry(item_id)
        if entry is None:
            raise ValueError("is not one of the app's free tracks or sound effects")
        return {"kind": "audio", "source": {"type": "library", "id": entry["id"]},
                "duration_us": model.us(entry.get("seconds") or 0), "width": 0, "height": 0, "fps": None,
                "has_audio": True, "name": str(entry.get("title") or entry["id"])}

    def _facts(self, path: Path | None) -> dict:
        """What a probe says about a video file; {} when there is no file or
        it can't be read (the media is added with what is known)."""
        if path is None or not path.is_file():
            return {}
        try:
            if self._probe is not None:
                source = self._probe(path)
            else:
                from video import export_presets

                source = export_presets.probe(path)
        except Exception:
            return {}
        out = {"width": int(getattr(source, "width", 0) or 0), "height": int(getattr(source, "height", 0) or 0),
               "duration_us": model.us(getattr(source, "duration", 0) or 0)}
        try:
            out["fps"] = model.fps_of(round(float(getattr(source, "fps", 0) or 0), 3))
        except (model.ModelError, TypeError, ValueError):
            out["fps"] = None
        return out

    def _video(self, video_id: str) -> dict:
        if self.db is None:
            raise ValueError("can't be looked up here")
        row = self.db.conn.execute(
            "SELECT video_id, title, duration FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if row is None:
            raise ValueError("is not a video in the library")
        path = self.data_dir / "downloads" / f"{row['video_id']}.mp4" if self.data_dir is not None else None
        facts = self._facts(path)
        return {"kind": "video", "source": {"type": "video", "video_id": row["video_id"]},
                "duration_us": facts.get("duration_us") or model.us(row["duration"] or 0),
                "width": facts.get("width", 0), "height": facts.get("height", 0), "fps": facts.get("fps"),
                "has_audio": True, "name": str(row["title"] or "")}

    def video_facts(self, video_id: str) -> dict:
        """What is known about a library video, as importer.import_clip takes
        it; {} when it isn't in the library."""
        try:
            return self._video(video_id)
        except ValueError:
            return {}

    def _asset(self, sha256: str) -> dict:
        if self.db is None or not _SHA256.fullmatch(sha256):
            raise ValueError("is not the sha256 of an imported file")
        row = self.db.conn.execute("SELECT * FROM media_assets WHERE sha256 = ?", (sha256,)).fetchone()
        if row is None:
            raise ValueError("is not an imported file")
        fps = {"num": row["fps_num"], "den": row["fps_den"]} if row["fps_num"] and row["fps_den"] else None
        return {"kind": row["kind"], "source": {"type": "asset", "sha256": row["sha256"]},
                "duration_us": int(row["duration_us"] or 0), "width": int(row["width"] or 0),
                "height": int(row["height"] or 0), "fps": fps, "has_audio": bool(row["has_audio"]),
                "name": str(row["name"] or "")}

    # ---- looks ----

    def filter_ok(self, name: str) -> bool:
        from video import filters

        return filters.is_valid(name)

    def gaming(self, settings: dict) -> dict:
        from core import modes

        return modes.clean_gaming(settings)

    def font(self, name: str) -> str | None:
        """A text item's or watermark's font: one of the app's own (the
        editor's fonts, video/fonts.py) or one every Windows has."""
        from video import fonts

        wanted = str(name or "").strip().casefold()
        known = (*_WINDOWS_FONTS, *fonts.families(), *fonts.typographic_families())
        return next((f for f in known if f.casefold() == wanted), None) if wanted else None

    def caption_style(self, style: dict) -> dict:
        """A caption style with only the keys the captions read
        (video/caption_presets.py DEFAULT, and a dragged x and y), each
        checked."""
        from video import caption_presets as cp

        out = {}
        for key, value in style.items():
            if key not in cp.DEFAULT and key not in cp.POSITION_KEYS:
                raise ValueError(f"has no {str(key)[:40]!r}. It takes {', '.join((*cp.DEFAULT, *cp.POSITION_KEYS))}")
            if key in cp.POSITION_KEYS:
                if value is None:
                    continue            # back to the named place
                if cp.fraction(value) is None or not 0 <= value <= 1:
                    raise ValueError(f"{key} must be a number from 0 to 1 (a share of the frame)")
                value = cp.fraction(value)
            elif key == "preset":
                if value not in cp.ids():
                    raise ValueError(f"preset {str(value)[:60]!r} is not one of the editor's text presets")
            elif key == "size":
                if value not in cp.SIZES:
                    raise ValueError("size must be small, medium or large")
            elif key == "position":
                if value not in cp.PLACES:
                    raise ValueError("position must be bottom, middle or top")
            elif key == "safe_zone":
                if value not in cp.SAFE_ZONE_CHOICES:
                    raise ValueError(f"safe_zone must be one of {', '.join(cp.SAFE_ZONE_CHOICES)}")
            elif key == "second_speaker":
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif key == "words_per_caption":
                if type(value) is not int or not 1 <= value <= 10:
                    raise ValueError(f"{key} must be a whole number from 1 to 10")
            elif not isinstance(value, str) or not _COLOUR.fullmatch(value):
                raise ValueError(f"{key} must be a colour as #RRGGBB")
            out[key] = value
        return out

    # ---- export ----

    def export_preset(self, raw) -> dict | None:
        from video import export_presets

        try:
            preset = export_presets.normalize(raw)
        except export_presets.PresetError as e:
            raise ValueError(str(e)) from e
        if preset["kind"] == "original":
            return None
        if self._encoder_for is not None:
            encoder_for = self._encoder_for
        else:
            from video import encoding

            encoder_for = encoding.encoder_for
        if encoder_for(preset["codec"], preset["encoder"]) is None:
            codec = export_presets.CODEC_NAMES.get(preset["codec"], preset["codec"])
            how = {"cpu": " on the processor", "gpu": " on the graphics card"}.get(preset["encoder"], "")
            raise ValueError(f"this PC can't encode {codec}{how}. Choose another codec")
        return preset
