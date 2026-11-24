"""Word-synced caption generation.

Builds an ASS subtitle file for one clip, short word groups that FFmpeg burns
in during the clip's final encode.

Captions look exactly as Clips Kitty Editor draws them: a caption style
names one of the editor's text presets, a size and a place (Small, Medium,
Large; Bottom, Middle, Top, as the editor's Captions sheet has them), and
video/caption_presets.py turns that into the ASS style: the editor's font
file, em, outline, shadow, plate and place.

Falls back to spreading a segment's words evenly across its duration when
word-level timestamps are missing (transcripts cached before they were
enabled).
"""

import math
import re
from pathlib import Path

from core.models import ClipCandidate, Segment
from video import alphabets, caption_fit
from video import caption_presets as presets
from video import fonts as bundled

# preset, size, position, words_per_caption, second_speaker(+colour), safe_zone
DEFAULT_STYLE = presets.DEFAULT

# The area each app covers with its own buttons and text, in px on a
# 1080x1920 Short: the bottom bar (name, description, sound) and the column of
# buttons down the right. The same measured guidance values as the Gaming
# layout editor's (gaming/layouts.json, docs/GAMING.md: measured September
# 2026 on each app's feed, kreatli.com's safe zone checkers); a test keeps the
# two the same. Guidance, not a promise: the apps change by device and by
# how long the post's description is.
SAFE_ZONES = presets.SAFE_ZONES
SAFE_ZONE_CHOICES = presets.SAFE_ZONE_CHOICES


def script_of(language: str | None) -> str:
    """The writing system a language's captions need (video/alphabets.py)."""
    return alphabets.script_of(language)


def caption_font_for(language: str, chosen: str | None) -> str | None:
    """The font captions should actually burn with: `chosen` (a family of
    the editor's), unless the content language needs letters that file does
    not have; then the font the editor's own preset for that language uses
    (caption_presets.font_for_language). With no `chosen` (the hook title,
    GET /languages): the language's own font when it needs one, else None."""
    lang = alphabets.base_language((language or "en").lower())
    if chosen is None:
        if lang in presets.LANGUAGE_FONTS:
            return presets.LANGUAGE_FONTS[lang]
        return presets.WINDOWS_FALLBACK if alphabets.letters_for(lang) is None else None
    return presets.font_for_language(chosen, lang)

# Captions clear in the quiet parts of a clip (#228). A caption ends at a
# pause: when nothing is said for longer than PAUSE_SECONDS, the next word
# starts a new caption, however few words the last one has. And no word
# counts as spoken for longer than LONGEST_WORD_SECONDS: a transcript now and
# then stretches a short word's end across the silence after it, which would
# hide the pause and hold the caption up. Measured on real speech (a median
# word 0.18 s, 95 in 100 under 0.6 s): a breath between sentences stays under
# the pause, and a word said slowly on purpose stays under the cap.
# Lines a person saved keep their own times.
PAUSE_SECONDS = 0.8
LONGEST_WORD_SECONDS = 1.2
# A transcript also times a word with no length now and then. Alone beside a
# pause it has no caption to ride in, so it is on screen this long, in the
# silence after it (less than the pause, so it is gone before the next word).
ALONE_SECONDS = 0.4


