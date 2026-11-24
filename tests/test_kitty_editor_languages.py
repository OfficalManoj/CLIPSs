"""Other languages as editor tabs (step KF, part A): kitty_editor/languages.py
and the intake rule for a language tab's export.

A saved project in the editor's own file format (timelines, lanes, clips) is
written here; the headless editor the voice lane goes through is a small
stand-in session working on that file."""

import json
import os
from pathlib import Path

import pytest

from kitty_editor import automation, intake, languages, registry


def _text(cid, track, start, dur, words, family="Montserrat"):
    return {"id": cid, "trackId": track, "mediaId": "", "kind": "text", "name": words, "start": start,
            "duration": dur, "volume": 1.0, "offsetY": 0.35,
            "text": {"content": words, "fontFamily": family, "fontSize": 0.05, "color": "#ffffff",
                     "strokeWidth": 0.012, "background": ""}}


def _video(cid, start, dur, source_start):
    return {"id": cid, "trackId": "T4", "mediaId": "m1", "kind": "video", "name": "base.mp4", "start": start,
            "duration": dur, "sourceStart": source_start, "speed": 1.0, "volume": 1.0, "scale": 3.16,
            "videoEffects": [{"id": "blur", "params": {}}]}


CAPTIONS = [(0.0, 2.0, "We went to the gym"), (2.0, 4.0, "and it was packed"), (4.0, 6.0, "so we left."),
            (6.0, 8.0, "Then the bus came"), (8.0, 10.0, "and we got on"), (10.0, 12.0, "it was late.")]


def _project(home: Path) -> dict:
    clips = [_video("c2", 0.0, 3.0, 10.0), _video("c3", 3.0, 3.0, 20.0), _video("c4", 6.0, 3.0, 30.0),
             _video("c5", 9.0, 3.0, 40.0)]
    clips += [_text(f"c{10 + i}", "t6", a, b - a, w) for i, (a, b, w) in enumerate(CAPTIONS)]
    clips.append(_text("c20", "t7", 0.0, 3.0, "WAIT FOR IT", "Hanken Grotesk"))         # the hook
    clips.append(_text("c21", "t8", 0.0, 12.0, "@me", "Hanken Grotesk"))                # text watermark
    clips.append({"id": "c22", "trackId": "t9", "mediaId": "m3", "kind": "audio", "name": "music.mp3",
                  "start": 0.0, "duration": 12.0, "volume": 0.2})
    tracks = [{"id": t, "muted": False, "visible": True} for t in ("t8", "t7", "t6", "T1", "T2", "T3", "T4", "t9")]
    timeline = {"id": "TL1", "name": "Timeline 1", "video": {"width": 1080, "height": 1920, "rateNum": 30,
                                                                "rateDen": 1}, "tracks": tracks, "clips": clips}
    return {"concat": "0.1.0", "version": 2, "name": "Gym", "video": timeline["video"],
            "media": [{"id": "m1", "path": str(home / "media" / "base.mp4"), "kind": "video"},
                      {"id": "m3", "path": str(home / "media" / "music.mp3"), "kind": "audio"}],
            "tracks": tracks, "clips": clips, "fonts": [], "timelines": [timeline], "activeTimelineId": "TL1"}


class FakeSession:
    """The tools languages.apply asks of a headless editor, on the saved file."""

    def __init__(self, exe, home, roots):
        self.doc = None
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
            self.n = languages._highest(self.doc)
            return {}
        active = next(t for t in self.doc["timelines"] if t["id"] == self.doc["activeTimelineId"])
        if name == "edit_apply":
            assert a["command"]["op"] == "selectTimeline"
            self.doc["activeTimelineId"] = a["command"]["timelineId"]
            return {}
        if name == "media_import":
            self.n += 1
            self.doc["media"].append({"id": f"m{self.n}", "path": a["file"], "kind": "audio"})
            return {"createdId": f"m{self.n}"}
        if name == "track_add":
            self.n += 1
            active["tracks"].append({"id": f"t{self.n}"})
            return {"createdId": f"t{self.n}"}
        if name == "clip_add":
            self.n += 1
            active["clips"].append({"id": f"c{self.n}", "kind": "audio", "trackId": a["trackId"],
                                    "mediaId": a["mediaId"], "start": a["start"], "duration": 12.0, "volume": 1.0})
            return {"createdId": f"c{self.n}"}
        if name in ("project_save", "project_close"):
            if name == "project_save" or a.get("save"):
                (Path(self.path) / "concat.json").write_text(json.dumps(self.doc), encoding="utf-8")
            if name == "project_close":
                (Path(self.path) / "concat.lock").unlink()
                self.path = None
            return {}
        raise AssertionError(name)


