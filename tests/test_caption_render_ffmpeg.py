"""Captions burned for real by FFmpeg's libass, in the editor's presets.

Needs FFmpeg with libass (the subtitles filter); skips without it. Nothing is
downloaded: the picture is FFmpeg's own solid colour, the caption a short line
of made-up words, burned exactly the way a render burns it (the ASS file's
folder as the working folder, video/fonts.py's subtitles filter).

- The preset's font file is the one libass loads, not Arial.
- The caption's block is centred where the editor puts it.
- With a safe zone, a word or a run libass cannot break is drawn smaller and
  stays clear of the app's buttons.
"""

import subprocess
from pathlib import Path

import pytest

from core import binaries
from core.models import ClipCandidate, Segment
from video.captions import build_captions
from video.fonts import subtitles_filter

W, H, FPS = 540, 960, 50          # a 9:16 frame at half size; a frame every 20 ms
ROWS = (560, 820)                 # the band the bottom caption sits in at this size
WORDS = [{"start": 0.0, "end": 0.15, "word": "big"}, {"start": 0.15, "end": 0.3, "word": "fun"},
         {"start": 0.3, "end": 0.6, "word": "now"}]


def _ffmpeg_with_libass() -> str:
    if binaries.missing():
        pytest.skip("needs FFmpeg")
    exe = binaries.ffmpeg()
    try:
        filters = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True,
                                 timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("FFmpeg could not be run")
    if " subtitles " not in filters:
        pytest.skip("this FFmpeg has no libass")
    return exe


def _captions(folder: Path, style: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    segments = [Segment(start=0.0, end=0.6, text="big fun now", words=WORDS)]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=0.6, score=0), folder / "clip.ass",
                         style=style, canvas=(W, H))
    assert ass is not None
    return ass


def _burn(exe: str, ass: Path, pix: str = "gray", seconds: float = 0.3, loglevel: str = "error"):
    """Frames of a solid picture with the captions burned, as the render burns them."""
    cmd = [exe, "-hide_banner", "-loglevel", loglevel, "-f", "lavfi",
           "-i", f"color=c=0x203050:s={W}x{H}:r={FPS}:d={seconds}",
           "-vf", subtitles_filter(ass), "-f", "rawvideo", "-pix_fmt", pix, "-"]
    out = subprocess.run(cmd, capture_output=True, cwd=ass.parent, timeout=120)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-800:]
    size = W * H * (3 if pix == "rgb24" else 1)
    frames = [out.stdout[i:i + size] for i in range(0, len(out.stdout), size)]
    return frames, out.stderr.decode("utf-8", "replace")


def _area(frame: bytes):
    """(left, top, right, bottom, count) of the caption's bright pixels in a
    grey frame, sampled every other pixel; None when nothing is bright."""
    xs, ys = [], []
    for y in range(ROWS[0], ROWS[1], 2):
        row = frame[y * W:(y + 1) * W]
        for x in range(0, W, 2):
            if row[x] > 180:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys), len(xs)) if xs else None


FULL_W, FULL_H = 1080, 1920


def _full(folder: Path, style: dict, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    segments = [Segment(start=0.0, end=1.0, text=text, words=[])]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=1.0, score=0), folder / "clip.ass",
                         style=style, lines=[{"start": 0.0, "end": 1.0, "text": text}], canvas=(FULL_W, FULL_H))
    assert ass is not None
    return ass


def _full_frames(exe: str, ass: Path, seconds: float, fps: int = 50) -> list[bytes]:
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"color=c=0x203050:s={FULL_W}x{FULL_H}:r={fps}:d={seconds}",
           "-vf", subtitles_filter(ass), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out = subprocess.run(cmd, capture_output=True, cwd=ass.parent, timeout=120)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-800:]
    size = FULL_W * FULL_H
    return [out.stdout[i:i + size] for i in range(0, len(out.stdout), size)]


def _ink_right(frame: bytes) -> int:
    """The right-most pixel of the caption (letters, outline and shadow): any
    pixel that is not the background colour."""
    bg = frame[0]
    marked = frame.translate(bytes(1 if abs(v - bg) > 24 else 0 for v in range(256)))
    right = -1
    for y in range(FULL_H):
        x = marked.rfind(b"\x01", y * FULL_W, (y + 1) * FULL_W)
        if x >= 0:
            right = max(right, x - y * FULL_W)
    return right


