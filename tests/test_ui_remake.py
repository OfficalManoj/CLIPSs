"""Make clips again (#190, editor-suite step 2), built on the engine's
contract in docs/editor-suite/api/step2-remake.md.

The Clips page's "Make clips again..." button and its dialog are gone (his
call, step KC: he never asked for them). The engine's routes, core/remake.py
and the Queue row that says what a run did stay.

- lib/remake.ts decides what a run starts from and posts, why it is off,
  and the words of its count and result. It runs under Node here, as
  tests/test_ui_steps.py runs lib/steps.ts, and is held to the engine's own
  lists (core/remake.py REASONS and OPTIONS).
- The screens have no React test harness, so what they must keep doing is
  checked in their source: no button or dialog on the Clips page, the Queue
  page's own settings panel left as it was, and Add videos' Process again
  left as it was.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_ui_i18n_editor import untranslated

from core import remake

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
LIB = UI / "lib"

# What the Queue page's panel (QueueItemSettings) saves for a video whose
# settings nobody changed: every switch it has, said again, and the ones that
# are off in `clear`. Caught from the panel in the browser for a job with
# 60s+ and captions on.
STYLE = {"font": "Arial", "font_size": 84, "color": "#FFFFFF", "position": "bottom", "words_per_caption": 3,
         "uppercase": True, "highlight": False, "highlight_color": "#FFE600", "second_speaker": False,
         "second_speaker_color": "#5CE1FF"}
UNCHANGED = {"captions": True, "long_clips": True, "caption_style": STYLE,
             "clear": ["podcast", "vertical_live", "gaming_scoring", "gaming", "gaming_layout", "gaming_remember",
                       "sport", "longform", "pipeline", "watermark_profile_id"]}


def _source(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, body: str, data=None):
    """Run `body` (JavaScript: `m` is lib/remake.ts, `data` the JSON given) and
    return what it prints as JSON."""
    module = tmp_path / "remake.ts"
    module.write_text((LIB / "remake.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    # Node writes UTF-8 to a pipe whatever the Windows code page is.
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---- what the dialog posts -----------------------------------------------------------------------


def test_the_last_runs_options_go_back_as_they_were_when_nothing_is_changed(tmp_path):
    """GET's options, through an untouched panel, post back unchanged: the
    engine then queues exactly the last run's options (its own test)."""
    last = {"caption_style": STYLE, "captions": True, "long_clips": True}
    assert _run(tmp_path, "return m.remakeOptions(data.last, data.patch)", {"last": last, "patch": UNCHANGED}) == last


def test_what_the_panel_doesnt_show_goes_back_and_a_switch_turned_off_goes(tmp_path):
    """Hashtags, the plugins, the split and the rest aren't on the panel, and
    what its patch leaves out (Whole video is on it only in the dialog:
    test_a_whole_video_shows_as_a_switch_in_the_dialog) goes back as the last
    run had it, since a key left out would mean the setting's own value.
    60s+ unticked is cleared."""
    plugins = {"pipeline": {"id": "example-dev/finder"}, "rate": [{"id": "example-dev/rater"}],
               "understand": [{"id": "example-dev/notes"}], "edit": [{"id": "example-dev/trimmer"}]}
    hidden = {"whole": True, "max_clips": 4, "min_score": 40, "filter": "warm", "focus": "the timer",
              "hashtags": ["#mine"], "gaming": True, "gaming_layout": {"preset": "split"}, "gaming_remember": True,
              **plugins}
    last = {**hidden, "captions": True, "long_clips": True, "caption_style": STYLE}
    # The panel kept Gaming / Reaction and the plugins (a queued video's patch
    # leaves kept plugins out), and 60s+ was unticked.
    patch = {"captions": True, "gaming": True, "caption_style": STYLE,
             "clear": ["long_clips", "podcast", "vertical_live", "gaming_scoring", "sport", "longform",
                       "watermark_profile_id"]}
    got = _run(tmp_path, "return m.remakeOptions(data.last, data.patch)", {"last": last, "patch": patch})
    assert got == {**hidden, "captions": True, "caption_style": STYLE}
    # Turned off on the panel: gone, with what hung off it.
    off = {"captions": False, "caption_style": STYLE,
           "clear": ["gaming", "gaming_layout", "gaming_remember", "pipeline", "rate", "understand", "edit"]}
    got = _run(tmp_path, "return m.remakeOptions(data.last, data.patch)", {"last": last, "patch": off})
    assert got == {"whole": True, "max_clips": 4, "min_score": 40, "filter": "warm", "focus": "the timer",
                   "hashtags": ["#mine"], "long_clips": True, "captions": False, "caption_style": STYLE}