@pytest.fixture()
def made(tmp_path):
    data = tmp_path / "data"
    manifest = registry.save(data, {"schema": registry.SCHEMA, "id": registry.new_id(), "name": "Gym",
                                    "kind": "clip", "state": "ready", "clip": {"id": 7, "video_id": "v"},
                                    "exports": []})
    home = registry.folder(data, manifest["id"])
    project_dir = home / "p" / "Gym"
    project_dir.mkdir(parents=True)
    (project_dir / "concat.json").write_text(json.dumps(_project(home)), encoding="utf-8")
    record = {"lines": [{"start": a, "end": b, "text": w} for a, b, w in CAPTIONS]}
    manifest = registry.update(data, manifest["id"], lambda m: m.update(
        project_dir=str(project_dir), source_timeline="TL1", captions={"follow": record}))
    return data, manifest


def _fake_translate(lines, code):
    return [{**line, "text": f"[{code}] {line['text']}"} for line in lines]


def _read(manifest):
    return json.loads((Path(manifest["project_dir"]) / "concat.json").read_text(encoding="utf-8"))


def test_caption_lane_found_by_the_record(made):
    data, manifest = made
    project = _read(manifest)
    items = languages.caption_items(languages.source_timeline(project, manifest), manifest)
    assert [c["id"] for c in items] == [f"c{10 + i}" for i in range(6)]


def test_prepare_then_apply_adds_translated_tabs(made):
    data, manifest = made
    pid = manifest["id"]
    before = _read(manifest)
    languages.prepare(data, pid, ["es", "ja"], translate=_fake_translate)
    out = languages.apply(data, pid, exe="x", open_session=FakeSession)
    assert [a["language"] for a in out["added"]] == ["es", "ja"]
    project = _read(manifest)
    assert len(project["timelines"]) == 3
    assert project["timelines"][0] == before["timelines"][0]            # the clip's own tab untouched
    es, ja = project["timelines"][1], project["timelines"][2]
    assert (es["name"], ja["name"]) == ("Spanish", "Japanese")
    assert project["activeTimelineId"] == es["id"]
    ids = [c["id"] for t in project["timelines"] for c in t["clips"]] + \
          [x["id"] for t in project["timelines"] for x in t["tracks"]] + [t["id"] for t in project["timelines"]]
    assert len(ids) == len(set(ids))
    source = before["timelines"][0]["clips"]
    for tab, code in ((es, "es"), (ja, "ja")):
        lane_of = {old["id"]: new["id"] for old, new in zip(before["timelines"][0]["tracks"],
                                                             tab["tracks"])}
        assert len(tab["clips"]) == len(source)
        for old, new in zip(source, tab["clips"]):
            assert new["trackId"] == lane_of[old["trackId"]]
            same = {k: v for k, v in old.items() if k not in ("id", "trackId", "text", "name")}
            assert {k: v for k, v in new.items() if k not in ("id", "trackId", "text", "name")} == same
            if old["id"] in {f"c{10 + i}" for i in range(6)}:
                assert new["text"]["content"] == f"[{code}] {old['text']['content']}"
                want = "Kosugi Maru" if code == "ja" else "Montserrat"
                assert new["text"]["fontFamily"] == want
                assert {k: v for k, v in new["text"].items() if k not in ("content", "fontFamily")} == \
                    {k: v for k, v in old["text"].items() if k not in ("content", "fontFamily")}
            elif old.get("text"):
                assert new["text"] == old["text"]                       # hook and watermark untouched
    saved = registry.load(data, pid)
    assert saved["languages"] == {es["id"]: "es", ja["id"]: "ja"}
    assert not (languages.folder(data, pid) / languages.PENDING).exists()
    assert {t["language"] for t in languages.tabs(data, pid)["tabs"]} == {"es", "ja"}


def test_dub_adds_a_voice_lane_and_turns_the_clip_down(made):
    data, manifest = made
    pid = manifest["id"]

    def speak(lines, code, out):
        out.write_bytes(b"voice")
        return out

    languages.prepare(data, pid, ["es"], translate=_fake_translate, speak=speak)
    languages.apply(data, pid, exe="x", open_session=FakeSession)
    project = _read(manifest)
    es = project["timelines"][1]
    voice = [c for c in es["clips"] if c["kind"] == "audio" and c["mediaId"] != "m3"]
    assert len(voice) == 1 and voice[0]["start"] == 0.0
    assert es["tracks"][-1]["id"] == voice[0]["trackId"]
    assert all(c["volume"] == 0.12 for c in es["clips"] if c["kind"] == "video")
    assert next(c for c in es["clips"] if c["mediaId"] == "m3")["volume"] == 0.2      # music as it was
    assert all(c["volume"] == 1.0 for c in project["timelines"][0]["clips"] if c["kind"] == "video")


