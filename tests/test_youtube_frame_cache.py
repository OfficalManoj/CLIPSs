"""GET /clips/{id}/frame (the thumbnail picker's stills) never shows a still
of a clip's old video. An editor export keeps the clip's id (editor suite
step KC), so the cached still's name carries the file's stamp."""

import os
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from core.state import StateDB  # noqa: E402
from server import youtube_api, youtube_service  # noqa: E402


def test_a_new_file_under_the_same_clip_id_gets_a_new_still(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    try:
        youtube_service.save_settings(db, {"enabled": True})
        db.upsert_video("example-video")
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"the AI's video")
        clip_id = db.add_clip("example-video", 0, 10, 90, "A clip", path=str(path))
    finally:
        db.close()
    made = []

    def extract(source, at, target):
        made.append((at, target.name))
        target.write_bytes(source.read_bytes())

    monkeypatch.setattr(youtube_api, "_extract_frame", extract)
    app = fastapi.FastAPI()
    youtube_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path,
                        worker=SimpleNamespace(notify=lambda: None), publish_worker=None)
    client = TestClient(app)

    first = client.get(f"/clips/{clip_id}/frame", params={"t": 2.5})
    assert first.status_code == 200 and first.content == b"the AI's video"
    again = client.get(f"/clips/{clip_id}/frame", params={"t": 2.5, "v": "ignored"})
    assert again.content == b"the AI's video" and len(made) == 1     # the same file: kept
    assert made[0][1].startswith(f"clip_{clip_id}_2500_")

    # The editor's export replaces the file in place, under the same clip id.
    path.write_bytes(b"the editor's export, longer")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    after = client.get(f"/clips/{clip_id}/frame", params={"t": 2.5})
    assert after.status_code == 200 and after.content == b"the editor's export, longer"
    assert len(made) == 2 and made[1][1] != made[0][1]
    # The chosen thumbnail's name is untouched (core/remake.py looks for it).
    assert not any(name.endswith("_chosen.jpg") for _, name in made)
