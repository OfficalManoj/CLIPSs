"""Editor suite FL2, the engine's part: a project's record read while it is
being replaced (F2, kitty_editor/registry.py), and the base video of a clip
whose source is already 9:16 made without the full render (F1,
kitty_editor/fastbase.py).

The registry tests need nothing but the standard library. The base tests
make small portrait videos with FFmpeg and skip without numpy, OpenCV or
FFmpeg, as tests/test_kitty_editor_fastbase.py does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from kitty_editor import registry

# ---- F2: a record being replaced is read, not called damaged ------------------


def _manifest(project_id: str, **more) -> dict:
    return {"schema": registry.SCHEMA, "id": project_id, "name": "A clip", "kind": "clip", "state": "ready",
            "progress": 1.0, "message": "", **more}


def test_a_read_that_meets_the_file_being_replaced_tries_again(tmp_path, monkeypatch):
    """Windows refuses a read for an instant while os.replace swaps the file
    (a sharing violation, PermissionError): the read is tried again for up to
    half a second instead of saying the record can't be read."""
    project_id = registry.new_id()
    registry.save(tmp_path, _manifest(project_id))
    real = Path.read_text
    refused = {"n": 3}

    def read_text(self, *args, **kwargs):
        if self.name == registry.MANIFEST and refused["n"] > 0:
            refused["n"] -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    loaded = registry.load(tmp_path, project_id)
    assert refused["n"] == 0 and not loaded.get("damaged") and loaded["state"] == "ready"


def test_a_record_that_stays_unreadable_is_still_listed_as_damaged(tmp_path, monkeypatch):
    project_id = registry.new_id()
    registry.save(tmp_path, _manifest(project_id))
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == registry.MANIFEST:
            raise PermissionError(13, "held")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    began = time.monotonic()
    loaded = registry.load(tmp_path, project_id)
    waited = time.monotonic() - began
    assert loaded["damaged"] and loaded["message"] == "This project's record can't be read."
    assert registry.READ_PATIENCE - 0.05 <= waited < registry.READ_PATIENCE + 1.0


def test_a_record_that_is_not_json_is_not_waited_for(tmp_path):
    project_id = registry.new_id()
    registry.folder(tmp_path, project_id).mkdir(parents=True)
    (registry.folder(tmp_path, project_id) / registry.MANIFEST).write_text("{not json", encoding="utf-8")
    began = time.monotonic()
    assert registry.load(tmp_path, project_id)["damaged"]
    assert time.monotonic() - began < registry.READ_PATIENCE


def test_reads_beside_a_writer_never_see_a_damaged_record(tmp_path):
    """The race the K2 click-through held: the build thread writes the record
    (progress) while the page asks for it. A second each of writes and reads
    in two threads: every read is the project."""
    project_id = registry.new_id()
    registry.save(tmp_path, _manifest(project_id))
    stop = threading.Event()
    bad: list = []

    def write() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            registry.update(tmp_path, project_id, lambda m, n=n: m.update(progress=(n % 100) / 100))

    def read() -> None:
        while not stop.is_set():
            got = registry.load(tmp_path, project_id)
            if got.get("damaged"):
                bad.append(got["message"])
            for listed in registry.list_all(tmp_path):
                if listed.get("damaged"):
                    bad.append(listed["message"])

    threads = [threading.Thread(target=write), threading.Thread(target=read), threading.Thread(target=read)]
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(timeout=10)
    assert bad == []


# ---- F1: a full-frame 9:16 clip's base --------------------------------------


def _ffmpeg() -> str:
    try:
        from core.binaries import ffmpeg

        exe = ffmpeg()
    except Exception:
        exe = None
    if not exe or not (Path(exe).is_file() or shutil.which(exe)):
        pytest.skip("FFmpeg is not on this machine")
    return exe