def test_longform_force_and_a_link_are_never_posted(tmp_path):
    """The engine refuses Longform here (400) and takes no link or force: the
    video's own link or file is used, and the run is forced."""
    data = {"start": {"captions": True, "url": "https://example.com/v", "force": False},
            "patch": {"longform": {"mode": "short_clips"}, "force": True, "captions": False, "clear": ["podcast"]}}
    assert _run(tmp_path, "return m.remakeOptions(data.start, data.patch)", data) == {"captions": False}


def test_the_dialog_starts_from_the_last_run_or_else_the_generate_bars_settings(tmp_path):
    """With no run left to read (Clear completed deletes them), the Generate
    bar's remembered settings, without Longform even when that is on there."""
    usual = {"captions": False, "caption_style": STYLE, "long_clips": True, "longform": {"mode": "highlights"}}
    data = {"usual": usual, "last": {"long_clips": True, "whole": True}}
    got = _run(tmp_path, """
        const usual = () => data.usual
        return [m.remakeStart({ options: data.last, from_job: 7 }, usual),
                m.remakeStart({ options: {}, from_job: null }, usual),
                m.remakeStart({ options: {}, from_job: 3 }, usual)]
    """, data)
    assert got[0] == {"long_clips": True, "whole": True}
    assert got[1] == {"captions": False, "caption_style": STYLE, "long_clips": True}
    # A run that had no options of its own starts from nothing, as it ran.
    assert got[2] == {}


# ---- why the action is off, and the count -----------------------------------------------------


def test_the_action_says_why_it_is_off_in_plain_words(tmp_path):
    cases = [
        ("running", "file", "This video’s clips are being made right now. You can make them again once that’s done."),
        ("queued", "link", "This video is waiting in the queue. You can make its clips again once it’s done, or "
                           "after you remove it from the queue."),
        ("source_gone", "file", "This video’s file isn’t in Clips Kitty any more, so its clips can’t be made again. "
                                "Add the file again."),
        ("source_gone", "link", "Clips Kitty no longer has this video’s download or a link to it, so its clips "
                                "can’t be made again. Add the video again."),
        ("busy", "link", "Clips Kitty is working with one of this video’s clips right now (publishing, exporting "
                         "or editing it). You can make its clips again once that’s done."),
        ("queue_full", "link", "The queue is full. Let a video finish, or remove one from the queue, first."),
        (None, "file", None),
    ]
    data = [{"blocked": blocked, "source": {"kind": kind}} for blocked, kind, _ in cases]
    assert _run(tmp_path, "return data.map((p) => m.remakeBlocked(p))", data) == [said for _, _, said in cases]
    # Every sentence goes through the screen's t().
    said = _run(tmp_path, "const said = []; data.forEach((p) => m.remakeBlocked(p, (s) => { said.push(s);"
                          " return s })); return said", data)
    assert said == [s for _, _, s in cases if s]


