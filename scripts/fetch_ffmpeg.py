"""Download the FFmpeg binaries the installer ships.

Creators don't have FFmpeg and won't install it, so the packaged app carries
its own. This fetches a static Windows build and drops ffmpeg.exe and
ffprobe.exe into vendor/ffmpeg/, where core.binaries looks for them and the
packaging step picks them up.

    python scripts/fetch_ffmpeg.py            # fetch if missing
    python scripts/fetch_ffmpeg.py --force    # re-download

The binaries are NOT committed — they're build inputs, fetched on the
machine that builds the installer.

Licensing note: this pulls a GPL build, because CPU encoding uses libx264
which is GPL. Clips Kitty only runs FFmpeg as a separate process and does
not link against it, so shipping the unmodified binary alongside the app is
mere aggregation — but the GPL still requires that users can get FFmpeg's
source. vendor/ffmpeg/README-FFMPEG.txt is written next to the binaries with
that offer, and the installer ships it.
"""

import argparse
import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "ffmpeg"

# Pinned, by version AND by hash, so a build is reproducible and a surprise
# upstream change can't silently alter what ships. This used to point at
# gyan.dev's moving "release-essentials" link, which started serving FFmpeg 9
# in November 2026; FFmpeg 9 removed -vsync, which made every clip fail
# (#281). Bump deliberately, and test a new FFmpeg with the app first.
FFMPEG_VERSION = "8.1.2"
BUILD_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/"
    f"{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
)
BUILD_SHA256 = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
BUILD_NAME = f"FFmpeg {FFMPEG_VERSION} essentials build by gyan.dev (GPL)"
SOURCE_URL = "https://www.ffmpeg.org/download.html"
# FFmpeg's own source for exactly this release.
RELEASE_SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz"

NOTICE = f"""FFmpeg
------
This application bundles unmodified FFmpeg binaries ({BUILD_NAME}).

FFmpeg is free software licensed under the GNU General Public License
version 3 or later, because this build includes GPL components (libx264).
Clips Kitty invokes FFmpeg as a separate program and does not link
against it.

The complete corresponding source code for FFmpeg {FFMPEG_VERSION} is
available from:
    {RELEASE_SOURCE_URL}
    {SOURCE_URL}
The build itself (archive SHA-256 {BUILD_SHA256}):
    {BUILD_URL}

FFmpeg is a trademark of Fabrice Bellard.
"""

WANTED = ("ffmpeg.exe", "ffprobe.exe")


def check_archive(blob: bytes, expected: str = BUILD_SHA256) -> str | None:
    """None when the downloaded archive is the pinned build; otherwise why
    not, in words. Checked before anything is extracted."""
    got = hashlib.sha256(blob).hexdigest()
    if got == expected.lower():
        return None
    return (
        f"The download is not the pinned FFmpeg {FFMPEG_VERSION} build: its SHA-256 is\n"
        f"  {got}\n"
        f"but should be\n"
        f"  {expected}\n"
        "Nothing was extracted. If the file was replaced upstream, test the new\n"
        "build with the app before changing BUILD_SHA256 in scripts/fetch_ffmpeg.py."
    )


def fetch(force: bool = False) -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    have = [n for n in WANTED if (DEST / n).exists()]
    if len(have) == len(WANTED) and not force:
        for n in WANTED:
            print(f"  present: {DEST / n} ({(DEST / n).stat().st_size / 1e6:.0f} MB)")
        print("Already vendored — use --force to re-download.")
        return 0

    print(f"Downloading {BUILD_NAME}")
    print(f"  {BUILD_URL}")
    try:
        with urllib.request.urlopen(BUILD_URL, timeout=180) as r:
            blob = r.read()
    except Exception as e:
        print(f"\nDownload failed: {type(e).__name__}: {e}")
        print("Fetch it manually and put ffmpeg.exe + ffprobe.exe in:")
        print(f"  {DEST}")
        return 1
    print(f"  got {len(blob) / 1e6:.0f} MB")

    problem = check_archive(blob)
    if problem:
        print(f"\n{problem}")
        return 1
    print("  SHA-256 matches the pinned build")

    found = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for member in z.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in WANTED:
                with z.open(member) as src, open(DEST / name, "wb") as out:
                    shutil.copyfileobj(src, out)
                size = (DEST / name).stat().st_size
                print(f"  extracted {name} ({size / 1e6:.0f} MB)")
                found += 1

    if found != len(WANTED):
        print(f"\nExpected {len(WANTED)} binaries, found {found} — archive layout changed?")
        return 1

    (DEST / "README-FFMPEG.txt").write_text(NOTICE, encoding="utf-8")
    print(f"  wrote {DEST / 'README-FFMPEG.txt'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    sys.exit(fetch(ap.parse_args().force))
