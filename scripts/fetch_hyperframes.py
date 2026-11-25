"""Install HyperFrames (HeyGen, Apache-2.0) for the animations on clips.

HyperFrames turns an HTML page with a paused animation timeline into video: it
films the page frame by frame in its own pinned headless Chrome. Clips Kitty
uses it exactly as published: their npm package, their composition format,
their catalog blocks and their own lint, check and render commands
(video/hyperframes.py). Nothing of theirs is changed or rebuilt here.

    python scripts/fetch_hyperframes.py            # install if missing
    python scripts/fetch_hyperframes.py --force    # install again

It needs Node 22 or newer on this PC (their package says so), and npm.

What it does, into vendor/hyperframes/ (gitignored, like every fetched tool):
  1. downloads the pinned package tarball with `npm pack` and checks it
     against the sha512 the npm registry published for that version;
  2. installs that tarball with `npm install` (npm checks every dependency
     against the integrity in the registry as it goes);
  3. runs their own `hyperframes browser ensure`, which fetches the Chrome
     build their renderer is pinned to, into vendor/hyperframes/cache.

HyperFrames is Apache-2.0 (its package.json says so; the npm package ships no
LICENSE file, so the text is linked). A NOTICE is written beside it. Before
it is ever bundled into an installer, add the licence text from their repo.
"""

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video import hyperframes as hf  # noqa: E402  (after the path line above)

DEST = ROOT / "vendor" / "hyperframes"

# Pinned so a build is reproducible. Bump deliberately, with the integrity
# `npm view hyperframes@<version> dist.integrity` prints, after a real render.
VERSION = hf.VERSION
INTEGRITY = (
    "sha512-fhRP3IGvkZFlDPo4/l+n2TKPguOJAPngh/YQWi7tIrajqg0BtAuAWeyUzc2Mwrm1lCF7zwa+KV29e/SFJrlOcw=="
)
SOURCE_URL = "https://github.com/heygen-com/hyperframes"

NOTICE = f"""HyperFrames {VERSION}
-------------------
This application uses an unmodified copy of HyperFrames by HeyGen, installed
from the npm registry (package "hyperframes@{VERSION}", integrity
{INTEGRITY}).

Source: {SOURCE_URL}
Licence: Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0
(declared in node_modules/hyperframes/package.json; the source repository
holds the LICENSE file).

Its catalog blocks animate with GSAP, which is under its own licence
(https://gsap.com/standard-license).
"""


def integrity_of(blob: bytes) -> str:
    """The npm-style sha512 integrity string of a tarball."""
    return "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode("ascii")


def check_tarball(blob: bytes, expected: str = INTEGRITY) -> str | None:
    """None when the tarball is the pinned release; otherwise why not."""
    got = integrity_of(blob)
    if got == expected:
        return None
    return (
        f"The download is not the pinned HyperFrames {VERSION}: its integrity is\n"
        f"  {got}\n"
        f"but should be\n"
        f"  {expected}\n"
        "Nothing was installed."
    )


def _npm() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def fetch(force: bool = False) -> int:
    node = hf.find_node()
    if node is None or node[1] < hf.NODE_MIN:
        print(hf.NODE_MESSAGE)
        return 1
    npm = _npm()
    if npm is None:
        print("npm was not found. It comes with Node from https://nodejs.org (version 22 or newer).")
        return 1

    installed = hf.cli_script(DEST)
    if installed.exists() and not force and hf.installed_version(DEST) == VERSION:
        print(f"  present: HyperFrames {VERSION} in {DEST}")
    else:
        DEST.mkdir(parents=True, exist_ok=True)
        pack_dir = DEST / "pack"
        pack_dir.mkdir(exist_ok=True)
        print(f"Downloading HyperFrames {VERSION} from the npm registry")
        out = subprocess.run(
            [npm, "pack", f"hyperframes@{VERSION}", "--json", "--pack-destination", str(pack_dir)],
            capture_output=True, text=True, cwd=str(DEST),
        )
        if out.returncode != 0:
            print(f"\nnpm pack failed:\n{out.stderr[-1500:]}")
            return 1
        tarball = pack_dir / json.loads(out.stdout)[0]["filename"]
        problem = check_tarball(tarball.read_bytes())
        if problem:
            print("\n" + problem)
            tarball.unlink(missing_ok=True)
            return 1
        print("  integrity checked")
        (DEST / "package.json").write_text(
            json.dumps({"name": "clips-kitty-hyperframes", "private": True,
                        "description": "HyperFrames, installed by scripts/fetch_hyperframes.py"}, indent=2),
            encoding="utf-8",
        )
        out = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--omit=dev", str(tarball)],
            capture_output=True, text=True, cwd=str(DEST), env=hf.env(DEST),
        )
        if out.returncode != 0:
            print(f"\nnpm install failed:\n{out.stderr[-1500:]}")
            return 1
        (DEST / "NOTICE-HYPERFRAMES.txt").write_text(NOTICE, encoding="utf-8")
        print(f"  installed HyperFrames {hf.installed_version(DEST)} into {DEST}")

    print("Fetching HyperFrames' pinned headless Chrome (their `browser ensure`)")
    try:
        result = hf.run_cli(["browser", "ensure"], cwd=DEST, root=DEST, timeout=1800)
    except hf.HyperFramesError as e:
        print(f"\n{e}")
        return 1
    if result.returncode != 0:
        print(f"\nbrowser ensure failed:\n{(result.stderr or result.stdout)[-1500:]}")
        return 1
    print("  browser ready in", hf.cache_dir(DEST))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="install again even if present")
    return fetch(ap.parse_args().force)


if __name__ == "__main__":
    sys.exit(main())