def build_caption_lines(
    segments: list[Segment],
    candidate: ClipCandidate,
    words_per_caption: int = 3,
    turns: list | None = None,
) -> list[dict]:
    """The caption lines for one clip as editable data:
    [{"start", "end", "text"}] with times relative to the clip start.
    This is what the caption editor in the UI shows and what users correct.

    `turns` (analysis/voice_turns.py) is where someone other than the main
    speaker talks, [[start, end, speaker], ...] in clip seconds. With it a
    line ends where the speaker changes, so no caption mixes two people's
    words, and the other speaker's lines carry "speaker"."""
    words = _words_in_window(segments, candidate.start, candidate.end)
    size = max(1, int(words_per_caption))
    lines = []
    heard = bool(_turns(turns))
    captions: list[tuple[int, list[dict]]] = []
    for speaker, run in _by_speaker(words, turns, candidate.start, candidate.end):
        groups = _grouped(run, size, candidate.start, candidate.end)
        if heard:
            groups = _standing(groups, size, candidate.start, candidate.end)
        captions.extend((speaker, group) for group in groups)
    said: list[dict] = []       # the clip's words as the editor is given them, when they are needed
    for i, (speaker, group) in enumerate(captions):
        start = max(0.0, group[0]["start"] - candidate.start)
        closing = group[-1:]
        if heard:
            # A short word at the end can be timed inside the word before
            # it: the caption stays up until that word has been said.
            stands = [k for k, w in enumerate(group) if _stands(w, candidate.start, candidate.end)]
            closing = group[stands[-1] if stands else 0:]
        end = min(candidate.duration, max(w["end"] for w in closing) - candidate.start)
        # Up until its last word has been said (_said: no word for longer
        # than LONGEST_WORD_SECONDS), so it is gone in the silence after.
        until = max(_said(w, candidate.start, candidate.end)[1] for w in closing)
        after = captions[i + 1][1] if i + 1 < len(captions) else None
        beside = _beside_a_pause(captions[i - 1][1] if i else None, group, after, candidate.start, candidate.end)
        alone = _alone(group, after, candidate.start, candidate.end) if beside else 0.0
        if alone:
            end = until = alone
        if end <= start:
            continue
        line = {"start": round(start, 2), "end": until, "text": " ".join(w["word"] for w in group)}
        if heard and beside and not any(_stands(w, candidate.start, candidate.end) for w in group):
            # A pause made it a caption of its own, and no word in it is long
            # enough to say whose it is: by the clock, as the same line is
            # marked when the editor sends it back.
            said = said or clip_words(words, candidate.start, candidate.end)
            line = tag_lines([line], turns, said)[0]
        elif speaker:
            line["speaker"] = speaker
        lines.append(line)
    return lines


def tag_lines(lines: list[dict], turns: list | None, words: list[dict] | None = None) -> list[dict]:
    """Caption lines marked with who says them: "speaker" on a line that is
    mostly the other speaker's, and on no other. For lines the user saved:
    their text and their grouping stay as they made them.

    `words` are the clip's words (clip_words). With them a line goes by who
    says the words in it, each counted for as long as it is spoken, the same
    answer build_caption_lines gives word by word; a pause inside a caption
    then decides nothing. Without them, or for a line with no word in it to
    go by, it goes by who talks through more of the line's time.

    The words that count are the ones long enough to stand alone (a shorter
    one is said with a word beside it, which may be in another line), each in
    the line its middle falls in, from the line's start up to its end. That
    is the stretch a turn covers, so what the editor says about a caption's
    stretch (its fix for a saved caption) always decides that caption."""
    spans = _turns(turns)
    voices = []     # each word that counts: its middle, how long it is spoken, who says it
    for w in words or ():
        said_from, said_to = in_clip(w, 0.0, float("inf"))
        if said_to - said_from > _TINY:
            middle = (said_from + said_to) / 2
            voices.append((middle, said_to - said_from, next((who for a, b, who in spans if a <= middle < b), 0)))
    tagged = []
    for line in lines:
        line = {k: v for k, v in line.items() if k != "speaker"}
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            tagged.append(line)
            continue
        held: dict[int, float] = {}
        whole = 0.0
        for middle, spoken, speaker in voices:
            if start <= middle < end:
                whole += spoken
                if speaker:
                    held[speaker] = held.get(speaker, 0.0) + spoken
        if whole <= 0:
            # No word to go by: the stretch of time.
            held, whole = {}, end - start
            for turn_start, turn_end, speaker in spans:
                shared = min(end, turn_end) - max(start, turn_start)
                if shared > 0:
                    held[speaker] = held.get(speaker, 0.0) + shared
        if held:
            speaker = max(held, key=held.get)
            if held[speaker] > whole / 2:
                line["speaker"] = speaker
        tagged.append(line)
    return tagged


def words_of(segments: list[Segment], candidate: ClipCandidate) -> list[dict]:
    """A clip's words as the editor is given them (GET /clips/{id}/words):
    the transcript's timed words, in its order. Saved lines are marked by
    these on both sides, so the editor shows the colour the render burns. A
    segment without word timings has none to give; its lines go by the clock."""
    return clip_words((w for seg in segments for w in seg.words or ()), candidate.start, candidate.end)


