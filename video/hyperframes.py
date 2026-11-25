"""Animations on clips, made with HyperFrames (HeyGen, Apache-2.0), as published.

HyperFrames films an HTML page that holds a paused animation timeline, frame by
frame, in its own pinned headless Chrome. Clips Kitty uses it as it is: their
npm package (scripts/fetch_hyperframes.py), their composition format, their
catalog blocks (`hyperframes add`) and their own `lint`, `check` and `render`.
There is no template system of our own here, only the steps that hand a
composition to their CLI and lay the transparent video it renders over a clip.

What a run does:
  1. a project folder in a temp directory, made by their `init` (blank);
  2. the animation: a catalog block added with `hyperframes add`, or HTML in
     their format (from a model, or from an outside MCP client);
  3. `lint --json` and `check --json`, their own validators;
  4. `render --format mov`: ProRes 4444 with alpha, at the clip's size and fps;
  5. the clip is rendered again with the MOV laid over it (core/pipeline.py,
     render_opts["overlays"]), so the source video is never touched.

Every run gets the same environment (env()): no telemetry, no update check, no
skills or optional installs, the app's own FFmpeg, no keys or session secret
from the engine, and a page that can't reach this PC or its local network.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = "0.9.94"
NODE_MIN = 22

NODE_MESSAGE = (
    "Animations need Node 22 or newer on this PC. Install it from https://nodejs.org "
    "(the LTS version), then try again."
)
NOT_INSTALLED = (
    "Animations aren't set up on this PC yet. Run: python scripts/fetch_hyperframes.py"
)

# Their Windows issue #4058: spawning Chrome or FFmpeg can fail with EBUSY while
# a just-written file is still held. Waiting a moment and running it again works.
EBUSY_ATTEMPTS = 3
EBUSY_WAIT = 2.0

# Replaced in tests, so no test spawns Node or waits.
spawn = subprocess.run
sleep = time.sleep


class HyperFramesError(Exception):
    """A failure in plain words, safe to show to a creator."""


# ---- where things are ----------------------------------------------------------


def _roots() -> list[Path]:
    from core.binaries import _search_roots

    override = os.environ.get("CLIPS_STUDIO_HYPERFRAMES")
    roots = [Path(override)] if override else []
    return roots + _search_roots("hyperframes")


def vendor_root() -> Path:
    """The folder scripts/fetch_hyperframes.py installed into (the first that
    has their CLI), else the repo's vendor/hyperframes."""
    roots = _roots()
    for root in roots:
        if cli_script(root).exists():
            return root
    return roots[-1]


def cli_script(root: Path) -> Path:
    return Path(root) / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"


def package_dir(root: Path) -> Path:
    return Path(root) / "node_modules" / "hyperframes"


