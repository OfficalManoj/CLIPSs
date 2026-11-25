"""Animations on clips (HyperFrames, video/hyperframes.py), for the MCP tools.

The routes make the animation; laying it on the clip is the existing re-render
(POST /clips/{id}/render with render_opts["overlays"]), so the source video is
never touched and a clip with no animations renders exactly as before.

    GET  /animations/status            is HyperFrames set up (Node 22+, installed)
    GET  /animations/catalog           their catalog blocks (?query=, ?block= for one's variables)
    GET  /animations/guide             a short how-to from their own docs
    GET  /clips/{id}/animations        the animations a clip has now
    POST /clips/{id}/animations        make one (html | block + variables | request) as a MOV

Every model call goes through the app's own provider system (llm/registry.py),
so a request to OpenRouter carries the app's attribution headers
(llm/providers/openrouter.attribution_headers) like every other one.
"""

import json
import subprocess
import traceback
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel


class AnimationIn(BaseModel):
    html: str | None = None          # a composition in HyperFrames' format
    block: str | None = None         # a catalog block's name
    variables: dict | None = None    # the block's variables
    request: str | None = None       # plain words, for the model to write
    start: float = 0.0               # seconds into the clip
    duration: float | None = None    # for a block or a request
    # The model that writes a request, as a backend spec ("ollama/gemma4:e4b",
    # "openrouter/google/gemma-4-26b-a4b-it"); else the app's chosen model.
    model: str | None = None


def model_spec(config: dict, chosen: str | None) -> str:
    """The backend spec a request is written with: the caller's model (the text
    box's), else the app's chosen one. A bare Ollama model name means Ollama."""
    spec = (chosen or (config.get("llm") or {}).get("backend") or "").strip()
    if spec and "/" not in spec:
        spec = f"ollama/{spec}"
    return spec


# server/agent.py run() talks to Ollama with this context size. A request the
# box makes uses the same one, so Ollama doesn't reload the model around it.
BOX_NUM_CTX = 16384


def llm_for(config: dict, chosen: str | None):
    """A backend from the app's own provider system, for one request."""
    from llm.registry import create_backend
    from video.hyperframes import HyperFramesError

    spec = model_spec(config, chosen)
    if not spec:
        raise HyperFramesError("No AI model is chosen. Pick one in Settings > AI.")
    llm = dict(config.get("llm") or {})
    if chosen and spec.startswith("ollama/"):
        llm["num_ctx"] = max(BOX_NUM_CTX, int(llm.get("num_ctx") or 0))
    try:
        return create_backend({**llm, "backend": spec})
    except ValueError as e:
        raise HyperFramesError(f"That AI model can't be used here: {e}") from e


def _probe(path: Path) -> tuple[tuple[int, int], float, float]:
    """(size, fps, duration) of a finished clip."""
    from core.binaries import ffprobe

    out = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    data = json.loads(out.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    num, _, den = str(stream.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    size = (int(stream.get("width") or 0), int(stream.get("height") or 0))
    duration = float((data.get("format") or {}).get("duration") or 0)
    return size, fps or 30.0, duration


def install(app, *, config, db, data_dir) -> None:
    from video import hyperframes as hf

    data_path = Path(data_dir)

    def _fail(e: Exception) -> HTTPException:
        return HTTPException(400, str(getattr(e, "message", None) or e)[:600])

    def _clip(clip_id: int):
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such clip")
        return row

    def _overlays(row) -> list:
        opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
        return [o for o in (opts.get("overlays") or []) if isinstance(o, dict)]

    @app.get("/animations/status")
    def animations_status():
        root = hf.vendor_root()
        node = hf.find_node()
        if node is None or node[1] < hf.NODE_MIN:
            return {"ready": False, "message": hf.NODE_MESSAGE}
        if not hf.cli_script(root).exists():
            return {"ready": False, "message": hf.NOT_INSTALLED}
        return {"ready": True, "version": hf.installed_version(root), "node": node[1]}

    @app.get("/animations/catalog")
    def animations_catalog(query: str = "", block: str = ""):
        try:
            if block:
                return {"block": block, "variables": hf.block_variables(block)}
            return {"blocks": hf.catalog(query)}
        except hf.HyperFramesError as e:
            raise _fail(e) from e

    @app.get("/animations/guide")
    def animations_guide():
        try:
            return {"guide": hf.guide()}
        except hf.HyperFramesError as e:
            raise _fail(e) from e

    @app.get("/clips/{clip_id}/animations")
    def clip_animations(clip_id: int):
        row = _clip(clip_id)
        return {"clip_id": clip_id, "video_id": row["video_id"], "start_s": row["start_s"],
                "end_s": row["end_s"], "overlays": _overlays(row)}

    @app.post("/clips/{clip_id}/animations")
    def make_animation(clip_id: int, body: AnimationIn):
        """Make one animation for this clip, as a transparent MOV in the data
        folder. Returns it and the clip's overlays with it added, for the
        re-render (POST /clips/{id}/render) that lays them on the clip."""
        from llm.providers.base import LLMError

        row = _clip(clip_id)
        path = Path(row["path"]) if row["path"] else None
        if path is None or not path.exists():
            raise HTTPException(404, "The clip's video file is missing, so nothing can go on it.")
        size, fps, length = _probe(path)
        if length and body.start >= length:
            raise HTTPException(400, f"The clip is {length:.1f} seconds long, so an animation can't start at "
                                     f"{body.start:g} seconds.")
        spec = body.model_dump(exclude_none=True)
        try:
            made = hf.make(spec, size=size, fps=fps, out_dir=hf.animations_dir(data_path),
                           llm_factory=lambda: llm_for(config, body.model))
        except (hf.HyperFramesError, LLMError) as e:
            raise _fail(e) from e
        except Exception as e:
            # The console gets the whole thing; the reply stays plain.
            traceback.print_exc()
            raise HTTPException(500, "The animation could not be made. The backend log has the details.") from e
        overlay = {k: made[k] for k in ("file", "start", "duration", "kind", "label")}
        return {"overlay": overlay, "overlays": [*_overlays(row), overlay], "seconds": made["seconds"],
                "video_id": row["video_id"], "start_s": row["start_s"], "end_s": row["end_s"]}
