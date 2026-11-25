"""Editor suite step KG: the Layout picker's rebuild of a clip's editor
project (kitty_editor/relayout.py).

A3: a gaming layout to another, to Letterbox, Letterbox to Center, Center
to Auto (the tracker's base video, on the base's clock) and Auto back to a
gaming layout (on the source's clock): the cuts, speed and captions are the
same before and after, only the picture lanes change. A project held by the
editor is left as it was (409), and a failure puts the file back and keeps
the pending layout for the next try.

The headless editor is a small stand-in session working on the saved file."""

import json
from pathlib import Path

import pytest

from kitty_editor import automation, build, registry, relayout, sections

START = 100.0
CONFIG = {"clips": {"vertical": True, "whole": False}, "tracking": {"detector": "yolov8n-pose.pt"}}
GAMING = {"cam": [0.02, 0.05, 0.25, 0.3], "preset": "split", "by": "video", "layout": "split", "used_preset": "split",
          "used_cam": [0.02, 0.05, 0.25, 0.3], "panels": [], "heads_norm": {"cam": [0.14, 0.06, 0.2]}}


class FakeSession:
    """What relayout asks of a headless editor, on the saved file."""

    def __init__(self, exe, home, roots):
        self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.path:
            (Path(self.path) / "concat.lock").unlink(missing_ok=True)

    def tool(self, name, **a):
        if name == "project_open":
            lock = Path(a["path"]) / "concat.lock"
            if lock.exists():
                raise automation.EditorError("That project is open somewhere else.")
            lock.write_text("x")
            self.path = a["path"]
            self.doc = json.loads((Path(a["path"]) / "concat.json").read_text(encoding="utf-8"))
            return {}
        if name == "media_import":
            self.doc["media"].append({"id": f"m{len(self.doc['media']) + 50}", "path": a["file"].replace("\\", "/"),
                                      "kind": "video", "width": 1080, "height": 1920})
            return {}
        if name in ("project_save", "project_close"):
            if name == "project_save" or a.get("save"):
                (Path(self.path) / "concat.json").write_text(json.dumps(self.doc), encoding="utf-8")
            if name == "project_close":
                (Path(self.path) / "concat.lock").unlink()
                self.path = None
            return {}
        raise AssertionError(name)


def _video(cid, start, dur, source_start):
    return {"id": cid, "trackId": "T4", "mediaId": "m1", "kind": "video", "name": "source.mp4", "start": start,
            "duration": dur, "sourceStart": source_start, "speed": 1.25, "volume": 1.0, "scale": 1.0,
            "offsetX": 0.0, "offsetY": 0.0, "opacity": 1.0, "videoEffects": [], "filters": []}


def _text(cid, start, dur, words):
    return {"id": cid, "trackId": "t6", "mediaId": "", "kind": "text", "name": words, "start": start,
            "duration": dur, "text": {"content": words}}


@pytest.fixture()
def made(tmp_path, monkeypatch):
    data = tmp_path / "data"
    downloads = data / "downloads"
    downloads.mkdir(parents=True)
    source = downloads / "vid1.mp4"
    source.write_bytes(b"the stream")
    monkeypatch.setattr(build, "probe", lambda path: {"video": True, "read": True, "width": 1920, "height": 1080,
                                                      "fps_num": 60, "fps_den": 1, "seconds": 3600.0})
    manifest = registry.save(data, {"schema": registry.SCHEMA, "id": registry.new_id(), "name": "Clip",
                                    "kind": "clip", "state": "ready", "clip": {"id": 7, "video_id": "vid1"},
                                    "exports": [], "notes": [], "video_file": str(source), "clock": "source"})
    home = registry.folder(data, manifest["id"])
    project_dir = home / "p" / "Clip"
    project_dir.mkdir(parents=True)
    clips = [_video("c2", 0.0, 2.4, START + 0.0), _video("c3", 2.4, 4.0, START + 4.0),
             _text("c10", 0.0, 2.0, "hello"), _text("c11", 2.0, 2.0, "there")]
    tracks = [{"id": t, "muted": False, "visible": True} for t in ("T1", "T4", "t6")]
    timeline = {"id": "TL1", "name": "Timeline 1", "tracks": tracks, "clips": clips,
                "video": {"width": 1080, "height": 1920, "rateNum": 60, "rateDen": 1}}
    project = {"name": "Clip", "media": [{"id": "m1", "path": str(source).replace("\\", "/"), "kind": "video"}],
               "timelines": [timeline], "activeTimelineId": "TL1", "tracks": tracks, "clips": clips}
    counter = [20]

    def mint(prefix):
        counter[0] += 1
        return f"{prefix}{counter[0]}"

    sections.place(project, sections.gaming_specs(GAMING, 1920, 1080), ["m1"], mint)
    (project_dir / "concat.json").write_text(json.dumps(project), encoding="utf-8")
    manifest = registry.update(data, manifest["id"], lambda m: m.update(project_dir=str(project_dir)))
    row = {"id": 7, "video_id": "vid1", "start_s": START, "end_s": START + 8.0, "path": None,
           "render_opts": json.dumps({"gaming": GAMING})}

    def render_base(row_, config, data_dir, work_dir, progress=None):
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "base.mp4"
        out.write_bytes(b"framed")
        made_bases.append(json.loads(row_["render_opts"]))
        return out

    made_bases: list = []
    monkeypatch.setattr(build, "render_base", render_base)
    return data, manifest, row, project_dir, made_bases


