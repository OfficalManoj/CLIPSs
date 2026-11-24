"""The caption fonts that ship with the app (assets/fonts/, video/fonts.py):
Clips Kitty Editor's own fonts, byte for byte.

- The licence manifest: every file it lists is there with its sha256 and is
  the family it says (read from the TTF's own name table); every file has its
  licence file (OFL 1.1 or Apache 2.0); NOTICE names every licence file; the
  folder holds nothing the manifest does not list; no old caption font is left.
- stage(): a bundled font's file is copied beside the captions and the burn
  is given fontsdir; a missing or changed file is not staged; Windows fonts
  stage nothing.
- Each file's "scripts" are exactly the writing systems its character map
  has every letter of.
- caption_font_for(): a font is kept only for a language whose every letter
  its file has; otherwise the editor's own font for that language.
- The burn places all build their subtitles filter through video/fonts.py.

Standard library only: these run in CI's light install.
"""

import hashlib
import json
import re
import struct
from pathlib import Path

import pytest

from video import fonts

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "assets" / "fonts"
MANIFEST = json.loads((FONTS_DIR / "manifest.json").read_text(encoding="utf-8"))
ENTRIES = MANIFEST["files"]
SCRIPTS = {"latin", "latin-ext", "cyrillic", "cyrillic-ext", "greek", "vietnamese", "thai"}
OLD_FONTS = ("Barlow", "Bowlby", "Kanit", "Lilita", "Passion One", "Poppins", "Russo", "Titan One")


def _family_names(path: Path) -> set[str]:
    """The TTF name table's family, full and typographic names (IDs 1, 4,
    16), Windows English: libass matches a style's Fontname against them."""
    names: set[str] = set()
    data = path.read_bytes()
    count = struct.unpack(">H", data[4:6])[0]
    for i in range(count):
        tag, _sum, offset, _length = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        if tag != b"name":
            continue
        _fmt, records, strings = struct.unpack(">HHH", data[offset:offset + 6])
        for r in range(records):
            platform, _enc, lang, name_id, length, start = struct.unpack(
                ">HHHHHH", data[offset + 6 + 12 * r:offset + 18 + 12 * r])
            if platform == 3 and lang == 0x409 and name_id in (1, 4, 16):
                at = offset + strings + start
                names.add(data[at:at + length].decode("utf-16-be"))
    if not names:
        raise AssertionError(f"{path.name} has no family name")
    return names


# ---- the licence manifest -------------------------------------------------------------------