def installed_version(root: Path) -> str:
    try:
        data = json.loads((package_dir(root) / "package.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except (OSError, ValueError):
        return ""


def cache_dir(root: Path) -> Path:
    """HYPERFRAMES_CACHE_DIR: their pinned Chrome lives here, beside the package."""
    override = os.environ.get("CLIPS_STUDIO_HYPERFRAMES_CACHE")
    return Path(override) if override else Path(root) / "cache"


def _version_of(text: str) -> int:
    m = re.match(r"\s*v?(\d+)\.", text or "")
    return int(m.group(1)) if m else 0


def find_node() -> tuple[str, int] | None:
    """(path, major version) of the Node this PC has, or None.

    `CLIPS_STUDIO_NODE` names one explicitly, like the other CLIPS_STUDIO_*
    overrides in core/binaries.py; else PATH."""
    path = os.environ.get("CLIPS_STUDIO_NODE") or shutil.which("node")
    if not path:
        return None
    try:
        out = spawn([path, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return path, _version_of(out.stdout)


def node_or_error() -> str:
    found = find_node()
    if found is None or found[1] < NODE_MIN:
        raise HyperFramesError(NODE_MESSAGE)
    return found[0]


_WITHHELD_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")


def _withheld(name: str) -> bool:
    """True for a variable their CLI must not inherit: the engine's session
    key (plugins/session.py) or anything named like a key or token."""
    upper = name.upper()
    return upper == "CLIPS_KITTY_SESSION_SECRET" or upper.endswith(_WITHHELD_SUFFIXES)


def env(root: Path | None = None, base: dict | None = None) -> dict:
    """The environment every HyperFrames run gets.

    Their CLI reads these (docs/packages/cli.mdx): agent mode (JSON, no
    prompts), no telemetry or usage beacon, no update check, no agent skills
    installed into the project, no optional installs. FFmpeg is the app's own.
    While it renders, a composition's page can't reach this PC or private
    addresses (their restricted mode); public sites stay reachable.
    The engine's session secret and any *_TOKEN, *_KEY, *_SECRET or
    *_PASSWORD variable are left out: their CLI never needs them."""
    from core.binaries import ffmpeg, ffprobe

    root = Path(root) if root is not None else vendor_root()
    out = {name: value for name, value in (os.environ if base is None else base).items()
           if not _withheld(name)}
    out.update({
        "HYPERFRAMES_AGENT": "1",
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HYPERFRAMES_NO_UPDATE_CHECK": "1",
        "HYPERFRAMES_SKIP_SKILLS": "1",
        "HYPERFRAMES_NO_OPTIONAL_INSTALL": "1",
        "HYPERFRAMES_FFMPEG_PATH": ffmpeg(),
        "HYPERFRAMES_FFPROBE_PATH": ffprobe(),
        "HYPERFRAMES_CACHE_DIR": str(cache_dir(root)),
        "PRODUCER_RESTRICT_PAGE_NETWORK": "true",
    })
    return out


def _ebusy(result) -> bool:
    text = f"{getattr(result, 'stderr', '') or ''}\n{getattr(result, 'stdout', '') or ''}"
    return "EBUSY" in text


def run_cli(args: list[str], cwd: Path, root: Path | None = None, timeout: float = 600):
    """Run their CLI once (again on EBUSY, #4058). Returns the CompletedProcess."""
    root = Path(root) if root is not None else vendor_root()
    script = cli_script(root)
    if not script.exists():
        raise HyperFramesError(NOT_INSTALLED)
    node = node_or_error()
    cmd = [node, str(script), *args]
    result = None
    for attempt in range(1, EBUSY_ATTEMPTS + 1):
        try:
            result = spawn(cmd, cwd=str(cwd), env=env(root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise HyperFramesError(f"HyperFrames took longer than {int(timeout)} seconds and was stopped.") from e
        if result.returncode == 0 or not _ebusy(result) or attempt == EBUSY_ATTEMPTS:
            return result
        sleep(EBUSY_WAIT * attempt)
    return result


# ---- their catalog and guide ---------------------------------------------------

# Their own tags for blocks made to sit over footage. The catalog with no search
# words lists these, so a model isn't handed 200 full-screen scenes; a search
# (`query`) covers the whole catalog.
OVER_FOOTAGE_TAGS = ("overlay", "lower-third", "social", "title-card", "captions")

# Their canvas presets (`init --resolution`), by the clip's size.
PRESETS = {(1080, 1920): "portrait", (1920, 1080): "landscape", (1080, 1080): "square"}

_variables_cache: dict[str, list[dict]] = {}


def _json_out(text: str):
    """The JSON document their CLI printed (`--json`), ignoring log lines."""
    text = text or ""
    for opener in ("{", "["):
        at = text.find(opener)
        while at != -1:
            try:
                return json.loads(text[at:])
            except ValueError:
                at = text.find(opener, at + 1)
    raise HyperFramesError("HyperFrames answered in a way Clips Kitty couldn't read.")


def _scratch() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="clipskitty-hf-"))


def catalog(query: str = "", root: Path | None = None) -> list[dict]:
    """Their catalog's blocks (`hyperframes catalog --type block --json`):
    name, title, description, tags, size and length. With no search words,
    only the blocks they tag for laying over footage."""
    work = _scratch()
    try:
        args = ["catalog", "--type", "block", "--json"] + ([query] if query.strip() else [])
        result = run_cli(args, cwd=work, root=root, timeout=120)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if result.returncode != 0:
        raise HyperFramesError("HyperFrames couldn't read its catalog. Check the internet connection.")
    items = _json_out(result.stdout)
    items = items if isinstance(items, list) else items.get("items", [])
    if not query.strip():
        items = [i for i in items if set(i.get("tags") or []) & set(OVER_FOOTAGE_TAGS)]
    return [
        {k: i.get(k) for k in ("name", "title", "description", "tags", "dimensions", "duration")}
        for i in items if i.get("name")
    ]


_VARIABLES = re.compile(r"data-composition-variables\s*=\s*'([^']*)'", re.S)


def declared_variables(html: str) -> list[dict]:
    """The variables a composition declares (`data-composition-variables`)."""
    m = _VARIABLES.search(html or "")
    if not m:
        return []
    try:
        found = json.loads(m.group(1))
    except ValueError:
        return []
    return [v for v in found if isinstance(v, dict) and v.get("id")] if isinstance(found, list) else []


def new_project(workdir: Path, size: tuple[int, int], root: Path | None = None) -> Path:
    """A blank project from their own `init`, at the clip's canvas."""
    preset = PRESETS.get(tuple(size))
    if preset is None:
        raise HyperFramesError(
            f"Animations work on 1080x1920, 1920x1080 and 1080x1080 clips, not {size[0]}x{size[1]}.")
    result = run_cli(["init", "anim", "--example", "blank", "--non-interactive", "--skip-skills",
                      "--resolution", preset], cwd=workdir, root=root, timeout=120)
    project = Path(workdir) / "anim"
    if result.returncode != 0 or not (project / "index.html").exists():
        raise HyperFramesError("HyperFrames couldn't start a project. " + _tail(result))
    return project


def add_block(project: Path, block: str, variables: dict | None = None, root: Path | None = None) -> dict:
    """Their `hyperframes add <block>`: the block's files go into the project.
    Returns their JSON summary plus the block's size, length and variables."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", block or ""):
        raise HyperFramesError(f"{block!r} isn't a block name from the catalog.")
    args = ["add", block, "--dir", str(project), "--json", "--no-clipboard"]
    if variables:
        args += ["--vars", json.dumps(variables)]
    result = run_cli(args, cwd=project, root=root, timeout=180)
    if result.returncode != 0:
        raise HyperFramesError(f"HyperFrames couldn't add the block {block!r}. "
                               "Check the name with hyperframes_catalog, and the internet connection.")
    out = _json_out(result.stdout)
    if not out.get("ok", True):
        raise HyperFramesError(f"HyperFrames couldn't add the block {block!r}.")
    snippet = str(out.get("snippet") or "")

    def attr(name: str, default: str) -> str:
        m = re.search(rf'{name}="([^"]*)"', snippet)
        return m.group(1) if m and m.group(1) else default

    comp = Path(project) / "compositions" / f"{block}.html"
    html = comp.read_text(encoding="utf-8") if comp.exists() else ""
    return {
        **out,
        "width": int(float(attr("data-width", "1920"))),
        "height": int(float(attr("data-height", "1080"))),
        "duration": float(attr("data-duration", "5")),
        "variables": declared_variables(html),
    }


def block_variables(block: str, root: Path | None = None) -> list[dict]:
    """A block's variables, from the composition `hyperframes add` installs."""
    if block not in _variables_cache:
        work = _scratch()
        try:
            project = new_project(work, (1920, 1080), root=root)
            _variables_cache[block] = add_block(project, block, root=root)["variables"]
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return _variables_cache[block]


def block_root(block: str, info: dict, size: tuple[int, int], duration: float, variables: dict | None) -> str:
    """index.html hosting one catalog block over a transparent root at the
    clip's size. A block of another shape is fitted inside, centred: their
    blocks are drawn for 1920x1080 or 1080x1920, and a clip is either."""
    w, h = size
    bw, bh = info["width"], info["height"]
    scale = min(w / bw, h / bh)
    left, top = round((w - bw * scale) / 2), round((h - bh * scale) / 2)
    values = json.dumps(variables or {}).replace("'", "&#39;")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={w}, height={h}" />
    <style>
      html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: transparent; }}
      #root {{ position: relative; width: {w}px; height: {h}px; background: transparent; }}
      #fit {{ position: absolute; left: {left}px; top: {top}px; width: {bw}px; height: {bh}px;
              transform: scale({scale:.6f}); transform-origin: 0 0; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-no-timeline data-start="0" data-duration="{duration:g}"
         data-width="{w}" data-height="{h}">
      <div id="fit">
        <div id="anim" class="clip" data-composition-id="{block}" data-composition-src="compositions/{block}.html"
             data-start="0" data-duration="{duration:g}" data-track-index="0" data-width="{bw}" data-height="{bh}"
             data-variable-values='{values}'></div>
      </div>
    </div>
  </body>
</html>
"""


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def _read(root: Path, rel: str) -> str:
    try:
        return (package_dir(root) / "dist" / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def guide(root: Path | None = None) -> str:
    """A short how-to, drawn from their own docs inside the installed package:
    the render contract (skills/hyperframes/SKILL.md), cards over footage
    (references/overlays.md) and variables (docs/compositions.md)."""
    root = Path(root) if root is not None else vendor_root()
    skill = _read(root, "skills/hyperframes/SKILL.md")
    overlays = _read(root, "skills/hyperframes/references/overlays.md")
    comps = _read(root, "docs/compositions.md")
    if not skill:
        raise HyperFramesError(NOT_INSTALLED)
    parts = [
        "HyperFrames animation on a Clips Kitty clip, from HyperFrames' own guide.",
        "Write ONE complete index.html. The root element is "
        '<div id="root" data-composition-id="main" data-start="0" data-duration="<seconds>" '
        'data-width="<clip width>" data-height="<clip height>">, and html, body and #root have '
        "background: transparent, or the animation hides the clip. Load GSAP with "
        '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>, '
        'build one paused timeline and register it: window.__timelines["main"] = tl.',
        "Their contract:\n" + _section(skill, "Contract"),
        "Cards over footage:\n" + _section(overlays, "Pitfalls"),
        "Variables:\n" + _section(comps, "Variables")[:700],
        "A catalog block instead: hyperframes_catalog lists them, add_animation with block and "
        "variables puts one on the clip.",
    ]
    return "\n\n".join(p for p in parts if p.strip())


# ---- checking and rendering ----------------------------------------------------


def _tail(result) -> str:
    text = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
    return text[-400:]


def _errors_in(report: dict) -> list[str]:
    found = []
    for part in (report, *(report.get(k) or {} for k in ("lint", "runtime", "layout"))):
        for f in part.get("findings") or []:
            if f.get("severity") == "error":
                hint = f" Fix: {f['fixHint']}" if f.get("fixHint") else ""
                found.append(f"{f.get('code', 'error')}: {f.get('message', '')}{hint}".strip())
    return list(dict.fromkeys(found))


def validate(project: Path, root: Path | None = None) -> list[str]:
    """Their `lint --json`, then `check --json` (lint, runtime and layout in
    headless Chrome). The errors, in their words; empty when it passes.
    Warnings are theirs to weigh and never stop a render."""
    lint = run_cli(["lint", "--json"], cwd=project, root=root, timeout=120)
    errors = _errors_in(_json_out(lint.stdout)) if (lint.stdout or "").strip() else []
    if errors:
        return errors
    check = run_cli(["check", "--json"], cwd=project, root=root, timeout=600)
    if not (check.stdout or "").strip():
        return [f"check failed: {_tail(check)}"] if check.returncode != 0 else []
    report = _json_out(check.stdout)
    errors = _errors_in(report)
    if not errors and report.get("ok") is False and check.returncode != 0:
        errors = [f"check failed: {_tail(check)}"]
    return errors


def render(project: Path, out: Path, fps: float = 30, root: Path | None = None, timeout: float = 1800) -> Path:
    """Their `render --format mov`: ProRes 4444 with alpha, at the project's
    canvas (the clip's size) and the clip's frame rate."""
    fps_arg = str(int(round(fps))) if abs(fps - round(fps)) < 0.01 else f"{fps:.3f}"
    result = run_cli(["render", "--format", "mov", "--fps", fps_arg, "--quiet", "-o", str(out)],
                     cwd=project, root=root, timeout=timeout)
    if result.returncode != 0 or not Path(out).exists():
        raise HyperFramesError("HyperFrames couldn't render the animation. " + _tail(result))
    return Path(out)


def alpha_cover(mov: Path, at: float) -> float:
    """How much of the frame at `at` seconds the animation covers, 0 to 1,
    from its alpha channel (0 = fully transparent, 1 = a solid frame)."""
    from core.binaries import ffmpeg

    out = spawn([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(mov), "-frames:v", "1",
                 "-vf", "alphaextract,scale=64:64", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=120)
    data = out.stdout or b""
    return (sum(data) / (255 * len(data))) if data else 0.0


# ---- the AI writes the composition ------------------------------------------


# Ollama's generate() keeps answers to 1,024 tokens for clip picking; a whole
# composition needs more room.
OLLAMA_ANSWER_TOKENS = 4096


def _ask(llm, prompt: str) -> str:
    """One answer from the app's own provider system (llm/), so an OpenRouter
    request carries the app's attribution headers like every other one."""
    from llm.ollama_backend import OllamaBackend

    if isinstance(llm, OllamaBackend):
        return llm.generate(prompt, max_tokens=OLLAMA_ANSWER_TOKENS)
    return llm.generate(prompt)


def extract_html(text: str) -> str:
    """The HTML document in a model's answer, without code fences or prose."""
    text = text or ""
    fenced = re.search(r"```(?:html)?\s*(.*?)```", text, re.S | re.I)
    if fenced and "<" in fenced.group(1):
        text = fenced.group(1)
    low = text.lower()
    starts = [i for i in (low.find("<!doctype"), low.find("<html")) if i != -1]
    end = low.rfind("</html>")
    if not starts or end == -1:
        return ""
    return text[min(starts):end + len("</html>")].strip()


# What the box says when a written animation still fails after its one retry:
# their raw lint codes sent the model off inventing causes ("audio", "try
# again later"), so it gets one plain sentence instead.
WRITE_FAILED = ("The AI model couldn't write a working animation for that. Try a catalog block, "
                "simpler words, or a stronger model.")


def skeleton(size: tuple[int, int], duration: float) -> str:
    """A minimal composition that passes their checks, for the model to fill:
    a transparent root, one timed element, start values set with gsap.set."""
    w, h = size
    d = f"{duration:g}"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <style>
      html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: transparent; }}
      #root {{ position: relative; width: {w}px; height: {h}px; background: transparent; }}
      #card {{ position: absolute; left: 0; right: 0; bottom: 200px; text-align: center; }}
      #card .box {{ display: inline-block; padding: 24px 48px; border-radius: 24px; background: #e62117;
                    color: #fff; font: 700 64px sans-serif; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{d}" data-width="{w}" data-height="{h}">
      <div id="card" class="clip" data-start="0" data-duration="{d}" data-track-index="0">
        <div class="box">SUBSCRIBE</div>
      </div>
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      gsap.set("#card .box", {{ opacity: 0, scale: 0.6 }});
      tl.to("#card .box", {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(2)" }}, 0);
      tl.to("#card .box", {{ opacity: 0, duration: 0.35, ease: "power2.in" }}, {max(0.0, duration - 0.4):g});
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>"""


def writing_prompt(request: str, size: tuple[int, int], duration: float, guide_text: str,
                   previous: str = "", errors: list[str] | None = None) -> str:
    w, h = size
    lines = [
        "You write HyperFrames compositions: HTML that HyperFrames renders to video.",
        guide_text,
        f"Make this animation, to lay over a {w}x{h} video clip: {request.strip()}",
        f"The root is {w}x{h} and lasts {duration:g} seconds (data-duration=\"{duration:g}\"). "
        "Everything outside the animation stays transparent: the clip shows through.",
        "Clips Kitty lays the clip UNDERNEATH your animation itself. So: no <video>, <audio> or "
        "<img> of the clip (no media at all), html, body and #root keep background: transparent, "
        "and set every start value (opacity, x, y, scale, rotation) with gsap.set or the timeline, "
        "never with a CSS transform: GSAP animates transforms, and a CSS transform on the same "
        "element fails their checks.",
        "Start from this working file and change only what the request needs (the elements, "
        "their words, colours and tweens). Keep its structure: the root, each timed element with "
        "class=\"clip\", data-start and data-duration, one paused timeline registered as "
        "window.__timelines[\"main\"]:\n" + skeleton(size, duration),
        "Answer with the complete index.html only, from <!doctype html> to </html>.",
    ]
    if previous and errors:
        lines += [
            "Your last answer failed HyperFrames' checks with these errors. Fix them and answer "
            "with the whole corrected file:",
            "\n".join(f"- {e}" for e in errors[:12]),
            "Your last answer:",
            previous,
        ]
    return "\n\n".join(lines)


def check_variables(block: str, given: dict | None, declared: list[dict]) -> None:
    """A block's words only show when they go in its own variables; an unknown
    name is ignored by their renderer and the demo text plays instead. Say
    which names it has, so the model tries again with those."""
    unknown = [k for k in (given or {}) if k not in {v.get("id") for v in declared}]
    if not unknown:
        return
    names = ", ".join(str(v.get("id")) for v in declared)
    missing = ", ".join(unknown)
    if not names:
        raise HyperFramesError(f"{block} has no variables, so it has no {missing}: it plays as designed. "
                               "Add it without variables, or pick another block.")
    raise HyperFramesError(f"{block} has no {missing}. Its words go in: {names}. "
                           "Use those names in variables and try again.")


# ---- one animation, made and rendered ------------------------------------------


def make(spec: dict, *, size: tuple[int, int], fps: float, out_dir: Path, llm_factory=None,
         root: Path | None = None) -> dict:
    """Make one animation as a transparent MOV in `out_dir` and describe it.

    `spec` holds one of: `html` (a composition in their format), `block` with
    `variables` (a catalog block), or `request` (plain words, written by the
    model `llm_factory()` returns, which goes through the app's own providers).
    Plus `start` (seconds into the clip) and, for a block or a request,
    `duration`.
    """
    import uuid

    start = max(0.0, float(spec.get("start") or 0))
    duration = spec.get("duration")
    duration = float(duration) if duration not in (None, "") else None
    if duration is not None and not 0.2 <= duration <= 120:
        raise HyperFramesError("An animation lasts between 0.2 and 120 seconds.")
    timings: dict[str, float] = {}
    t0 = time.monotonic()
    work = _scratch()
    try:
        project = new_project(work, size, root=root)
        if spec.get("block"):
            info = add_block(project, spec["block"], spec.get("variables") or None, root=root)
            check_variables(spec["block"], spec.get("variables"), info["variables"])
            duration = duration or info["duration"]
            (project / "index.html").write_text(
                block_root(spec["block"], info, size, duration, spec.get("variables")), encoding="utf-8")
            kind, label = "block", spec["block"]
        elif spec.get("html"):
            html = str(spec["html"])
            (project / "index.html").write_text(html, encoding="utf-8")
            m = re.search(r'data-duration="([\d.]+)"', html)
            duration = float(m.group(1)) if m else (duration or 0)
            kind, label = "html", "custom"
        elif spec.get("request"):
            if llm_factory is None:
                raise HyperFramesError("No AI model is set up to write the animation.")
            duration = duration or 4.0
            llm = llm_factory()
            guide_text = guide(root)
            html = extract_html(_ask(llm, writing_prompt(spec["request"], size, duration, guide_text)))
            (project / "index.html").write_text(html, encoding="utf-8")
            errors = validate(project, root=root) if html else ["The answer had no HTML document in it."]
            if errors:
                # Once more with their errors; after that, a plain answer.
                retry = extract_html(_ask(llm, writing_prompt(spec["request"], size, duration, guide_text,
                                                             previous=html or "(no HTML)", errors=errors)))
                if not retry:
                    raise HyperFramesError(WRITE_FAILED)
                (project / "index.html").write_text(retry, encoding="utf-8")
            kind, label = "request", str(spec["request"])[:80]
            timings["write"] = time.monotonic() - t0
        else:
            raise HyperFramesError("Give one of: html (a HyperFrames composition), block (from the catalog), "
                                   "or request (what the animation should be).")

        t1 = time.monotonic()
        errors = validate(project, root=root)
        timings["check"] = time.monotonic() - t1
        if errors:
            if kind == "request":
                # Written by the model and already retried once: their raw
                # codes only send the model off inventing causes.
                log.info("hyperframes: written animation still fails: %s", " | ".join(errors[:5]))
                raise HyperFramesError(WRITE_FAILED)
            raise HyperFramesError("The animation didn't pass HyperFrames' checks: " + " | ".join(errors[:5]))

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"anim-{uuid.uuid4().hex[:12]}.mov"
        t2 = time.monotonic()
        mov = render(project, out_dir / name, fps=fps, root=root)
        timings["render"] = time.monotonic() - t2
        if duration and alpha_cover(mov, min(duration / 2, max(0.0, duration - 0.05))) > 0.97:
            mov.unlink(missing_ok=True)
            raise HyperFramesError("The animation has a solid background, so it would hide the clip. "
                                   "Its html, body and root need background: transparent.")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "file": name, "start": round(start, 3), "duration": round(float(duration or 0), 3),
        "kind": kind, "label": label, "seconds": {k: round(v, 1) for k, v in timings.items()},
    }


# ---- laying the animations over a finished clip --------------------------------


def animations_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "animations"


def overlay_graph(starts: list[float]) -> str:
    """Each animation shifted to its start and laid over the clip with its
    alpha; when one ends the clip shows through again (eof_action=pass)."""
    parts, prev = [], "[0:v]"
    for i, start in enumerate(starts, start=1):
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS+{start:.3f}/TB[a{i}]")
        out = "[vout]" if i == len(starts) else f"[v{i}]"
        parts.append(f"{prev}[a{i}]overlay=0:0:eof_action=pass:format=auto{out}")
        prev = out
    return ";".join(parts)


def apply_overlays(video_path: Path, overlays: list, folder: Path) -> int:
    """Burn the animations into the clip IN PLACE, one extra encode, after the
    image watermark (core/pipeline.py). Returns how many were laid.

    Each entry names a file in the animations folder; a name that isn't a
    plain file there, or a file that's gone, is skipped and said so."""
    from core.binaries import ffmpeg
    from core.paths import discard, safe_name, within
    from video.encoding import final_movflags, video_encoder_args

    folder = Path(folder)
    found: list[tuple[Path, float]] = []
    for item in overlays or []:
        if not isinstance(item, dict):
            continue
        name = safe_name(str(item.get("file") or ""))
        path = folder / name if name else None
        if path is None or not within(folder, path) or not path.is_file():
            print(f"      (animation {item.get('file')!r} is missing; the clip is made without it)")
            continue
        try:
            start = max(0.0, float(item.get("start") or 0))
        except (TypeError, ValueError):
            start = 0.0
        found.append((path, start))
    if not found:
        return 0

    tmp = Path(video_path).with_suffix(".anim.mp4")
    cmd = [ffmpeg(), "-y", "-i", str(Path(video_path).resolve())]
    for path, _ in found:
        cmd += ["-i", str(path.resolve())]
    cmd += [
        "-filter_complex", overlay_graph([s for _, s in found]),
        "-map", "[vout]", "-map", "0:a?",
        *video_encoder_args(),
        "-c:a", "copy",
        *final_movflags(),
        str(tmp.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        discard(tmp)
        raise RuntimeError(f"animation overlay failed:\n{result.stderr[-1500:]}")
    from video.outro import _replace_with_retry

    placed = _replace_with_retry(tmp, Path(video_path), label="Animation")
    discard(tmp)
    if not placed:
        raise RuntimeError(f"animation: {Path(video_path).name} is open elsewhere and could not be written")
    return len(found)