def _read(project_dir) -> dict:
    return json.loads((Path(project_dir) / "concat.json").read_text(encoding="utf-8"))


def _cuts(project: dict) -> list:
    tl = project["timelines"][0]
    main = next(t["id"] for t in tl["tracks"] if t.get("kittyRole") in sections.MAIN_ROLES)
    return [(c["start"], c["duration"], c["speed"]) for c in sorted(tl["clips"], key=lambda c: c["start"])
            if c["trackId"] == main]


def _captions(project: dict) -> list:
    return [c for c in project["timelines"][0]["clips"] if c["kind"] == "text"]


def _change(data, manifest, row, body):
    relayout.prepare(data, manifest["id"], body, row=row, config=CONFIG)
    assert relayout.has_pending(data, manifest["id"])
    out = relayout.apply(data, manifest["id"], exe="editor.exe", open_session=FakeSession)
    assert not relayout.has_pending(data, manifest["id"])
    return out


def test_each_layout_change_keeps_the_cuts_and_captions(made):
    data, manifest, row, project_dir, bases = made
    cuts, words = _cuts(_read(project_dir)), _captions(_read(project_dir))

    _change(data, manifest, row, {"gaming": {**GAMING, "preset": "circle_cam"}})
    project = _read(project_dir)
    roles = [t.get("kittyRole") for t in project["timelines"][0]["tracks"]]
    assert roles == [None, "game", "cam", None]
    cam = next(c for c in project["timelines"][0]["clips"] if c.get("kittyRole") == "cam")
    assert cam["masks"][0]["source"]["shape"] == "circle"
    assert registry.load(data, manifest["id"])["layout"]["gaming"]["preset"] == "circle_cam"

    _change(data, manifest, row, {"crop": "letterbox"})
    project = _read(project_dir)
    assert [t.get("kittyRole") for t in project["timelines"][0]["tracks"]] == [None, "picture", None]
    assert _cuts(project) == cuts and _captions(project) == words

    _change(data, manifest, row, {"crop": "center"})
    main = [c for c in _read(project_dir)["timelines"][0]["clips"] if c["kind"] == "video"]
    assert all(c["scale"] > 3 and "background" not in c for c in main)

    out = _change(data, manifest, row, {"crop": "track"})                      # Auto: a base, on its clock
    assert bases and bases[-1]["crop"] == "track" and bases[-1]["gaming"] is None
    project = _read(project_dir)
    main = sorted((c for c in project["timelines"][0]["clips"] if c["kind"] == "video"), key=lambda c: c["start"])
    assert [c["sourceStart"] for c in main] == [0.0, 4.0]
    assert all(c["scale"] == 1.0 and "crop" not in c and "background" not in c for c in main)
    kept = registry.load(data, manifest["id"])
    assert kept["clock"] == "base" and Path(kept["video_file"]).parent == registry.folder(data, manifest["id"]) / "media"
    assert out["old_video"] == manifest["video_file"] and "framing_baked" in kept["notes"]
    assert _cuts(project) == cuts and _captions(project) == words

    _change(data, manifest, row, {"gaming": GAMING})                            # back on the source's clock
    project = _read(project_dir)
    main = sorted((c for c in project["timelines"][0]["clips"]
                   if c.get("kittyRole") == "game"), key=lambda c: c["start"])
    assert [c["sourceStart"] for c in main] == [START, START + 4.0]
    source_id = next(m["id"] for m in project["media"] if m["path"].endswith("vid1.mp4"))
    assert {c["mediaId"] for c in main} == {source_id}
    assert _cuts(project) == cuts and _captions(project) == words
    assert registry.load(data, manifest["id"])["clock"] == "source"
    # The base the server deletes after this change is out of the bin too:
    # no "can't find these files" box on the next open.
    assert [m["path"] for m in project["media"]] == [m["path"] for m in project["media"] if m["id"] == source_id]