def test_no_dub_leaves_the_sound(made):
    data, manifest = made
    languages.prepare(data, manifest["id"], ["es"], translate=_fake_translate)
    languages.apply(data, manifest["id"], exe="x", open_session=FakeSession)
    es = _read(manifest)["timelines"][1]
    assert all(c["volume"] == 1.0 for c in es["clips"] if c["kind"] == "video")


def test_held_project_is_left_alone(made):
    data, manifest = made
    languages.prepare(data, manifest["id"], ["es"], translate=_fake_translate)
    (Path(manifest["project_dir"]) / "concat.lock").write_text("window")
    before = (Path(manifest["project_dir"]) / "concat.json").read_bytes()
    with pytest.raises(languages.Held):
        languages.apply(data, manifest["id"], exe="x", open_session=FakeSession)
    assert (Path(manifest["project_dir"]) / "concat.json").read_bytes() == before
    assert languages.has_pending(data, manifest["id"])


def test_failure_puts_the_project_back(made, monkeypatch):
    data, manifest = made

    def speak(lines, code, out):
        out.write_bytes(b"voice")
        return out

    languages.prepare(data, manifest["id"], ["es"], translate=_fake_translate, speak=speak)
    before = (Path(manifest["project_dir"]) / "concat.json").read_bytes()

    class Broken(FakeSession):
        def tool(self, name, **a):
            if name == "clip_add":
                raise automation.EditorError("no")
            return super().tool(name, **a)

    with pytest.raises(languages.Failed):
        languages.apply(data, manifest["id"], exe="x", open_session=Broken)
    assert (Path(manifest["project_dir"]) / "concat.json").read_bytes() == before
    assert languages.has_pending(data, manifest["id"])
    assert "languages" not in registry.load(data, manifest["id"])


def test_no_captions_says_so(made):
    data, manifest = made
    project = _read(manifest)
    tab = project["timelines"][0]
    tab["clips"] = [c for c in tab["clips"] if c["trackId"] != "t6"]         # the captions deleted in the editor
    (Path(manifest["project_dir"]) / "concat.json").write_text(json.dumps(project), encoding="utf-8")
    with pytest.raises(ValueError, match="no captions"):
        languages.prepare(data, manifest["id"], ["es"], translate=_fake_translate)


def _export(manifest, data, name="Gym.mp4"):
    out = registry.folder(data, manifest["id"]) / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(b"video")
    return name


def test_language_tab_export_goes_to_the_folder(made, tmp_path, monkeypatch):
    data, manifest = made
    registry.update(data, manifest["id"], lambda m: m.update(languages={"tl40": "es"}))
    monkeypatch.setattr(registry, "probe_seconds", lambda p: 12.0)
    where = tmp_path / "exports"

    def no_db():
        raise AssertionError("a language tab's export never touches the clip")

    answer = intake.take_in(data, manifest["id"], _export(manifest, data), folder=str(where), open_db=no_db,
                            timeline="tl40")
    assert answer["result"] == "saved_to_folder"
    assert Path(answer["path"]).name == "Gym (Spanish).mp4"
    # An export whose tab isn't known, of a project with language tabs: also to the folder.
    answer = intake.take_in(data, manifest["id"], _export(manifest, data, "Gym2.mp4"), folder=str(where),
                            open_db=no_db)
    assert answer["result"] == "saved_to_folder"


def test_edited_captions_read_the_clips_own_tab(made):
    data, manifest = made
    project = _read(manifest)
    copy_tab = json.loads(json.dumps(project["timelines"][0]))
    copy_tab["id"] = "TL50"
    for c in copy_tab["clips"]:
        if c["kind"] == "text":
            c["text"]["content"] = "otro"
    project["timelines"].append(copy_tab)
    project["activeTimelineId"] = "TL50"
    (Path(manifest["project_dir"]) / "concat.json").write_text(json.dumps(project), encoding="utf-8")
    record = {"lines": [{"start": a, "end": b, "text": w} for a, b, w in CAPTIONS],
              "source": [{"start": a, "end": b, "text": w} for a, b, w in CAPTIONS]}
    lines = intake.edited_captions(manifest["project_dir"], record, "TL1")
    assert [x["text"] for x in lines] == [w for _, _, w in CAPTIONS]


def test_voice_only_dub_mixes_just_the_voice(tmp_path, monkeypatch):
    from multilingual import dub

    calls = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(dub.subprocess, "run", lambda cmd, **k: calls.append(cmd) or Done())
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    out = dub._voice_track([(wav, 1.5)], tmp_path / "es-voice.m4a")
    assert out == tmp_path / "es-voice.m4a"
    cmd = calls[0]
    assert "-vn" in cmd and "0:v" not in cmd
    assert "adelay=1500|1500" in cmd[cmd.index("-filter_complex") + 1]
    assert os.fspath(wav.resolve()) in cmd
