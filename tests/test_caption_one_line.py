"""One line per caption by default (editor suite step KG-link).

Captions were cut from the transcript in groups of words_per_caption words.
At the large preset sizes three long words are wider than the caption box,
so libass wrapped them two over one. Now a caption whose words would not
stay on one line in its box (the preset's font at its size, inside the
width between the margins, measured with the same font files the burn
uses) shows fewer words, down to one, and the rest start the next caption.
words_per_caption stays the most. Text that cannot be measured keeps the
word count alone. The same lines go to the burn and the editor's titles.

Standard library only: these run in CI's light install.
"""

from core.models import ClipCandidate, Segment
from video import caption_fit
from video import caption_presets as presets
from video.captions import _fit_for, build_caption_lines, build_captions, line_fit, refit_caption_lines

LONG = "unbelievable extraordinary performance"
SHORT = "I am here"


def _segments(text: str, start: float = 0.0, step: float = 0.3) -> list[Segment]:
    words = text.split()
    timed = [{"start": start + i * step, "end": start + (i + 1) * step - 0.02, "word": w} for i, w in enumerate(words)]
    return [Segment(start=start, end=start + len(words) * step, text=text, words=timed)]


def _clip(segments) -> ClipCandidate:
    return ClipCandidate(start=0.0, end=max(s.end for s in segments) + 0.5, score=1.0)


def _look(style=None, canvas=(1080, 1920)):
    return presets.resolve(presets.normalize(style), canvas, "en")


def _one_line_in_the_box(text: str, style=None, canvas=(1080, 1920)) -> bool:
    """What libass would show: caption_fit.lines at the resolved look."""
    look = _look(style, canvas)
    return caption_fit.lines(text, look["fontname"], look["fontsize"], look["bold"],
                             canvas[0] - 2 * look["margin"]) == [text]


def test_the_default_font_is_the_bundled_file_the_burn_uses():
    look = _look()
    assert presets.bundled.face(look["family"], 900) is not None
    assert caption_fit.font(look["fontname"]) is not None


def test_three_long_words_are_one_word_per_caption_never_two_over_one():
    segments = _segments(LONG)
    plain = build_caption_lines(segments, _clip(segments), 3)
    assert [l["text"] for l in plain] == [LONG]
    assert not _one_line_in_the_box(LONG)             # the old caption wrapped
    lines = build_caption_lines(segments, _clip(segments), 3, fit=line_fit(None))
    assert [l["text"] for l in lines] == LONG.split()
    for line in lines:
        assert _one_line_in_the_box(line["text"])
    # Each caption up while its word is said, as before.
    assert [l["start"] for l in lines] == [0.0, 0.3, 0.6]


def test_three_short_words_stay_one_caption():
    segments = _segments(SHORT)
    lines = build_caption_lines(segments, _clip(segments), 3, fit=line_fit(None))
    assert [l["text"] for l in lines] == [SHORT]


def test_words_per_caption_is_still_the_most():
    text = "a b c d e f g"
    segments = _segments(text)
    lines = build_caption_lines(segments, _clip(segments), 5, fit=line_fit(None))
    assert [l["text"] for l in lines] == ["a b c d e", "f g"]


def test_every_generated_line_stays_on_one_line_at_each_size():
    text = ("so we were walking down the street and then this absolutely enormous dog came running "
            "towards us out of nowhere unbelievable honestly")
    segments = _segments(text)
    for size in ("small", "medium", "large"):
        style = {"size": size}
        lines = build_caption_lines(segments, _clip(segments), 3, fit=line_fit(style))
        assert " ".join(l["text"] for l in lines) == text
        for line in lines:
            words = line["text"].split()
            assert 1 <= len(words) <= 3
            assert len(words) == 1 or _one_line_in_the_box(line["text"], style), (size, line)


def test_a_script_that_cannot_be_measured_keeps_the_word_count():
    text = "यह बहुत अच्छा खेल था"
    segments = _segments(text)
    fit = line_fit(None, (1080, 1920), "hi")
    assert fit("यह बहुत अच्छा") is True
    lines = build_caption_lines(segments, _clip(segments), 3, fit=fit)
    assert lines == build_caption_lines(segments, _clip(segments), 3)


def test_no_fit_is_the_old_grouping():
    segments = _segments(LONG + " " + SHORT)
    assert [l["text"] for l in build_caption_lines(segments, _clip(segments), 3)] == [LONG, SHORT]


def test_a_close_call_counts_as_not_fitting():
    look = _look()
    face = caption_fit.font(look["fontname"])
    m = caption_fit.Measure(face, look["fontsize"], look["bold"])
    glyphs = m.glyphs("performance")
    width = caption_fit._span(glyphs, 0, len(glyphs) - 1)
    assert caption_fit.one_line("performance", look["fontname"], look["fontsize"], look["bold"], width + 1) is True
    assert caption_fit.one_line("performance", look["fontname"], look["fontsize"], look["bold"], width) is False
    assert caption_fit.one_line("performance", look["fontname"], look["fontsize"], look["bold"], width + 50,
                                spacing=6.0) is False                   # 10 gaps of 6 px of letter spacing
    assert caption_fit.one_line("performance", "No Such Font", 90, False, 9999) is None


def test_the_refit_head_and_tail_follow_the_same_rule():
    segments = _segments(LONG + " " + SHORT + " " + LONG)        # 0-0.9, 0.9-1.8, 1.8-2.7
    saved = [{"start": 0.0, "end": 0.9, "text": "kept as it was saved"}]
    # Saved for a clip starting at 0.9; it now starts at 0 and ends at 3:
    # the first LONG is the head, the second the tail.
    out = refit_caption_lines(saved, segments, 0.9, 0.0, 3.0, 3, fit=line_fit(None))
    assert [l["text"] for l in out] == [*LONG.split(), "kept as it was saved", *LONG.split()]
    assert out[3] == {"start": 0.9, "end": 1.8, "text": "kept as it was saved"}
    plain = refit_caption_lines(saved, segments, 0.9, 0.0, 3.0, 3)
    assert [l["text"] for l in plain] == [LONG, "kept as it was saved", LONG]


def test_the_burn_groups_its_own_lines_the_same_way(tmp_path):
    segments = _segments(LONG)
    out = build_captions(segments, _clip(segments), tmp_path / "c.ass")
    events = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert [e.rsplit("}", 1)[1] for e in events] == LONG.split()
    look = _look()
    assert _fit_for(look, (1080, 1920))(LONG) is False and line_fit(None)(LONG) is False


def test_with_the_second_speakers_turns_the_captions_stay_on_one_line_too():
    text = LONG + " " + SHORT + " " + LONG
    segments = _segments(text)
    turns = [[0.9, 1.8, 1]]                                       # the other speaker says SHORT
    lines = build_caption_lines(segments, _clip(segments), 3, turns, fit=line_fit(None))
    assert " ".join(l["text"] for l in lines) == text
    assert all(len(l["text"].split()) == 1 or _one_line_in_the_box(l["text"]) for l in lines)
    assert [l["text"] for l in lines if l.get("speaker")] == [SHORT]