def test_a_video_kept_on_disk_or_still_used_stays_in_the_bin(made):
    data, manifest, row, project_dir, bases = made
    project = _read(project_dir)
    outside = {"id": "m9", "path": "D:/elsewhere/clip.mp4", "kind": "video"}
    relayout._forget_dropped({**project, "media": [*project["media"], outside]}, outside["path"], Path("x.mp4"),
                             registry.folder(data, manifest["id"]) / "media", {"m1"})
    media = registry.folder(data, manifest["id"]) / "media"
    media.mkdir(parents=True, exist_ok=True)
    used = {"id": "m8", "path": str(media / "base.mp4").replace("\\", "/"), "kind": "video"}
    doc = {**project, "media": [*project["media"], used]}
    doc["timelines"][0]["clips"][0]["mediaId"] = "m8"
    relayout._forget_dropped(doc, str(media / "base.mp4"), Path("x.mp4"), media, {"m1"})
    assert used in doc["media"]
    doc["timelines"][0]["clips"][0]["mediaId"] = "m1"
    relayout._forget_dropped(doc, str(media / "base.mp4"), Path("x.mp4"), media, {"m1"})
    assert used not in doc["media"] and doc["media"][0]["id"] == "m1"


def test_the_links_survive_each_layout_change(made):
    data, manifest, row, project_dir, bases = made
    record = {"follow": {"lines": [{"start": 0.0, "end": 2.0, "text": "hello"},
                                   {"start": 2.0, "end": 4.0, "text": "there"}]}}
    registry.update(data, manifest["id"], lambda m: m.update(captions=record))

    def links(project):
        tl = project["timelines"][0]
        columns: dict = {}
        for c in tl["clips"]:
            if c["kind"] == "video":
                columns.setdefault(c.get(sections.TIME_LINK), set()).add(c["trackId"])
        looks = [c.get(sections.LOOK_LINK) for c in tl["clips"] if c["kind"] == "text"]
        return columns, looks

    for body in ({"gaming": {**GAMING, "preset": "circle_cam"}}, {"crop": "letterbox"}, {"crop": "track"},
                 {"gaming": GAMING}):
        _change(data, manifest, row, body)
        project = _read(project_dir)
        columns, looks = links(project)
        lanes = [t for t in project["timelines"][0]["tracks"] if t.get("kittyRole")]
        assert sorted(columns) == ["c2", "c3"], body
        assert all(len(v) == len(lanes) for v in columns.values()), body
        assert looks == ["captions", "captions"], body


def test_a_held_project_is_left_as_it_was(made):
    data, manifest, row, project_dir, _ = made
    before = (project_dir / "concat.json").read_bytes()
    relayout.prepare(data, manifest["id"], {"crop": "letterbox"}, row=row, config=CONFIG)
    (project_dir / "concat.lock").write_text("the window")
    with pytest.raises(relayout.Held):
        relayout.apply(data, manifest["id"], exe="editor.exe", open_session=FakeSession)
    assert (project_dir / "concat.json").read_bytes() == before and relayout.has_pending(data, manifest["id"])


def test_a_failure_puts_the_file_back_and_keeps_the_pending_layout(made, monkeypatch):
    data, manifest, row, project_dir, _ = made
    before = (project_dir / "concat.json").read_bytes()
    relayout.prepare(data, manifest["id"], {"crop": "center"}, row=row, config=CONFIG)

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sections, "place", broken)
    with pytest.raises(relayout.Failed):
        relayout.apply(data, manifest["id"], exe="editor.exe", open_session=FakeSession)
    assert (project_dir / "concat.json").read_bytes() == before and relayout.has_pending(data, manifest["id"])
    assert "layout" not in registry.load(data, manifest["id"])


def test_a_choice_is_one_layout():
    assert relayout.choice_of({"crop": "letterbox"}) == {"kind": "framing", "crop": "letterbox"}
    assert relayout.choice_of({"gaming": {"preset": "mosaic", "by": "x", "cam": [0, 0, 1, 1]}}) == {
        "kind": "gaming", "gaming": {"preset": "mosaic", "cam": [0, 0, 1, 1]}}
    for bad in ({}, {"crop": "zoom"}, {"crop": "center", "gaming": {}}, {"gaming": {"preset": "nope"}}):
        with pytest.raises(ValueError):
            relayout.choice_of(bad)
