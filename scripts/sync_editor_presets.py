"""Copy Clips Kitty Editor's text presets and fonts into the app.

Auto clips burn their captions with the editor's own looks: the text presets
and the fonts the editor (our Concat fork) ships. This script reads them from
a checkout of the fork and writes:

- config/caption_presets.json: every built-in text preset, in the editor's
  order, each with its whole look spelled out (the editor's defaults laid
  under what the preset file says, as concat/src/presets.rs `over()` does);
- assets/fonts/: the editor's font files and licence files, byte for byte,
  in one flat folder, and assets/fonts/manifest.json describing each file.

    python scripts/sync_editor_presets.py D:/path/to/kitty-editor   # write
    python scripts/sync_editor_presets.py D:/path/to/kitty-editor --check

--check writes nothing and exits 1 when the app's copy differs from the
fork's. Run it again whenever the fork's presets or fonts change.
Standard library only (Python 3.11 for tomllib).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRESETS_JSON = ROOT / "config" / "caption_presets.json"
FONTS_DIR = ROOT / "assets" / "fonts"

# concat-project/src/model.rs TextStyle::default()
_BASE = {
    "fontFamily": "Hanken Grotesk",
    "fontSize": 0.09,
    "fontWeight": 700.0,
    "color": "#ffffff",
    "strokeColor": "#000000",
    "backgroundRadius": 0.0135,
    "backgroundPaddingX": 0.0315,
    "backgroundPaddingY": 0.018,
    "lineHeight": 1.2,
}


def _crates(fork: Path) -> Path:
    for candidate in (fork / "src" / "crates", fork / "crates", fork):
        if (candidate / "concat-text" / "fonts").is_dir() and (candidate / "concat" / "text-presets").is_dir():
            return candidate
    raise SystemExit(f"{fork} is not a checkout of the editor: no concat-text/fonts or concat/text-presets")


def _commit(fork: Path) -> str:
    try:
        done = subprocess.run(["git", "-C", str(fork), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30)
        return done.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def resolved_style(raw: dict, name: str) -> dict:
    """A preset file's [style] over the editor's defaults (presets.rs over())."""
    style = {
        "content": raw.get("content", name),
        "fontFamily": raw.get("fontFamily", "Hanken Grotesk"),
        "fontSize": _clamp(float(raw.get("fontSize", _BASE["fontSize"])), 0.005, 1.0),
        "fontWeight": _clamp(float(raw.get("fontWeight", _BASE["fontWeight"])), 100.0, 900.0),
        "italic": bool(raw.get("italic", False)),
        "textCase": raw.get("textCase", "original"),
        "underline": bool(raw.get("underline", False)),
        "strikethrough": bool(raw.get("strikethrough", False)),
        "color": raw.get("color", _BASE["color"]),
        "align": raw.get("align") if raw.get("align") in ("left", "right") else "center",
        "opacity": _clamp(float(raw.get("opacity", 1.0)), 0.0, 1.0),
        "strokeWidth": max(0.0, float(raw.get("strokeWidth", 0.0))),
        "strokeColor": raw.get("strokeColor", _BASE["strokeColor"]),
        "shadow": bool(raw.get("shadow", True)),
        "background": raw.get("background", ""),
        "backgroundRadius": max(0.0, float(raw.get("backgroundRadius", _BASE["backgroundRadius"]))),
        "backgroundPaddingX": max(0.0, float(raw.get("backgroundPaddingX", _BASE["backgroundPaddingX"]))),
        "backgroundPaddingY": max(0.0, float(raw.get("backgroundPaddingY", _BASE["backgroundPaddingY"]))),
        "lineHeight": max(0.5, float(raw.get("lineHeight", _BASE["lineHeight"]))),
        "tracking": float(raw.get("tracking", 0.0)),
        "maxWidth": max(0.0, float(raw.get("maxWidth", 0.0))),
        "maxHeight": max(0.0, float(raw.get("maxHeight", 0.0))),
    }
    if raw.get("runs"):
        raise SystemExit(f"Preset {name!r} has runs, which captions do not carry: decide how first")
    return style


def read_presets(crates: Path) -> list[dict]:
    """The built-in presets in the editor's order: by `order`, then name."""
    found = []
    for path in sorted((crates / "concat" / "text-presets").glob("*/preset.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        pid = str(data.get("id") or "").strip()
        if not pid:
            continue
        name = str(data.get("name") or "").strip() or pid
        language = str(data.get("language") or "").strip() or "Latin"
        style = resolved_style(data.get("style") or {}, str(data.get("name") or ""))
        sample = str(data.get("sample") or "").strip()
        if not sample:
            sample = "Aa" if language.casefold() == "latin" else "".join(
                c for c in style["content"] if not c.isspace())[:2]
        entry = {"id": pid, "name": name, "language": language, "sample": sample, "style": style}
        if data.get("offsetY") is not None:
            entry["offsetY"] = float(data["offsetY"])
        found.append((int(data["order"]) if data.get("order") is not None else sys.maxsize, name, entry))
    found.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in found]


def _licence_for(font_name: str, licences: list[str]) -> str:
    stem = Path(font_name).stem.split("-")[0]
    matches = [name for name in licences if stem.startswith(name[len("LICENSE-"):-len(".txt")])]
    if not matches:
        raise SystemExit(f"No licence file for {font_name}")
    return max(matches, key=len)


def read_fonts(crates: Path, commit: str) -> tuple[list[dict], list[Path]]:
    """The manifest entries and every file to copy (fonts, then licences)."""
    from video import alphabets, fontfile

    folder = crates / "concat-text" / "fonts"
    licences = sorted(p.name for p in folder.glob("LICENSE-*.txt"))
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".ttf", ".otf"))
    entries = []
    for path in files:
        data = path.read_bytes()
        font = fontfile.Font(data, 0)
        names = font.names
        family = names.get(1, [path.stem])[0]
        subfamily = names.get(2, ["Regular"])[0]
        typographic = (names.get(16) or names.get(1) or [path.stem])[0]
        licence_file = _licence_for(path.name, licences)
        text = (folder / licence_file).read_text(encoding="utf-8", errors="replace")
        entries.append({
            "file": path.name,
            "family": family,
            "subfamily": subfamily,
            "typographic": typographic,
            "weight": int(font.weight),
            "italic": "italic" in subfamily.casefold(),
            "ass_scale": round(font.win_height / font.units_per_em, 4),
            "scripts": alphabets.scripts_in(font.codepoints()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "licence": "Apache-2.0" if "Apache License" in text else "OFL-1.1",
            "licence_file": licence_file,
            "source": commit,
        })
    return entries, files + [folder / name for name in licences]


def build(fork: Path) -> tuple[dict, dict, list[Path]]:
    crates = _crates(fork)
    commit = _commit(fork)
    presets = read_presets(crates)
    entries, files = read_fonts(crates, commit)
    have = {e["typographic"] for e in entries}
    missing = sorted({p["style"]["fontFamily"] for p in presets} - have)
    if missing:
        raise SystemExit(f"Presets name fonts the editor's folder lacks: {', '.join(missing)}")
    preset_doc = {
        "about": ("Clips Kitty Editor's text presets (our Concat fork, concat/text-presets), in the editor's "
                  "order, each with its whole look as the editor resolves it. Captions on auto clips use "
                  "them (video/caption_presets.py). Made by scripts/sync_editor_presets.py: do not edit by hand."),
        "source": commit,
        "presets": presets,
    }
    manifest = {
        "about": ("Clips Kitty Editor's fonts (our Concat fork, concat-text/fonts), byte for byte, with each "
                  "font's licence file beside it. family is the name libass matches (name ID 1), typographic "
                  "the family the editor's presets name (ID 16, else ID 1). ass_scale is "
                  "(usWinAscent + usWinDescent) / unitsPerEm: libass sizes a font by it, so an ASS Fontsize is "
                  "the em times ass_scale. scripts lists the writing systems whose every letter is in the "
                  "file (video/alphabets.py). Made by scripts/sync_editor_presets.py."),
        "source": commit,
        "files": entries,
    }
    return preset_doc, manifest, files


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def check(fork: Path) -> list[str]:
    preset_doc, manifest, files = build(fork)
    problems = []
    if not PRESETS_JSON.is_file() or PRESETS_JSON.read_text(encoding="utf-8") != _dump(preset_doc):
        problems.append(f"{PRESETS_JSON.relative_to(ROOT)} differs from the fork's presets")
    manifest_path = FONTS_DIR / "manifest.json"
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != _dump(manifest):
        problems.append("assets/fonts/manifest.json differs from the fork's fonts")
    for src in files:
        dest = FONTS_DIR / src.name
        if not dest.is_file() or dest.read_bytes() != src.read_bytes():
            problems.append(f"assets/fonts/{src.name} differs from the fork's")
    extra = sorted({p.name for p in FONTS_DIR.iterdir()} - {p.name for p in files} - {"manifest.json"}) \
        if FONTS_DIR.is_dir() else []
    problems += [f"assets/fonts/{name} is not one of the editor's files" for name in extra]
    return problems


def write(fork: Path) -> None:
    preset_doc, manifest, files = build(fork)
    if FONTS_DIR.is_dir():
        shutil.rmtree(FONTS_DIR)
    FONTS_DIR.mkdir(parents=True)
    for src in files:
        shutil.copyfile(src, FONTS_DIR / src.name)
    (FONTS_DIR / "manifest.json").write_text(_dump(manifest), encoding="utf-8", newline="\n")
    PRESETS_JSON.write_text(_dump(preset_doc), encoding="utf-8", newline="\n")
    print(f"{len(preset_doc['presets'])} presets, {len(manifest['files'])} fonts and "
          f"{len(files) - len(manifest['files'])} licence files from {manifest['source'][:8]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fork", type=Path, help="a checkout of the editor (our Concat fork)")
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 on any difference")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.fork)
        for line in problems:
            print(line)
        print("The app's presets and fonts match the editor's." if not problems else "")
        return 1 if problems else 0
    write(args.fork)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