def _full_rows(frame: bytes) -> int:
    """How many separate bands of bright (text) rows a full-size grey frame has."""
    bands, before = 0, False
    for y in range(FULL_H):
        bright = max(frame[y * FULL_W:(y + 1) * FULL_W]) > 200
        if bright and not before:
            bands += 1
        before = bright
    return bands


@pytest.fixture(scope="module")
def exe():
    return _ffmpeg_with_libass()


def test_the_presets_font_is_the_font_libass_burns(exe, tmp_path):
    ass = _captions(tmp_path, {"preset": "concat.impact"})
    assert subtitles_filter(ass) == "subtitles=clip.ass:fontsdir=fonts"
    _, log = _burn(exe, ass, seconds=0.1, loglevel="verbose")
    picked = [line for line in log.splitlines() if "fontselect:" in line]
    assert any("Loading font file" in line and "Anton-Regular.ttf" in line for line in log.splitlines()), log[-2000:]
    assert picked and all("(Anton," in line and "-> Anton-Regular" in line for line in picked), picked
    assert not any("Arial" in line for line in picked), picked


def _rows_with_ink(frame: bytes) -> tuple[int, int]:
    bg = frame[0]
    lit = [y for y in range(FULL_H) if any(abs(v - bg) > 24 for v in frame[y * FULL_W:(y + 1) * FULL_W:4])]
    return lit[0], lit[-1]


@pytest.mark.parametrize(("position", "centre"), [("bottom", 0.85), ("middle", 0.5), ("top", 0.15)])
def test_the_caption_is_centred_where_the_editor_puts_it(exe, tmp_path, position, centre):
    ass = _full(tmp_path, {"preset": "concat.block", "position": position, "safe_zone": "off"}, "mid line")
    top, bottom = _rows_with_ink(_full_frames(exe, ass, 0.1, fps=10)[0])
    assert abs((top + bottom) / 2 - centre * FULL_H) <= 0.01 * FULL_H, (top, bottom)


def test_a_word_too_wide_for_the_safe_area_stays_clear_of_the_buttons(exe, tmp_path):
    """CONGRATULATIONS at Large is wider than the space between TikTok's
    margins; libass cannot break it, so the caption is drawn smaller."""
    from video import captions

    ass = _full(tmp_path, {"preset": "concat.impact", "size": "large", "safe_zone": "tiktok"},
                "congratulations everyone")
    assert "\\fscx" in ass.read_text(encoding="utf-8")
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    rights = [_ink_right(f) for f in _full_frames(exe, ass, 0.1, fps=10)]
    assert rights and all(0 < r < column for r in rights), (column, rights)


@pytest.mark.parametrize(("language", "text"), [("zh", "这个真的太厉害了吧朋友们"), ("ja", "これは本当にすごいですね皆さん")])
def test_a_caption_without_spaces_stays_clear_of_the_buttons(exe, tmp_path, language, text):
    from video import captions

    segments = [Segment(start=0.0, end=1.0, text=text, words=[])]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=1.0, score=0), tmp_path / "clip.ass",
                         style={"size": "large", "safe_zone": "tiktok"},
                         lines=[{"start": 0.0, "end": 1.0, "text": text}], canvas=(FULL_W, FULL_H),
                         language=language)
    assert "\\fscx" in ass.read_text(encoding="utf-8")
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    shown = [r for r in (_ink_right(f) for f in _full_frames(exe, ass, 0.1, fps=10)) if r >= 0]
    assert shown and all(r < column for r in shown), (column, shown)


@pytest.mark.parametrize("joiner", ["no-break space", "narrow no-break space"])
def test_words_joined_by_a_no_break_space_stay_clear_of_the_buttons(exe, tmp_path, joiner):
    """libass never breaks at a no-break space: the two words would be one
    run into TikTok's column. They are spaced plainly and wrap."""
    from video import captions

    gap = chr(0xA0) if joiner == "no-break space" else chr(0x202F)
    style = {"preset": "concat.bold-caption", "size": "large", "safe_zone": "tiktok"}
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    frames = _full_frames(exe, _full(tmp_path, style, f"ABSOLUTELY{gap}UNREAL"), 0.1, fps=10)
    rights = [_ink_right(f) for f in frames]
    assert rights and all(0 < r < column for r in rights), (column, rights)
    assert _full_rows(frames[-1]) == 2
