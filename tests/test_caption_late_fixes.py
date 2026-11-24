"""Step 3, the last round of caption fixes: a staged font's temporary copy
has a short name, so a long clip folder keeps its font. (The karaoke and
style-size fixes went with karaoke and the old caption styles.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from video import fonts


def test_a_staged_fonts_temporary_copy_has_a_short_name(tmp_path, monkeypatch):
    family = next((f for f in fonts.families() if fonts.usable(f)), None)
    if family is None:
        pytest.skip("no bundled fonts in this checkout")
    seen = []
    real = shutil.copyfile

    def copy(src, dst, *args, **kwargs):
        seen.append((Path(src).name, Path(dst).name))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(fonts.shutil, "copyfile", copy)
    assert fonts.stage([family], tmp_path) == fonts.STAGED
    assert seen
    for final, temporary in seen:
        # Never longer than the font's own file name, so staging needs no
        # longer path than the staged file itself.
        assert temporary.startswith("~") and temporary.endswith(".tmp")
        assert len(temporary) <= len(final)
    left = sorted(p.name for p in (tmp_path / fonts.STAGED).iterdir())
    assert not [n for n in left if n.endswith(".tmp")]
