"""A run whose every render failed ends failed, with the reason (#281).

FFmpeg 9 refused every cut, and the run still finished as "done" with an
outcome that read "nothing went wrong". These run process_video with
download, transcription, analysis and the title writer stubbed out (the
`rerun` fixture of tests/test_plugin_edit_rerun.py), and a render that fails
the way FFmpeg 9 failed: every time, once, or by being cancelled.
"""

import pytest
from test_plugin_edit_rerun import _found, rerun  # noqa: F401  (rerun is a fixture)

# What FFmpeg 9.1 printed for a cut with -vsync in it.
FFMPEG9_REFUSAL = (
    "ffmpeg cut failed:\n"
    "Unrecognized option 'vsync'.\n"
    "Error splitting the argument list: Option not found"
)


def _state(tmp_path):
    """The database the run used (the fixture's first), opened again."""
    from core.state import StateDB

    return StateDB(tmp_path / "state-0.db")


def test_every_render_failing_fails_the_run_with_the_reason(rerun, monkeypatch, tmp_path):  # noqa: F811
    import core.pipeline as pipeline
    from core.outcome import RendersFailedError

    def refused(*_a, **_k):
        raise RuntimeError(FFMPEG9_REFUSAL)

    monkeypatch.setattr(pipeline, "_render_files", refused)

    with pytest.raises(RendersFailedError, match=r"^All \d+ clips failed while being made: "
                                                 r"this FFmpeg version is not supported: .*-vsync"):
        rerun(_found())

    db = _state(tmp_path)
    try:
        assert db.video_status("vid321") == "failed"
        out = db.get_outcome("vid321")
        assert out["cause"] == "render_failed"
        assert out["render_failed"] == out["clips"] > 1
        assert "-vsync" in out["render_error"]
    finally:
        db.conn.close()


def test_one_clip_made_keeps_the_run_done_and_counts_the_failure(rerun, monkeypatch, tmp_path):  # noqa: F811
    import core.pipeline as pipeline

    made = pipeline._render_files      # the fixture's stand-in render

    def first_refused(source, candidate, *a, **k):
        if candidate.start == 10.0:
            raise RuntimeError(FFMPEG9_REFUSAL)
        return made(source, candidate, *a, **k)

    monkeypatch.setattr(pipeline, "_render_files", first_refused)

    got = rerun(_found())

    assert len(got.rows) == len(_found()) - 1
    db = _state(tmp_path)
    try:
        assert db.video_status("vid321") == "done"
        out = db.get_outcome("vid321")
        assert out["render_failed"] == 1
        assert "-vsync" in out["render_error"]
        assert out["cause"] != "render_failed"
    finally:
        db.conn.close()


def test_renders_stopped_by_a_cancel_do_not_fail_the_run(rerun, monkeypatch, tmp_path):  # noqa: F811
    """A render PC's job cancelled on its side comes back as CancelledError
    with the video id as its text: not a clip that failed."""
    import core.pipeline as pipeline
    from core.cancel import CancelledError

    def cancelled(*_a, **_k):
        raise CancelledError("vid321")

    monkeypatch.setattr(pipeline, "_render_files", cancelled)

    rerun(_found())

    db = _state(tmp_path)
    try:
        assert db.video_status("vid321") == "done"
        out = db.get_outcome("vid321")
        assert "render_failed" not in out and out.get("cause") != "render_failed"
    finally:
        db.conn.close()