def test_the_counts_tooltip_says_what_replaced_means_and_why_each_is_kept(tmp_path):
    counts = {"total": 9, "replaced": 3, "kept": 6,
              "kept_because": {"unsure": 1, "edited": 2, "longform": 1, "chosen": 1, "published": 1, "busy": 0}}
    replaced = ("Replaced: the clips nobody used. Once the new clips are ready, each is made again with the new "
                "settings or deleted.")
    got = _run(tmp_path, "return [m.remakeCountTitle(data[0]), m.remakeCountTitle(data[1])]",
               [counts, {"total": 2, "replaced": 2, "kept": 0, "kept_because": {}}])
    # In the engine's order (core/remake.py REASONS), with none at zero.
    assert got[0] == (f"{replaced}\nKept: 16:9 Longform (1), edited (2), published or sent to publish (1), "
                      "chosen by a watched channel (1), not sure if used (1)")
    assert got[1] == replaced
    said = _run(tmp_path, "const said = []; m.remakeCountTitle(data, (s) => { said.push(s); return s });"
                          " return said", counts)
    assert said == [replaced, "16:9 Longform", "edited", "published or sent to publish",
                    "chosen by a watched channel", "not sure if used", "Kept:"]


def test_clips_from_before_the_update_get_a_line_saying_why(tmp_path):
    """Nothing on such a clip shows why it is kept (core/remake.py 'older':
    made before this update, or by another version of Clips Kitty, so it has
    no fingerprint of what its run made), so the tooltip says it, that one
    found again is made again in place, and what to do about the ones not
    wanted."""
    counts = {"total": 5, "replaced": 1, "kept": 4, "kept_because": {"older": 3, "busy": 1}}
    why = ("Clips made before this update, or in another version of Clips Kitty, are kept: Clips Kitty can’t "
           "tell whether you changed them. One the new settings find again at exactly the same start and end is "
           "made again with them, and keeps its title. Delete the ones you don’t want yourself.")
    got = _run(tmp_path, "return m.remakeCountTitle(data)", counts)
    assert got.split("\n")[1:] == ["Kept: still being edited, exported or published (1), made before this update or "
                                   "in another version (3)", why]
    said = _run(tmp_path, "const said = []; m.remakeCountTitle(data, (s) => { said.push(s); return s });"
                          " return said", counts)
    assert said[-1] == why


def test_every_reason_the_engine_keeps_a_clip_for_has_words_in_its_order():
    """lib/remake.ts KEPT_AS and lib/types.ts RemakeReason name the engine's
    reasons, in the engine's order: a reason added there shows up here."""
    module = (LIB / "remake.ts").read_text(encoding="utf-8")
    kept_as = re.search(r"const KEPT_AS: Record<RemakeReason, string> = \{(.*?)\n\}", module, re.S)
    assert kept_as
    assert tuple(re.findall(r"^\s+(\w+):", kept_as.group(1), re.M)) == remake.REASONS
    types = _source("lib/types.ts")
    union = re.search(r"export type RemakeReason =((?:\s*\|\s*'\w+')+)", types)
    assert union and tuple(re.findall(r"'(\w+)'", union.group(1))) == remake.REASONS


def test_every_option_the_engine_carries_is_one_the_dialog_can_hold():
    """RemakeOptions is JobOptions less force and Longform, plus hashtags:
    each option the engine carries from the last run (core/remake.py OPTIONS)
    has to be a key the screen's type knows, or the dialog drops it."""
    types = _source("lib/types.ts")
    job = re.search(r"export interface JobOptions \{(.*?)\n\}", types, re.S)
    assert job
    keys = set(re.findall(r"^  (\w+)\?:", job.group(1), re.M)) - {"force", "longform"} | {"hashtags"}
    assert set(remake.OPTIONS) <= keys, set(remake.OPTIONS) - keys
    assert ("export type RemakeOptions = Omit<JobOptions, 'force' | 'longform'> & { hashtags?: string[] }"
            in types)


# ---- the screens ----------------------------------------------------------------------------------


def test_the_api_calls_match_the_routes():
    api = _source("lib/api.ts")
    assert ("remakePreview: (videoId: string) =>\n"
            "    request<RemakePreview>(`/videos/${encodeURIComponent(videoId)}/remake`)") in api
    post = api[api.index("remakeVideo: "):]
    post = post[: post.index("}),") + 3]
    assert "`/videos/${encodeURIComponent(videoId)}/remake`" in post
    # A JSON body, all the options: the engine refuses a request without one.
    assert "method: 'POST'" in post and "body: JSON.stringify(options)" in post


