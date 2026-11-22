"""FFmpeg 9 made every clip fail, and the app said nothing went wrong (#281).

FFmpeg 9 removed -vsync. Clips Kitty passed it on every cut, so an FFmpeg 9
refused every clip with "Unrecognized option 'vsync'", and the run still
finished as "done" with an outcome that read "No clips from this video, and
nothing went wrong". The shipped FFmpeg (8.1.2) was fine; people running from
source with their own newer FFmpeg were not, and scripts/fetch_ffmpeg.py
followed a moving link that would have shipped FFmpeg 9 in the next build.

These cover each part of the fix: the commands use -fps_mode, a refused
option is named in plain words, a run whose renders all failed says so, and
the FFmpeg fetch is pinned by version and hash.
"""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.models import ClipCandidate
from core.outcome import explain_no_clips, note_render_failures, renders_failed_message

ROOT = Path(__file__).resolve().parent.parent

# What FFmpeg 9.1 prints for a command with -vsync in it (exit code 8).
FFMPEG9_REFUSAL = (
    "ffmpeg cut failed:\n"
    "ffmpeg version 9.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers\n"
    "  built with gcc 15.2.0 (Rev8, Built by MSYS2 project)\n"
    "Unrecognized option 'vsync'.\n"
    "Error splitting the argument list: Option not found"
)


# -- the commands -------------------------------------------------------------

def _tracked_python_files() -> list[str]:
    """The checkout's own .py files, as git tracks them: not a developer's
    untracked scratch files, venvs or node_modules. Without git (a source
    archive), every .py outside those folders."""
    git = shutil.which("git")
    if git:
        done = subprocess.run([git, "ls-files", "-z", "--", "*.py"], cwd=ROOT, capture_output=True)
        if done.returncode == 0 and done.stdout:
            return [p for p in done.stdout.decode("utf-8").split("\0") if p]
    skip = {"tests", "ui", "vendor", "node_modules", "data", ".venv", "venv", ".git"}
    return [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
            if not skip & set(p.relative_to(ROOT).parts)]


def test_no_engine_code_passes_vsync():
    """Any -vsync left in the engine is the same failure on FFmpeg 9."""
    offenders = []
    for rel in _tracked_python_files():
        if rel.startswith(("tests/", "ui/", "vendor/")):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"""["']-vsync["']""", text):
            offenders.append(rel)
    assert offenders == [], f"-vsync is gone in FFmpeg 9; use -fps_mode: {offenders}"


def test_cut_clip_forces_a_constant_frame_rate_with_fps_mode(tmp_path):
    from video import cutter
    from video.encoding import CPU_ARGS

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(cutter, "ffmpeg", return_value="ffmpeg"), \
            patch.object(cutter, "video_encoder_args", return_value=list(CPU_ARGS)), \
            patch.object(cutter, "hwaccel_input_args", return_value=[]), \
            patch.object(cutter, "using_hardware_encoder", return_value=False), \
            patch.object(cutter.subprocess, "run", side_effect=fake_run):
        cutter.cut_clip(tmp_path / "in.mp4", ClipCandidate(start=10.0, end=40.0, score=80),
                        tmp_path / "out.mp4")

    cmd = seen["cmd"]
    assert "-vsync" not in cmd
    i = cmd.index("-fps_mode")
    assert cmd[i + 1] == "cfr"
    assert i > cmd.index("-i"), "-fps_mode is an output option: it goes after the input"


def test_the_frame_sampler_keeps_frames_with_fps_mode_passthrough():
    pytest.importorskip("numpy", reason="the frame sampler imports numpy, which CI does not install")
    from video import encoding

    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            self.stdout = self
            self.returncode = 0

        def read(self, size):
            return b""          # no frames: the generator ends at once

        def close(self):
            pass

        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self):
            return 0

    with patch("core.binaries.ffmpeg", return_value="ffmpeg"), \
            patch("subprocess.Popen", FakeProc):
        assert list(encoding.sampled_frames("clip.mp4", 4, 64, 36, hwaccel=False)) == []

    cmd = seen["cmd"]
    assert "-vsync" not in cmd
    i = cmd.index("-fps_mode")
    assert cmd[i + 1] == "passthrough"
    assert i > cmd.index("-i")


# -- the reason a render failed -------------------------------------------------

def _reason(text):
    pytest.importorskip("numpy", reason="core.pipeline imports numpy, which CI does not install")
    from core.pipeline import _render_failure_reason

    return _render_failure_reason(RuntimeError(text))


def test_an_unknown_option_is_named_with_what_to_do():
    reason = _reason(FFMPEG9_REFUSAL)

    assert "not supported" in reason
    assert "-vsync" in reason
    assert "scripts/fetch_ffmpeg.py" in reason
    assert "Error splitting" not in reason, "FFmpeg's last line names neither the option nor a fix"
    assert "\n" not in reason and len(reason) <= 300


def test_the_argument_list_refused_alone_still_says_the_ffmpeg_is_unsupported():
    reason = _reason("ffmpeg cut failed:\nError splitting the argument list: Option not found")

    assert "not supported" in reason
    assert "\n" not in reason and len(reason) <= 300