def _turns(turns: list | None) -> list[tuple[float, float, int]]:
    """Speaker turns as (start, end, speaker). They are read back from a
    clip's saved options, so anything that is not a turn is passed over."""
    out = []
    for turn in turns if isinstance(turns, (list, tuple)) else ():
        try:
            start, end = float(turn[0]), float(turn[1])
            speaker = int(turn[2]) if len(turn) > 2 else 1
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start and speaker > 0:
            out.append((start, end, speaker))
    return out


def _by_speaker(
    words: list[dict], turns: list | None, start: float, end: float
) -> list[tuple[int, list[dict]]]:
    """Words in runs of one speaker: (0 for the main speaker, said). `start`
    and `end` are the clip's, in the words' own time."""
    spans = _turns(turns)
    if not spans:
        return [(0, words)]
    runs: list[tuple[int, list[dict]]] = []
    for w, speaker in zip(words, _speakers(words, spans, start, end)):
        if runs and runs[-1][0] == speaker:
            runs[-1][1].append(w)
        else:
            runs.append((speaker, [w]))
    return runs


# A word this short has no room for a caption of its own (one of no length
# would not be burned at all): it goes with the word beside it.
_TINY = 0.05
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")   # as transcription/cloud.py ends one


def in_clip(word: dict, start: float, end: float) -> tuple[float, float]:
    """When a word is said, in clip seconds: cut to the clip and rounded to a
    hundredth. These are the numbers the editor is given for it (clip_words),
    so whoever is asked "who says this word" is asked about the same word."""
    return round(max(0.0, word["start"] - start), 2), round(min(end - start, word["end"] - start), 2)


def _said(word: dict, start: float, end: float) -> tuple[float, float]:
    """When a word counts as spoken, in clip seconds: as in_clip has it, but
    for no longer than LONGEST_WORD_SECONDS after it starts (in the clip: a
    word the clip's start cuts through counts from there). Text without word
    timings, spread evenly over its segment, is left as long as it was
    spread: nothing was stretched there."""
    said_from, said_to = in_clip(word, start, end)
    if word.get("spread"):
        return said_from, said_to
    return said_from, min(said_to, round(said_from + LONGEST_WORD_SECONDS, 2))


def _quiet(before: list[dict], after: list[dict], start: float, end: float) -> bool:
    """Whether a pause sits between two captions' words: the first have been
    said and the others start more than PAUSE_SECONDS later."""
    until = max(_said(w, start, end)[1] for w in before)
    return round(_said(after[0], start, end)[0] - until, 2) > PAUSE_SECONDS


def _beside_a_pause(
    before: list[dict] | None, group: list[dict], after: list[dict] | None, start: float, end: float
) -> bool:
    """Whether a caption's words have a pause before or after them. `before`
    and `after` are the words of the captions either side of it."""
    return (before is not None and _quiet(before, group, start, end)) or (
        after is not None and _quiet(group, after, start, end))


def _alone(group: list[dict], after: list[dict] | None, start: float, end: float) -> float:
    """Until when a caption beside a pause is on screen when it has no room
    of its own, in clip seconds; 0 for every other caption. Its words take
    no longer than _TINY to say (a transcript times a word with no length
    now and then): it gets ALONE_SECONDS, or as much of that as there is
    before the next caption or the clip's end. Away from a pause such words
    are as they always were (a caption of no length is not burned)."""
    said_from = _said(group[0], start, end)[0]
    if max(_said(w, start, end)[1] for w in group) - said_from > _TINY:
        return 0.0
    until = min(round(end - start, 2), round(said_from + ALONE_SECONDS, 2))
    if after is not None:
        until = min(until, _said(after[0], start, end)[0])
    return until if until > said_from else 0.0


def clip_words(words, start: float, end: float) -> list[dict]:
    """The words said in a clip as the editor's transcript shows them:
    [{"start", "end", "word"}] in clip seconds. `words` is every word of the
    video, in the transcript's order and time."""
    out = []
    for w in words:
        if w["end"] > start and w["start"] < end:
            said_from, said_to = in_clip(w, start, end)
            out.append({"start": said_from, "end": said_to, "word": w["word"]})
    return out