def test_the_clips_page_no_longer_offers_make_clips_again():
    """Removed on his word (step KC): no button, no dialog, nothing reading
    whether it could be used. The engine's routes stay (other callers, MCP,
    tests), and so does lib/api.ts's client for them."""
    assert not (UI / "components" / "RemakeDialog.tsx").exists()
    studio = _source("pages/ClipStudio.tsx")
    for gone in ("Make clips again", "RemakeDialog", "refreshRemake", "remakePreview", "remakeNotice",
                 "REMAKE_BUSY_RECHECK_MS", "../lib/remake"):
        assert gone not in studio, gone
    for screen in (UI / "components").rglob("*.tsx"):
        assert "RemakeDialog" not in screen.read_text(encoding="utf-8"), screen
    # The clip list is still read again when a run ends (the replaced clips
    # go), as it always was.
    assert "if (e.type === 'job' && e.status === 'done' && activeVideo) refreshClips(activeVideo)" in studio
    server = (ROOT / "server" / "api.py").read_text(encoding="utf-8")
    assert '"/videos/{video_id}/remake"' in server








def test_a_clips_players_load_its_video_as_it_is_now():
    """A clip made again in place keeps its id: its /media URL carries the
    engine's media_version, which changes with its file, so the card, the
    details panel and the Other languages window load the new video without a reload."""
    api = _source("lib/api.ts")
    assert ("mediaUrl: (clipId: number, version?: string) =>\n    version\n"
            "      ? `${API_BASE}/media/${clipId}?v=${encodeURIComponent(version)}`\n"
            "      : `${API_BASE}/media/${clipId}`,") in api
    assert "media_version?: string" in _source("lib/types.ts")
    for screen in ("components/ClipCard.tsx", "components/ClipEditor.tsx"):
        source = _source(screen)
        assert "api.mediaUrl(clip.id, clip.media_version)" in source, screen
        assert "api.mediaUrl(clip.id)" not in source, screen





def test_the_keyboard_stays_in_a_window_and_goes_back_to_the_button():
    """lib/useDialogFocus.ts (first made for Make clips again's dialog, now
    the Publish window's): Tab and Shift+Tab cycle inside the window, and
    closing it puts the keyboard back on what opened it."""
    hook = _source("lib/useDialogFocus.ts")
    assert "returnTo?.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)" in hook
    assert "if (opener && opener.isConnected && !(opener as HTMLButtonElement).disabled) opener.focus()" in hook
    assert "if (e.key !== 'Tab' || !box.current) return" in hook
    assert "box.current.querySelectorAll<HTMLElement>(FOCUSABLE)" in hook
    assert "if (e.shiftKey && (lost || active === first || active === box.current))" in hook
    assert "} else if (!e.shiftKey && (lost || active === last)) {" in hook
    assert hook.count("e.preventDefault()") == 3
    publish = _source("components/PublishAllDialog.tsx")
    assert "useDialogFocus(box, () => {\n    if (!busy) onClose()\n  }, returnFocus)" in publish
    assert "aria-label={clips.length === 1 ? t('Publish this clip') : t('Publish all clips')}" in publish


def test_the_queue_pages_panel_is_as_it_was_unless_asked():
    """The Queue page and a watched channel pass none of the new props: Save
    settings, its Saving…, no Cancel and the Longform switch, as before."""
    panel = _source("components/queue/QueueItemSettings.tsx")
    assert "saveLabel = 'Save settings'," in panel and "savingLabel = 'Saving…'," in panel
    assert "noLongform = false" in panel
    assert "{busy ? t(savingLabel) : t(saveLabel)}" in panel
    assert "{onCancel && (" in panel
    longform = re.search(r"'Longform',\s*'\(16:9 clips\)',\s*longform,.*?\n          \},\n\s*'Makes 16:9 clips for "
                         r"YouTube and X\. Leave it off for 9:16 Shorts, from any video\.',\n\s*noLongform\n", panel,
                         re.S)
    assert longform
    assert "hidden ? null : (" in panel
    assert "offerWhole = false" in panel
    for user in ("components/queue/QueueItem.tsx", "components/watch/WatchCard.tsx"):
        source = _source(user)
        for prop in ("saveLabel", "savingLabel", "onCancel", "noLongform", "offerWhole"):
            assert prop not in source, (user, prop)