# What the shipped FFmpeg (8.1.2) prints for a filter option it does not know
# (-vf scale=bogus=1); 9.1 prints the same.
FILTER_OPTION_REFUSAL = (
    "ffmpeg cut failed:\n"
    "Input #0, lavfi, from 'color=c=black:s=64x64:d=0.1':\n"
    "  Duration: N/A, start: 0.000000, bitrate: N/A\n"
    "[fc#-1 @ 0000025053ee1000] Error applying option 'bogus' to filter 'scale': Option not found\n"
    "Error opening output file -.\n"
    "Error opening output files: Option not found"
)


def test_a_filter_refusing_an_option_is_not_blamed_on_the_ffmpeg_version():
    """"Option not found" is also how every FFmpeg, the shipped one included,
    refuses a bad filter option: telling that person to use the shipped
    FFmpeg would be wrong. The reason names the option and the filter."""
    reason = _reason(FILTER_OPTION_REFUSAL)

    assert "not supported" not in reason and "fetch_ffmpeg" not in reason
    assert reason == "Error applying option 'bogus' to filter 'scale': Option not found"


# -- the outcome after rendering --------------------------------------------------

BEFORE = {"clips": 3, "candidates": 82, "best_score": 91, "min_score": 55,
          "rejected": {"below_min_score": 79}, "measured": 0, "nothing_detected": 0, "cause": None}


def test_every_render_failing_names_the_cause():
    reason = "this FFmpeg version is not supported"
    out = note_render_failures(BEFORE, [reason, reason, "something else"], made=0)

    assert out["cause"] == "render_failed"
    assert out["render_failed"] == 3
    assert out["render_error"] == reason, "the commonest reason, not the last"
    assert out["clips"] == 3 and out["candidates"] == 82, "the numbers stay as they were"
    assert BEFORE["cause"] is None, "the outcome passed in is not changed"


def test_some_clips_made_counts_the_failures_without_a_cause():
    out = note_render_failures(BEFORE, ["boom"], made=2)

    assert out["render_failed"] == 1
    assert out["render_error"] == "boom"
    assert out["cause"] is None


def test_nothing_failing_leaves_the_outcome_exactly_as_it_was():
    out = note_render_failures(BEFORE, [], made=0)

    assert out is BEFORE
    assert "render_failed" not in out and "render_error" not in out


def test_a_tie_keeps_the_first_reason_seen():
    out = note_render_failures(BEFORE, ["first", "second"], made=0)

    assert out["render_error"] == "first"


def test_the_log_line_says_the_clips_failed():
    out = note_render_failures(BEFORE, ["this FFmpeg version is not supported"] * 3, made=0)
    line = explain_no_clips(out)

    assert line.startswith("No clips: 3 clip(s) failed while being made")
    assert "this FFmpeg version is not supported" in line
    assert "min_score" not in line, "the minimum score has nothing to do with it"


def test_the_error_counts_the_clips_in_plain_words():
    one = note_render_failures(BEFORE, ["boom"], made=0)
    many = note_render_failures(BEFORE, ["boom"] * 3, made=0)

    assert renders_failed_message(one) == "The clip failed while being made: boom"
    assert renders_failed_message(many) == "All 3 clips failed while being made: boom"


def test_a_watched_channel_says_why_there_was_nothing_to_publish():
    pytest.importorskip("fastapi", reason="server.automation imports fastapi, which CI does not install")
    from server.automation import _no_clips_error

    out = note_render_failures(BEFORE, ["this FFmpeg version is not supported"], made=0)
    message = _no_clips_error(out)

    assert "failed while being made" in message
    assert "this FFmpeg version is not supported" in message


# -- the FFmpeg the build ships ---------------------------------------------------

def _fetch_ffmpeg():
    spec = importlib.util.spec_from_file_location("fetch_ffmpeg", ROOT / "scripts" / "fetch_ffmpeg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ffmpeg_fetch_is_pinned_to_one_release():
    ff = _fetch_ffmpeg()

    assert "release-essentials" not in ff.BUILD_URL, "a moving link would ship whatever is newest"
    assert re.search(r"/\d+\.\d+(\.\d+)?/ffmpeg-\d+\.\d+(\.\d+)?-essentials_build\.zip$", ff.BUILD_URL)
    assert ff.FFMPEG_VERSION in ff.BUILD_URL
    assert re.fullmatch(r"[0-9a-f]{64}", ff.BUILD_SHA256)
    assert ff.FFMPEG_VERSION in ff.NOTICE and ff.BUILD_URL in ff.NOTICE, "the GPL source offer names the build"


def test_the_ffmpeg_fetch_refuses_a_download_that_is_not_the_pinned_build():
    ff = _fetch_ffmpeg()
    blob = b"not the archive"

    problem = ff.check_archive(blob)
    assert problem and "SHA-256" in problem and "Nothing was extracted" in problem

    import hashlib

    assert ff.check_archive(blob, hashlib.sha256(blob).hexdigest()) is None


def test_the_ffmpeg_fetch_extracts_nothing_from_a_download_that_is_not_the_pinned_build(tmp_path, monkeypatch):
    ff = _fetch_ffmpeg()
    dest = tmp_path / "vendor" / "ffmpeg"
    monkeypatch.setattr(ff, "DEST", dest)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"PK not the pinned archive"

    monkeypatch.setattr(ff.urllib.request, "urlopen", lambda *_a, **_k: Response())

    assert ff.fetch(force=True) == 1
    assert list(dest.iterdir()) == [], "no binary and no notice from an archive that failed the check"