def test_every_listed_file_is_there_with_its_sha256_and_family():
    assert len(ENTRIES) == 52
    for e in ENTRIES:
        path = FONTS_DIR / e["file"]
        assert path.is_file(), e["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == e["sha256"], e["file"]
        assert path.stat().st_size == e["bytes"], e["file"]
        if path.suffix == ".ttf":
            assert e["family"] in _family_names(path), e["file"]


def test_every_entry_says_what_it_is_and_where_it_came_from():
    commit = MANIFEST["source"]
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    for e in ENTRIES:
        assert e["licence"] in ("OFL-1.1", "Apache-2.0")
        assert e["source"] == commit
        assert isinstance(e["weight"], int) and 100 <= e["weight"] <= 900
        assert isinstance(e["ass_scale"], float) and 0.8 <= e["ass_scale"] <= 3.0, e["file"]
        assert e["typographic"] and e["subfamily"]
        assert set(e["scripts"]) <= SCRIPTS


def test_every_file_has_its_licence():
    for e in ENTRIES:
        text = (FONTS_DIR / e["licence_file"]).read_text(encoding="utf-8", errors="replace")
        wanted = "SIL OPEN FONT LICENSE Version 1.1" if e["licence"] == "OFL-1.1" else "Apache License"
        assert wanted in text, e["licence_file"]


def test_notice_names_every_licence_file():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    section = notice[notice.index("\nFonts\n"):]
    for e in ENTRIES:
        assert e["licence_file"] in section, e["licence_file"]
    assert MANIFEST["source"] in section
    for old in OLD_FONTS:
        assert old not in section, old


def test_the_folder_holds_only_what_the_manifest_lists():
    listed = {e["file"] for e in ENTRIES} | {e["licence_file"] for e in ENTRIES} | {"manifest.json"}
    found = {p.relative_to(FONTS_DIR).as_posix() for p in FONTS_DIR.rglob("*") if p.is_file()}
    assert found == listed
    assert sum((FONTS_DIR / f).stat().st_size for f in found) <= 20 * 1024 * 1024
    names = [Path(e["file"]).name for e in ENTRIES]
    assert len(names) == len(set(names)), "file names must be unique: GET /fonts/{file} serves them by name"


def test_no_old_caption_font_is_named_anywhere():
    places = ["config", "video/captions.py", "video/caption_presets.py", "server/mcp.py", "analysis/clip_edit.py",
              "kitty_editor/build.py", "ui/src/renderer/src/components/CaptionStyleControls.tsx",
              "ui/src/renderer/src/lib/captionLook.ts", "ui/src/renderer/src/lib/types.ts"]
    for place in places:
        root = ROOT / place
        for path in [root] if root.is_file() else root.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json", ".txt", ".ts", ".tsx"):
                text = path.read_text(encoding="utf-8", errors="replace")
                for old in OLD_FONTS:
                    # caption_presets.py maps an old saved style's font by name.
                    assert old not in text or path.name == "caption_presets.py", (path, old)


def test_the_registry_reads_the_manifest():
    assert fonts.families() == list(dict.fromkeys(e["family"] for e in ENTRIES))
    assert fonts.is_bundled("Anton") and not fonts.is_bundled("Arial") and not fonts.is_bundled("anton")
    assert fonts.files_for("Anton") == [FONTS_DIR / "Anton-Regular.ttf"]
    assert fonts.face("Montserrat", 900)["family"] == "Montserrat Black"
    assert fonts.face("Montserrat", 850)["family"] == "Montserrat Black"        # a tie goes heavier
    assert fonts.face("Inter", 700)["subfamily"] == "Bold"
    assert fonts.face("DM Serif Display", 400, italic=True)["file"] == "DMSerifDisplay-Italic.ttf"
    assert fonts.face("Arial") is None
    assert fonts.ass_scale("Montserrat Black") == pytest.approx(1.562)
    assert all(fonts.usable(f) for f in fonts.families())


# ---- what each font can write ------------------------------------------------------------------


def _cmap(path: Path) -> frozenset[int]:
    from video import fontfile

    font = fontfile.load(path)
    assert font is not None, path
    return font.codepoints()


def test_each_familys_scripts_are_exactly_what_its_file_has():
    """A script is listed only when the file's character map has every
    letter of it (video/alphabets.py SCRIPT_LETTERS, both cases), and every
    script it has in full is listed."""
    from video import alphabets

    for e in ENTRIES:
        have = _cmap(FONTS_DIR / e["file"])
        for script in e["scripts"]:
            missing = [c for c in alphabets.SCRIPT_LETTERS[script] if ord(c) not in have]
            assert not missing, f"{e['family']} lists {script} but has no {''.join(missing)}"
        assert e["scripts"] == alphabets.scripts_in(have), e["file"]


def test_the_character_maps_read_here_are_fonttools_own():
    """video/fontfile.py reads the cmap with struct; fontTools, where it is
    installed, reads the same characters from every bundled file."""
    ttlib = pytest.importorskip("fontTools.ttLib")
    for e in ENTRIES:
        best = ttlib.TTFont(str(FONTS_DIR / e["file"])).getBestCmap()
        assert _cmap(FONTS_DIR / e["file"]) == frozenset(c for c, g in best.items() if g != ".notdef"), e["family"]


def test_every_language_needs_its_own_letters():
    from video import alphabets

    assert "Ț" in alphabets.letters_for("ro") and "ț" in alphabets.letters_for("ro")
    assert "Ħ" in alphabets.letters_for("mt") and "Ĝ" in alphabets.letters_for("eo")
    assert "Ŵ" in alphabets.letters_for("cy") and "Ā" in alphabets.letters_for("mi")
    assert "Ə" in alphabets.letters_for("az")
    for lang, letter in (("kk", "Қ"), ("ky", "Ң"), ("tg", "Ҷ"), ("mn", "Ө"), ("ba", "Ҙ"), ("tt", "Җ")):
        assert letter in alphabets.letters_for(lang) and "Ж" in alphabets.letters_for(lang), lang
        assert alphabets.script_of(lang) == "cyrillic-ext"
    assert alphabets.script_of("cy") == alphabets.script_of("mi") == "latin-ext"
    assert alphabets.letters_for("hi") is None and alphabets.letters_for("ja") is None
    assert set(alphabets.letters_for("en")) == set(alphabets.SCRIPT_LETTERS["latin"])


# ---- staging -----------------------------------------------------------------------------------


@pytest.fixture
def copy_of_fonts(tmp_path, monkeypatch):
    """The registry reading a copy of assets/fonts, which a test may damage."""
    folder = tmp_path / "shipped"
    for e in ENTRIES:
        for rel in (e["file"], e["licence_file"]):
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes((FONTS_DIR / rel).read_bytes())
    (folder / "manifest.json").write_bytes((FONTS_DIR / "manifest.json").read_bytes())
    monkeypatch.setattr(fonts, "_roots", lambda: [folder])
    fonts.reload()
    yield folder
    monkeypatch.undo()
    fonts.reload()


def test_a_bundled_font_is_staged_beside_the_captions(copy_of_fonts, tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    assert fonts.stage(["Arial", "Anton", "Anton"], clips) == "fonts"
    staged = clips / "fonts" / "Anton-Regular.ttf"
    assert staged.read_bytes() == (copy_of_fonts / "Anton-Regular.ttf").read_bytes()
    assert sorted(p.name for p in (clips / "fonts").iterdir()) == ["Anton-Regular.ttf"]
    assert fonts.stage(["Anton"], clips) == "fonts"           # a neighbour's copy is reused as it is


def test_windows_fonts_stage_nothing(copy_of_fonts, tmp_path):
    assert fonts.stage(["Arial", "Impact", "Nirmala UI"], tmp_path) is None
    assert fonts.stage([], tmp_path) is None
    assert not (tmp_path / "fonts").exists()


def test_a_missing_file_is_not_staged(copy_of_fonts, tmp_path, capsys):
    (copy_of_fonts / "Anton-Regular.ttf").unlink()
    assert fonts.stage(["Anton"], tmp_path) is None
    assert not (tmp_path / "fonts" / "Anton-Regular.ttf").exists()
    assert "Anton is missing or damaged" in capsys.readouterr().out
    assert not fonts.usable("Anton") and fonts.usable("Bangers")


def test_a_changed_file_is_not_staged(copy_of_fonts, tmp_path):
    path = copy_of_fonts / "Bangers-Regular.ttf"
    path.write_bytes(path.read_bytes()[:-1] + b"\0")
    assert fonts.stage(["Bangers"], tmp_path) is None
    assert fonts.stage(["Bangers", "Anton"], tmp_path) == "fonts"     # the good one still goes
    assert sorted(p.name for p in (tmp_path / "fonts").iterdir()) == ["Anton-Regular.ttf"]


def test_no_fonts_folder_means_windows_fonts_only(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "_roots", lambda: [tmp_path / "nowhere"])
    fonts.reload()
    try:
        assert fonts.families() == [] and fonts.stage(["Anton"], tmp_path) is None
    finally:
        monkeypatch.undo()
        fonts.reload()


def test_the_subtitles_filter_names_fontsdir_only_for_a_bundled_font(tmp_path):
    from core.models import ClipCandidate, Segment
    from video.captions import build_captions

    (tmp_path / "windows").mkdir()
    plain = tmp_path / "windows" / "clip.ass"
    plain.write_text("Style: Default,Arial,84" + chr(10), encoding="utf-8")
    assert fonts.subtitles_filter(plain) == "subtitles=clip.ass"
    assert not (plain.parent / "fonts").exists()
    segments = [Segment(start=0.0, end=2.0, text="one two three")]
    window = ClipCandidate(start=0.0, end=2.0, score=0)
    (tmp_path / "bundled").mkdir()
    ass = build_captions(segments, window, tmp_path / "bundled" / "clip.ass")    # the default: Bold caption
    assert fonts.subtitles_filter(ass) == "subtitles=clip.ass:fontsdir=fonts"
    assert (ass.parent / "fonts" / "Montserrat-Black.ttf").is_file()


def test_the_staged_fonts_go_once_no_captions_file_is_left(tmp_path):
    (tmp_path / "a.ass").write_text("Style: Default,Anton,84\n", encoding="utf-8")
    (tmp_path / "b.ass").write_text("Style: Default,Anton,84\n", encoding="utf-8")
    assert fonts.subtitles_filter(tmp_path / "a.ass").endswith(":fontsdir=fonts")
    (tmp_path / "a.ass").unlink()
    fonts.release(tmp_path)
    assert (tmp_path / "fonts" / "Anton-Regular.ttf").is_file()      # b.ass still burns from it
    (tmp_path / "fonts" / "mine.txt").write_text("not ours", encoding="utf-8")
    (tmp_path / "b.ass").unlink()
    fonts.release(tmp_path)
    assert [p.name for p in (tmp_path / "fonts").iterdir()] == ["mine.txt"]    # only our files go
    (tmp_path / "fonts" / "mine.txt").unlink()
    fonts.release(tmp_path)
    assert not (tmp_path / "fonts").exists()


def test_fonts_in_an_ass_file():
    text = ("Style: Default,Bebas Neue,84,&H00FFFFFF\nStyle: Hook,Arial Black,96\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\\fnMontserrat Black\\b1}HI\n")
    assert fonts.fonts_in_ass(text) == ["Bebas Neue", "Arial Black", "Montserrat Black"]


def test_every_burn_place_builds_its_filter_through_the_registry():
    """The eight places that burn an ASS file (video/cropper.py four times,
    video/cutter.py, video_editor/export.py, gaming/compose.py and
    multilingual/burn.py) take their subtitles filter from
    video.fonts.subtitles_filter, so each adds fontsdir when it is needed."""
    places = {"video/cropper.py": 4, "video/cutter.py": 1, "video_editor/export.py": 1,
              "gaming/compose.py": 1, "multilingual/burn.py": 1}
    for rel, count in places.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert source.count("subtitles_filter(") == count, rel
        assert not re.search(r"subtitles=\{(?:ass_path|built)\.name\}", source), rel


# ---- which font burns for a language --------------------------------------------------------


@pytest.mark.parametrize(("language", "chosen", "burns"), [
    ("en", "Bangers", "Bangers"),
    ("pl", "Anton", "Anton"),
    ("ru", "Bangers", "Montserrat"),                     # Cyrillic: the editor's Russian font
    ("ru", "Montserrat", "Montserrat"),
    ("hi", "Anton", "Noto Sans Devanagari"),             # the editor's own font for the language
    ("ja", "Bangers", "Kosugi Maru"),
    ("ar", "Anton", "Noto Sans Arabic UI"),
    ("vi", "Bebas Neue", "Montserrat"),
    ("th", "Anton", "Noto Sans Thai"),
    ("he", "Anton", "Secular One"),
    ("zh-CN", "Anton", "Noto Sans CJK SC"),
    ("ko", "Inter", "Noto Sans CJK SC"),
    ("el", "Bangers", "Inter"),
    ("ta", "Anton", "Nirmala UI"),                       # no editor font: Windows' own
])
def test_a_font_burns_only_where_it_has_the_letters(language, chosen, burns):
    from video.captions import caption_font_for

    assert caption_font_for(language, chosen) == burns


LANGUAGES = ["en", "fr", "de", "es", "pt", "it", "nl", "sv", "is", "pl", "cs", "sk", "hu", "ro", "tr", "hr", "bs",
             "sl", "lt", "lv", "et", "az", "sq", "mt", "uz", "tk", "eo", "haw", "ln", "yo", "ha", "cy", "mi", "ru",
             "uk", "bg", "sr", "mk", "be", "kk", "ky", "tg", "mn", "ba", "tt", "el", "vi", "th", "hi", "ja", "ar"]


@pytest.mark.parametrize("family", fonts.typographic_families())
def test_a_kept_font_has_every_letter_of_the_language(family):
    """Whenever a preset's font is kept, its file has every letter the
    language is written with, in both cases: libass never has to borrow a
    letter from another font in the middle of a word."""
    from video import alphabets
    from video.caption_presets import LANGUAGE_FONTS
    from video.captions import caption_font_for

    for language in LANGUAGES:
        burns = caption_font_for(language, family)
        letters = alphabets.letters_for(language)
        # The editor's own font for a language is what its preset uses, as it is.
        if burns == family and letters is not None and burns != LANGUAGE_FONTS.get(language):
            assert fonts.covers(fonts.face(family)["family"], letters), (family, language)


def test_a_language_with_no_chosen_font():
    from video.captions import caption_font_for

    assert caption_font_for("en", None) is None
    assert caption_font_for("ru", None) is None
    assert caption_font_for("ja", None) == "Kosugi Maru"
    assert caption_font_for("ta", None) == "Nirmala UI"