def _speakers(words: list[dict], spans: list[tuple[float, float, int]], start: float, end: float) -> list[int]:
    """Who says each word: the speaker of the turn its middle falls in, 0
    (the main speaker) outside every turn. A word too short to stand alone
    is said by whoever says the word it leans on."""
    when = [in_clip(w, start, end) for w in words]
    own = [next((who for a, b, who in spans if a <= (said_from + said_to) / 2 < b), 0) for said_from, said_to in when]
    return [own[on] if on >= 0 else 0 for on in _leans(words, when)]


def _leans(words: list[dict], when: list[tuple[float, float]]) -> list[int]:
    """For each word, the word that decides who says it: itself, or for a
    word too short to stand alone one of the two words its run of short words
    sits between (-1 in a clip of nothing but short words).

    Words up to the last one in the run that ends a sentence finish what the
    speaker before them was saying; the rest open what comes next. With no
    full stop to go by, in the run or on the word before it, the run goes
    with the nearer of the two words, the one after when they are as near."""
    count = len(words)
    short = [said_to - said_from <= _TINY for said_from, said_to in when]
    on = [-1 if tiny else i for i, tiny in enumerate(short)]
    i = 0
    while i < count:
        if not short[i]:
            i += 1
            continue
        j = i
        while j < count and short[j]:
            j += 1                                  # the run is words i to j - 1
        before, after = i - 1, j
        if before < 0 and after >= count:
            part = None
        elif before < 0:
            part = i
        elif after >= count:
            part = j
        else:
            ends = [k for k in range(i, j) if _ends_sentence(words[k])]
            if ends:
                part = ends[-1] + 1
            elif _ends_sentence(words[before]):
                part = i
            else:
                part = j if when[i][0] - when[before][1] < when[after][0] - when[j - 1][1] else i
        if part is not None:
            on[i:part] = [before] * (part - i)
            on[part:j] = [after] * (j - part)
        i = j
    return on


def _ends_sentence(word: dict) -> bool:
    return bool(_SENTENCE_END.search(str(word.get("word") or "").strip()))


def _stands(word: dict, start: float, end: float) -> bool:
    """Whether a word is long enough to stand alone."""
    said_from, said_to = in_clip(word, start, end)
    return said_to - said_from > _TINY


def _standing(groups: list[list[dict]], size: int, start: float, end: float) -> list[list[dict]]:
    """What one person says, in captions that hold a word long enough to
    stand alone. A group of nothing but shorter words goes in the caption
    before it (the first of them in the one after): alone it would be on
    screen for an instant, or not burned at all, and its colour would be
    decided by a word in another caption.

    A caption takes at most three words more than are set for one. A
    transcript can squeeze a whole burst of words into one instant; the rest
    of those stay as they are without the option.

    Never across a pause, which would keep the caption up through it: each
    stretch between two pauses is done by itself, and short words alone in
    one are a caption together (_alone has it on screen in the silence)."""
    stretches = [groups[:1]]
    for before, group in zip(groups, groups[1:]):
        if _quiet(before, group, start, end):
            stretches.append([group])
        else:
            stretches[-1].append(group)
    if len(stretches) < 2:
        return _stood(groups, size, start, end)
    out: list[list[dict]] = []
    for stretch in stretches:
        if any(_stands(w, start, end) for group in stretch for w in group):
            out.extend(_stood(stretch, size, start, end))
        else:
            short = [w for group in stretch for w in group]
            out.extend(short[i:i + size + 3] for i in range(0, len(short), size + 3))
    return out


def _stood(groups: list[list[dict]], size: int, start: float, end: float) -> list[list[dict]]:
    """_standing for words with no pause between them."""
    limit = size + 3
    out: list[list[dict]] = []
    waiting: list[list[dict]] = []      # short groups in front of the first caption
    seen = room = False                 # a caption is made; the last one can still take words
    for group in groups:
        if any(_stands(w, start, end) for w in group):
            taken: list[dict] = []
            while waiting and len(waiting[-1]) + len(taken) + len(group) <= limit:
                taken = waiting.pop() + taken
            out.extend(waiting)
            out.append(taken + group)
            waiting = []
            seen = room = True
        elif not seen:
            waiting.append(group)
        elif room and len(out[-1]) + len(group) <= limit:
            out[-1] = out[-1] + group
        else:
            out.append(group)
            room = False
    return out if seen else groups      # nothing but short words: as they were


