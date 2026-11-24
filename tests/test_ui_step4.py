"""Step 4 in the desktop app: "About this creator".

The app has no React test harness, so the parts that live in the pages are
checked in their source: that the box is there, talks to the route the
engine has, and keeps the promises the user guide makes. The Free music
picker was in Quick edit, which is gone since step KC; music is imported in
the editor, and the free library comes back there in K4.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"


def _source(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---- About this creator -----------------------------------------------------------------------

def test_the_box_is_on_the_creators_page_and_saves_through_the_route():
    page = _flat(_source("pages/Creators.tsx"))
    assert "{t('About this creator')}" in page
    assert page.count("<textarea") == 1
    assert "onBlur={() => void saveAbout()}" in page
    assert "api.setCreatorDescription(now.id, now.text)" in page
    api = _flat(_source("lib/api.ts"))
    assert "`/creators/${creatorId}/description`" in api
    assert "body: JSON.stringify({ description })" in api
    # The page shows what the engine stored (tidied), not what was sent.
    assert "const { description } = await api.setCreatorDescription" in page
    assert re.search(r"interface CreatorDetail \{[^}]*\bdescription: string", _source("lib/types.ts"))


def test_the_box_stops_at_the_engines_limit_and_counts_only_near_it():
    page = _flat(_source("pages/Creators.tsx"))
    state = (ROOT / "core" / "state.py").read_text(encoding="utf-8")
    limit = re.search(r"^CREATOR_DESCRIPTION_MAX = (\d+)", state, re.MULTILINE).group(1)
    assert f"const ABOUT_MAX = {limit}" in page
    assert "maxLength={ABOUT_MAX}" in page
    assert "{about.length >= ABOUT_MAX - 100 && (" in page


def test_the_tooltip_gives_an_example_and_says_where_the_note_goes():
    page = _source("pages/Creators.tsx")
    tip = re.search(r"<textarea.*?title=\{t\(\s*'(.*?)'\s*\)\}", page, re.DOTALL).group(1)
    assert "Example:" in tip
    assert "stays on this PC" in tip
    assert "cloud AI with your own key" in tip and "sent to that provider" in tip


def test_the_creators_page_gained_no_other_control():
    """One box. The wipe button keeps its wording and gets no new text."""
    page = _source("pages/Creators.tsx")
    assert "Wipe learned memory for this creator" in page
    assert page.count("<textarea") == 1
    assert page.count("<input") == 3          # learning, profile name, channel to add