def _run(*args) -> None:
    done = subprocess.run([_ffmpeg(), "-v", "error", "-y", *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]


PW, PH, RATE = 360, 640, 30


@pytest.fixture(scope="module")
def portrait(tmp_path_factory) -> Path:
    """4 s of a 9:16 source full of detail, with a tone."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    out = tmp_path_factory.mktemp("fl2") / "portrait.mp4"
    _run("-f", "lavfi", "-i", f"mandelbrot=s={PW}x{PH}:rate={RATE}", "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out))
    return out


def _portrait_clip(source: Path, out: Path, layout: str) -> Path:
    """A clip of the portrait source as the standard render lays it out: the
    whole frame filling the screen, or the face-safe layout (the whole frame
    at full width under the apps' top bar over a blurred copy), with a white
    bar standing in for a caption."""
    if layout == "full":
        picture = "scale=1080:1920:flags=lanczos,setsar=1"
    else:
        picture = ("split=2[a][b];[a]scale=1080:1920,scale=135:240,gblur=sigma=12,"
                   "scale=1080:1920:flags=bilinear[bg];[b]scale=1080:-2:flags=lanczos[fg];"
                   f"[bg][fg]overlay=0:{220},setsar=1")
    graph = (f"[0:v]trim=duration=3.4,setpts=PTS-STARTPTS,{picture},"
             "drawbox=x=120:y=1250:w=840:h=160:color=white:t=fill[v]")
    _run("-i", str(source), "-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))
    return out


def test_a_source_is_full_frame_only_when_the_crop_cannot_move():
    from kitty_editor import fastbase

    assert fastbase.full_frame(1080, 1920) and fastbase.full_frame(720, 1280) and fastbase.full_frame(360, 640)
    assert not fastbase.full_frame(1920, 1080) and not fastbase.full_frame(1088, 1920)
    assert not fastbase.full_frame(1000, 1920)          # narrower than 9:16: letterboxed, not a crop


def test_vertical_live_is_told_from_the_clip_or_the_job():
    from kitty_editor import fastbase

    assert fastbase.live_layout({"vertical_live": True}, {"clips": {}})
    assert fastbase.live_layout({}, {"clips": {"vertical_live": True}})
    assert not fastbase.live_layout({}, {"clips": {}})
    assert not fastbase.live_layout({"vertical_live": True, "profile": "16:9"}, {"clips": {}})


def test_a_vertical_live_picture_is_the_whole_frame_with_the_clips_colour():
    from kitty_editor import fastbase

    assert fastbase.live_graph("30/1", 1080, 1920) == "[0:v]fps=30/1[v]"
    graph = fastbase.live_graph("60/1", 720, 1280, "eq=saturation=1.35")
    assert graph.startswith("[0:v]fps=60/1,scale=1080:1920:force_original_aspect_ratio=decrease")
    assert graph.endswith(",eq=saturation=1.35[v]") and "crop" not in graph


def test_the_graph_without_a_crop_script_keeps_the_crop_still():
    from kitty_editor import fastbase

    layout = fastbase.Layout("full", 1080, 1920, 0, 1920)
    graph = fastbase.filter_graph(layout, "30/1", 0, None)
    assert "sendcmd" not in graph and "crop@base=w=1080:h=1920:x=0:y=0:exact=1" in graph
    assert "sendcmd=f=crop.cmd," in fastbase.filter_graph(layout, "30/1", 0, "crop.cmd")


def test_a_full_frame_clip_is_told_from_a_face_safe_one(portrait, tmp_path):
    from kitty_editor import fastbase

    full = _portrait_clip(portrait, tmp_path / "full.mp4", "full")
    layout, seen = fastbase.full_frame_layout(portrait, full, 0.0, 3.4, src_w=PW, src_h=PH)
    assert (layout.name, layout.crop_w, layout.crop_h, layout.top, layout.height) == ("full", PW, PH, 0, 1920)
    assert len(seen) == 5 and all(s["full"] < s["face"] for s in seen)
    face = _portrait_clip(portrait, tmp_path / "face.mp4", "face")
    with pytest.raises(fastbase.NotPlain, match="face-safe"):
        fastbase.full_frame_layout(portrait, face, 0.0, 3.4, src_w=PW, src_h=PH)
    other = tmp_path / "other.mp4"
    _run("-f", "lavfi", "-i", "testsrc2=s=1080x1920:rate=30", "-t", "4", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(other))
    with pytest.raises(fastbase.NotPlain):
        fastbase.full_frame_layout(portrait, other, 0.0, 3.4, src_w=PW, src_h=PH)


def test_a_full_frame_clips_base_is_made_without_the_full_render(portrait, tmp_path):
    from kitty_editor import build, fastbase
    from video import encoding

    clip = _portrait_clip(portrait, tmp_path / "clip.mp4", "full")
    row = {"start_s": 0.0, "end_s": 3.0}
    config = {"clips": {}, "tracking": {"sample_fps": 7.5}}
    seen: list = []
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        out = fastbase.make(row, {}, config, portrait, clip, tmp_path / "work", progress=seen.append)
    assert out is not None and out.is_file()
    info = build.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["seconds"] - 3.4) < 0.1
    assert seen == sorted(seen) and 0 < seen[-1] <= 1.0
    framing = json.loads((tmp_path / "work" / "framing.json").read_text(encoding="utf-8"))
    assert framing["layout"] == "full" and framing["identity"] is True
    # A face-safe clip of the same source is left to the full render.
    face = _portrait_clip(portrait, tmp_path / "face.mp4", "face")
    assert fastbase.make(row, {}, config, portrait, face, tmp_path / "work2") is None


def test_a_vertical_live_clips_base_is_its_whole_frame(portrait, tmp_path):
    from kitty_editor import build, fastbase
    from video import encoding

    # A Vertical Live clip's file is never read for its framing: even a clip
    # that is not a crop of the source (here a face-safe one) gives the base.
    clip = _portrait_clip(portrait, tmp_path / "clip.mp4", "face")
    row = {"start_s": 0.0, "end_s": 3.0}
    config = {"clips": {"vertical_live": True}, "tracking": {}}
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        out = fastbase.make(row, {"edit": {"keep": [[0, 1]]}}, config, portrait, clip, tmp_path / "work")
    assert out is not None and build.probe(out)["width"] == 1080
    framing = json.loads((tmp_path / "work" / "framing.json").read_text(encoding="utf-8"))
    assert framing == {"layout": "live"}


def test_a_crop_that_never_moves_is_made_the_fast_way(tmp_path):
    """A centred crop has no crop commands; FFmpeg's sendcmd refuses an empty
    script, so such a clip went to the full render (found in the FL2 desktop
    check on synth_timer_30s's clip)."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from kitty_editor import build, fastbase
    from video import encoding

    source = tmp_path / "wide.mp4"
    _run("-f", "lavfi", "-i", f"mandelbrot=s=640x360:rate={RATE}", "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(source))
    full, _face = fastbase.layouts(640, 360)
    clip = tmp_path / "still.mp4"
    _run("-i", str(source), "-filter_complex",
         f"[0:v]trim=duration=3.4,setpts=PTS-STARTPTS,crop={full.crop_w}:360:x=219:y=0,"
         "scale=1080:1920:flags=lanczos,setsar=1,drawbox=x=120:y=1250:w=840:h=160:color=white:t=fill[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", str(clip))
    row = {"start_s": 0.0, "end_s": 3.0}
    config = {"clips": {}, "tracking": {"sample_fps": 7.5}}
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        out = fastbase.make(row, {}, config, source, clip, tmp_path / "work")
    assert out is not None and build.probe(out)["height"] == 1920
    assert (tmp_path / "work" / "crop.cmd").read_text(encoding="utf-8").strip() == ""


def test_a_vertical_live_clip_of_a_wide_source_still_takes_the_full_render(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from kitty_editor import fastbase

    wide = tmp_path / "wide.mp4"
    _run("-f", "lavfi", "-i", f"testsrc2=s=640x360:rate={RATE}", "-t", "2", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(wide))
    clip = tmp_path / "clip.mp4"
    _run("-f", "lavfi", "-i", f"testsrc2=s=1080x1920:rate={RATE}", "-t", "2", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(clip))
    row = {"start_s": 0.0, "end_s": 1.0}
    assert fastbase.make(row, {"vertical_live": True}, {"clips": {}, "tracking": {}}, wide, clip,
                         tmp_path / "work") is None