# ---- a video made as one whole clip (black-box S3) -------------------------------------------


def test_a_whole_video_shows_as_a_switch_when_asked():
    """A video whose last run made the whole video one clip (a watched
    channel's Twitch clips) has `whole` in its options. The panel shows it,
    on, as a switch to keep or turn off, only when asked (offerWhole): the
    Queue page and a watched channel's panel are as they were."""
    settings = _source("components/queue/QueueItemSettings.tsx")
    assert "const wholeShown = offerWhole && Boolean(s.whole)" in settings
    assert re.search(r"\{wholeShown &&\n\s*toggle\(\n\s*'Whole video',\n\s*'\(one clip\)',\n\s*whole,", settings)
    # Its tooltip goes through t(): toggle() shows a title as it is given.
    assert re.search(r"\n\s*t\(\n\s*'Last time the whole video became one clip, with no moments picked by the AI\.",
                     settings)
    # Saved only where it is shown: kept, or cleared when switched off.
    assert re.search(r"if \(wholeShown\) \{\n\s*if \(whole\) patch\.whole = true\n\s*else clear\.push\('whole'\)\n"
                     r"\s*\}", settings)
    assert "if (wholeShown) setWhole(false)" in settings


def test_the_whole_video_switch_follows_the_engines_rule():
    """The engine refuses a whole-video clip with Longform, 60s+, Sports, a
    pipeline, Rate or Suggest edits (server/api.py _process_options). On the
    panel, keeping Whole video on turns each of those off, and turning one
    of them on turns Whole video off, so the dialog never posts what the
    engine refuses."""
    api = (ROOT / "server" / "api.py").read_text(encoding="utf-8")
    rule = re.search(r'if payload\.get\("whole"\) and any\(payload\.get\(key\) for key in\n\s*\(([^)]*)\)\)', api)
    assert rule
    refused = re.findall(r'"(\w+)"', rule.group(1))
    assert refused == ["longform", "long_clips", "sport", "pipeline", "rate", "edit"]
    turned_off = {"longform": "setLongform(false)", "long_clips": "setLongClips(false)", "sport": "setSport(null)",
                  "pipeline": "setPipeline(null)", "rate": "setStepsKept(false)", "edit": "setEditKept(false)"}
    settings = _source("components/queue/QueueItemSettings.tsx")
    whole = settings[settings.index("'Whole video',"):]
    whole = whole[: whole.index("'Last time the whole video became one clip")]
    for key in refused:
        assert turned_off[key] in whole, key
    # Each switch that turns one of them on turns Whole video off.
    for label in ("'60s+',", "'Longform',", "'Sports',", "'Pipeline',", "'Rate & understand',", "'Suggest edits',"):
        handler = settings[settings.index(label):]
        handler = handler[: handler.index("\n        {")]       # up to the next switch
        assert "wholeOff()" in handler, label
    # And Vertical Live's choice of a sport.
    sport = settings[settings.index("if (e.target.value.startsWith('sport:')) {"):]
    assert "wholeOff()" in sport[: sport.index("} else {")]


def test_a_whole_video_switched_off_is_posted_without_it(tmp_path):
    start = {"whole": True, "captions": False, "caption_style": STYLE}
    off = {"captions": False, "caption_style": STYLE,
           "clear": ["whole", "long_clips", "podcast", "vertical_live", "gaming_scoring", "gaming", "gaming_layout",
                     "gaming_remember", "sport", "longform", "pipeline", "watermark_profile_id"]}
    kept = {**off, "whole": True, "clear": off["clear"][1:]}
    got = _run(tmp_path, "return [m.remakeOptions(data.start, data.off), m.remakeOptions(data.start, data.kept)]",
               {"start": start, "off": off, "kept": kept})
    assert got == [{"captions": False, "caption_style": STYLE}, start]