def paint_turns(turns: list | None, edits: list | None) -> list[list]:
    """The turns to caption by: what the render heard (`turns`), with what a
    person said in the editor laid over it.

    `edits` (a clip's saved speaker_edits) is [[start, end, speaker], ...] in
    clip seconds, speaker 0 for the main speaker and 1 for the other. Each is
    a statement about that stretch, whatever was heard there, and a later one
    overrules an earlier one."""
    held = list(_turns(turns))
    for said_from, said_to, speaker in _statements(edits):
        cut: list[tuple[float, float, int]] = []
        for a, b, who in held:
            if a < said_from:
                cut.append((a, min(b, said_from), who))
            if b > said_to:
                cut.append((max(a, said_to), b, who))
        held = cut
        if speaker:
            held.append((said_from, said_to, speaker))
    painted: list[list] = []
    for a, b, who in sorted(held):
        if painted and painted[-1][2] == who and a <= painted[-1][1]:
            painted[-1][1] = max(painted[-1][1], b)     # one turn, said in two pieces
        else:
            painted.append([a, b, who])
    # Not rounded: a statement about one of two words said at once ends
    # between their middles, which can be finer than a hundredth.
    return painted


def shift_edits(edits: list | None, old_start: float, new_start: float) -> list[list]:
    """A clip's speaker_edits carried to the clip after its start moved. They
    are only moved: one that now lies outside the clip says nothing there,
    and is back in place if the clip grows again."""
    moved = old_start - new_start
    # Kept finer than a hundredth, as paint_turns keeps them: an edge between
    # the middles of two words said at once must stay between them.
    return [[round(a + moved, 4), round(b + moved, 4), who] for a, b, who in _statements(edits)]