# ---- what a finished run did ---------------------------------------------------------------------


def test_what_a_finished_run_did_is_said_in_plain_words(tmp_path):
    """lib/remake.ts remakeResultText, from the job's result (core/remake.py
    summary): a run that changed nothing says so (black-box S3: its clips
    from before still there, it looked as if nothing had run)."""
    cases = [
        ({"made": 0, "new": 0, "again": 0, "removed": 0, "replaced": 0, "kept": 1, "unchanged": 1},
         "No new clips this time, so your clips are kept as they were."),
        ({"made": 0, "new": 0, "again": 0, "removed": 0, "replaced": 0, "kept": 0, "unchanged": 0},
         "No new clips this time."),
        ({"made": 15, "new": 15, "again": 0, "removed": 7, "replaced": 7, "kept": 2, "unchanged": 2},
         "15 new · 7 removed · 2 kept as they were"),
        ({"made": 1, "new": 0, "again": 1, "removed": 0, "replaced": 0, "kept": 1, "unchanged": 0},
         "1 made again with the new settings"),
        ({"made": 2, "new": 2, "again": 0, "removed": 0, "replaced": 0, "kept": 3, "unchanged": 3,
          "renders_failed": 1},
         "Some clips couldn’t be made, so nothing was replaced · 2 new · 3 kept as they were"),
        ({"made": 3, "new": 3, "again": 1, "removed": 2, "replaced": 3, "error": True},
         "3 new · 1 made again with the new settings · 2 removed · the rest kept, as they couldn’t be replaced"),
    ]
    data = [result for result, _ in cases]
    assert _run(tmp_path, "return data.map((r) => m.remakeResultText(r))", data) == [said for _, said in cases]
    # Every word goes through the screen's t().
    said = _run(tmp_path, "const said = []; data.forEach((r) => m.remakeResultText(r, (s) => { said.push(s);"
                          " return s })); return said", data)
    assert set(said) == {"No new clips this time, so your clips are kept as they were.", "No new clips this time.",
                         "new", "removed", "kept as they were", "made again with the new settings",
                         "Some clips couldn’t be made, so nothing was replaced",
                         "the rest kept, as they couldn’t be replaced"}


def test_the_result_type_holds_what_the_engine_says():
    """lib/types.ts RemakeResult has every key core/remake.py summary() can
    give: one missing there would never be shown."""
    types = _source("lib/types.ts")
    body = re.search(r"export interface RemakeResult \{(.*?)\n\}", types, re.S)
    assert body
    keys = set(re.findall(r"^  (\w+)\??:", body.group(1), re.M))
    assert keys == set(remake._SUMMED) | {"removed", "error"}
    assert "result?: RemakeResult" in types[types.index("export interface QueueJob {"):]
    assert "result?: RemakeResult" in types[types.index("export interface StudioEvent {"):]


def test_the_queue_row_says_what_it_did():
    item = _source("components/queue/QueueItem.tsx")
    start = item.index("{job.status === 'done' && job.result && (")
    row = item[start: item.index("\n          )}", start) + len("\n          )}")]
    assert "{t('Make clips again:')} {remakeResultText(job.result, t)}" in row
    assert untranslated(f"const A = () => (<div>{row}</div>)") == []


def test_process_again_in_add_videos_is_as_it_was():
    form = _source("components/queue/AddVideos.tsx")
    assert ("{t('Make clips again with the settings you chose? Existing clips are kept - new ones are added "
            "alongside them.')}") in form
    assert "onClick={() => generate(true, alreadyDone)}" in form and "{t('Process again')}" in form
    assert "remake" not in form.lower()