def _statements(edits: list | None) -> list[tuple[float, float, int]]:
    """A clip's speaker_edits as (start, end, speaker). Unlike a turn, a
    statement can name the main speaker (0) and can lie outside the clip.
    Read back from saved options, so anything else is passed over."""
    out = []
    for edit in edits if isinstance(edits, (list, tuple)) else ():
        try:
            start, end, speaker = float(edit[0]), float(edit[1]), int(edit[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start and speaker >= 0:
            out.append((start, end, 1 if speaker else 0))
    return out


# Saved line times are rounded to a hundredth, and a word often starts on the
# very instant the one before it ends.
_EDGE = 0.006


def refit_caption_lines(
    lines: list[dict],
    segments: list[Segment],
    old_start: float,
    new_start: float,
    new_end: float,
    words_per_caption: int = 3,
) -> list[dict]:
    """Saved caption lines carried to a clip whose start or end has moved.

    Saved lines hold the user's corrected text, timed from the clip start they
    were saved for (`old_start`). Burned in untouched after the clip grew, they
    left the added seconds with no captions on every later render, and a moved
    start put all of them out of sync (#120). So the lines shift with the
    start, the ones the clip no longer holds are dropped, and the stretch
    before the first and after the last is captioned from the transcript. A
    line the user blanked still covers its time, and stays blank."""
    duration = new_end - new_start
    shift = old_start - new_start
    kept = []
    for line in lines:
        try:
            start, end = float(line["start"]) + shift, float(line["end"]) + shift
        except (KeyError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(duration, end)
        if end > start:
            kept.append({**line, "start": round(start, 2), "end": round(end, 2)})
    if not kept:
        return build_caption_lines(
            segments, ClipCandidate(start=new_start, end=new_end, score=0), words_per_caption
        )
    kept.sort(key=lambda l: l["start"])

    # A word the saved lines already hold is never said twice: one cut by the
    # old edge of the clip belongs to the saved line it was in. The head is
    # what the old start left out. The tail goes by where the saved lines
    # end, not by the old end of the clip, so a clip that grew before this
    # existed gets its missing captions back too.
    last = new_start + kept[-1]["end"]
    words = _words_in_window(segments, new_start, new_end)
    head = [w for w in words if w["end"] <= old_start]
    tail = [w for w in words if w["start"] >= last - _EDGE]
    size = max(1, int(words_per_caption))
    added = []
    made = [_grouped(head, size, new_start, new_end), _grouped(tail, size, new_start, new_end)]
    for groups in made:
        for i, group in enumerate(groups):
            start = max(0.0, group[0]["start"] - new_start)
            end = min(duration, group[-1]["end"] - new_start)
            until = _said(group[-1], new_start, new_end)[1]
            after = groups[i + 1] if i + 1 < len(groups) else None
            beside = _beside_a_pause(groups[i - 1] if i else None, group, after, new_start, new_end)
            alone = _alone(group, after, new_start, new_end) if beside else 0.0
            if alone:
                end = until = alone
            if end > start:
                added.append(
                    {"start": round(start, 2), "end": until, "text": " ".join(w["word"] for w in group)}
                )
    return sorted(added + kept, key=lambda l: l["start"])


def build_captions(
    segments: list[Segment],
    candidate: ClipCandidate,
    output_path: Path,
    style: dict | None = None,
    lines: list[dict] | None = None,
    canvas: tuple[int, int] = (1080, 1920),
    language: str = "en",
) -> Path | None:
    """Write an ASS file with times relative to the clip start.
    `lines` (user-corrected caption text) overrides the generated ones.
    `canvas` is the output frame (default portrait Shorts; longform passes
    1920x1080 and the style scales to it). Returns the path, or None if
    there is nothing to caption."""
    opts = presets.normalize(style)
    look = presets.resolve(opts, canvas, language)
    if bundled.is_bundled(look["fontname"]) and not bundled.usable(look["fontname"]):
        # Never a failed render over a font: Arial, and a line in the job log.
        missing = " (the app's own caption fonts are missing)" if bundled.folder() is None else ""
        print(f"      The caption font {look['fontname']} is missing or damaged in this copy of the app, "
              f"so the captions use Arial{missing}")
        look = {**look, "fontname": "Arial", "fontsize": max(1, round(look["em"] * 1.117)), "bold": True}
    if lines is None:
        lines = build_caption_lines(segments, candidate, opts["words_per_caption"])

    # The other speaker's lines (build_caption_lines, tag_lines) in their own
    # colour. Only with the option on: a "speaker" left on a saved line by an
    # earlier render never colours a plain one.
    second = _inline_color(opts["second_speaker_color"]) if opts.get("second_speaker") else None

    # Every caption is placed where the editor puts a title: its block's
    # centre at the sheet's place, with the editor's shadow.
    place = f"\\an5\\pos({look['x']},{look['y']})"
    if look["shadow"]:
        place += f"\\xshad{_num(look['shadow'][0])}\\yshad{_num(look['shadow'][1])}"

    zoned = _zoned(opts, canvas)
    dialogue = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue  # a blanked-out line deletes that caption
        text = text.replace("\\", "").replace("{", "").replace("}", "")  # ASS control chars
        start, end = float(line["start"]), float(line["end"])
        if end <= start:
            continue
        tint = f"\\1c{second}" if second and line.get("speaker") else ""
        if zoned:
            # What is measured must be what is drawn (_spaced).
            text = _spaced(text)
            if not text:
                continue
        # A safe zone: a word too wide for the safe area shrinks its caption.
        fit = _fit_scale(text, look, canvas) if zoned else None
        dialogue.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{_EVENT_TEXT}"
                        f"{{{_scale_tag(fit)}{place}{tint}}}{text}")

    if not dialogue:
        return None
    output_path.write_text(_header(look, canvas) + "\n".join(dialogue) + "\n", encoding="utf-8")
    return output_path


_EVENT_TEXT = "Default,,0,0,0,,"

# Characters with no width and no ink that are not spaces to Python either:
# zero-width space, word joiner, byte-order mark, Mongolian vowel separator.
# (The zero-width joiner and non-joiner stay: they shape Arabic, Indic
# scripts and emoji.)
_NO_WIDTH = dict.fromkeys((0x200B, 0x2060, 0xFEFF, 0x180E))


def _zoned(opts: dict, canvas: tuple[int, int]) -> bool:
    """Whether a safe zone applies: one is chosen and the clip is portrait."""
    name = opts.get("safe_zone")
    return isinstance(name, str) and name in SAFE_ZONES and canvas[1] > canvas[0]


def _spaced(text: str) -> str:
    """A caption's text with single ordinary spaces between its words, as
    it is written to the event when a safe zone is on.

    libass draws the text as written and breaks a line only at an ordinary
    space (U+0020): two spaces are twice as wide, and words joined by a
    no-break space (U+00A0, U+202F, what French puts before ? ! : ;) are one
    unbreakable run. The measuring behind the safe zones (caption_fit) works
    on words with one space between them, so the event gets exactly that
    text: every run of spaces of any kind becomes one ordinary space, the
    ends are trimmed and characters with no width are dropped. A caption
    with no safe zone never comes here."""
    return " ".join(text.translate(_NO_WIDTH).split())


def _fit_scale(text: str, look: dict, canvas: tuple[int, int]) -> int | None:
    """With a safe zone, the size (in %) that brings a caption's widest word
    inside the safe area, when that word is wider than the space between
    the margins (libass breaks lines only at spaces, so such a word would
    run under the app's buttons; a caption in a language written without
    spaces, Chinese, Japanese or Thai, is one word). None when the caption
    fits, and when the font or a character cannot be measured
    (caption_fit.widest_word: Hindi, Arabic, an emoji): that caption is
    written as it always was."""
    room = canvas[0] - 2 * look["margin"] - 1
    widest = caption_fit.widest_word(text, look["fontname"], look["fontsize"], look["bold"])
    if not widest or widest <= room:
        return None
    return max(1, math.floor(100 * room / widest))


def _scale_tag(fit: int | None) -> str:
    """The override that draws a caption at `fit` % of its size ("" for none)."""
    return f"\\fscx{fit}\\fscy{fit}" if fit else ""


def _num(value: float) -> str:
    """A tag's number: whole numbers as they are, others to 2 decimals."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _header(look: dict, canvas: tuple[int, int] = (1080, 1920)) -> str:
    """The ASS file's head: one style, the editor's look (caption_presets.resolve).
    The shadow is on each event (\\xshad, \\yshad), so the style has none;
    a plate preset is BorderStyle 3, its plate the outline colour."""
    bold = -1 if look["bold"] else 0
    italic = -1 if look["italic"] else 0
    margin = look["margin"]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas[0]}
PlayResY: {canvas[1]}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{look['fontname']},{look['fontsize']},{look['primary']},{look['primary']},{look['outline_colour']},{look['back']},{bold},{italic},0,0,100,100,{_num(look['spacing'])},0,{look['border_style']},{_num(look['outline'])},0,5,{margin},{margin},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR (ASS stores colours little-endian)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _inline_color(hex_rgb: str) -> str:
    """#RRGGBB -> &HBBGGRR& — the form an inline \\1c override takes."""
    h = (hex_rgb or "").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def _words_in_window(segments: list[Segment], start: float, end: float) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        if seg.words:
            words.extend(w for w in seg.words if w["end"] > start and w["start"] < end)
        else:
            words.extend(_spread_evenly(seg, start, end))
    return sorted(words, key=lambda w: w["start"])


def _spread_evenly(seg: Segment, start: float, end: float) -> list[dict]:
    tokens = seg.text.split()
    if not tokens:
        return []
    step = (seg.end - seg.start) / len(tokens)
    return [
        {"start": seg.start + i * step, "end": seg.start + (i + 1) * step, "word": tok, "spread": True}
        for i, tok in enumerate(tokens)
        if seg.start + (i + 1) * step > start and seg.start + i * step < end
    ]


def _grouped(words: list[dict], size: int, start: float, end: float) -> list[list[dict]]:
    """Words in captions of `size`, a new one after every pause: when the
    words so far have been said (_said) and the next starts more than
    PAUSE_SECONDS later. `start` and `end` are the clip's, in the words' own
    time. A word of no length before a pause is left alone by it too: its
    caption is on screen in the silence after it (_alone)."""
    groups: list[list[dict]] = []
    until = 0.0                 # how far the caption being filled has been said
    for w in words:
        said_from, said_to = _said(w, start, end)
        paused = round(said_from - until, 2) > PAUSE_SECONDS
        if groups and len(groups[-1]) < size and not paused:
            groups[-1].append(w)
            until = max(until, said_to)
        else:
            groups.append([w])
            until = said_to
    return groups


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
