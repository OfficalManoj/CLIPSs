"""The assistant behind the app's Gemma box.

Type "clip this stream, then upload them an hour apart with #mychannel" and a
local model works out which of the engine's own tools to call, in order, and
does it. The tools are exactly the ones server/mcp.py exposes to outside
agents: one list, one set of descriptions, one place to fix a mistake.

Three rules hold it together.

**Posting is not something the model can do.** The tools that post or
schedule (HUMAN_ONLY) are never offered here, and queue_video's
publish_when_done is never taken from the model. The model may build a plan;
the window shows it as a card, and posting takes a person pressing its button.
Thirty posts cannot be taken back, so the confirmation is structural rather
than a matter of the model behaving. (The MCP server, for outside agents,
still offers all of them: this rule is the box's.)

**It needs a model that can call tools.** Gemma 4 can; the app's default
gemma3:4b cannot, and neither can gemma:7b. Ollama reports this per model, so
the box says which model it is using and says plainly when none can, instead of
producing a confident answer having called nothing.

**Nothing here does the work itself.** Every step is a call to the local API,
the same one the window uses. The model decides what to call and in what order;
the engine still processes video, renders clips and talks to YouTube.
"""

import json

# The model gets a handful of turns to reach an answer. Enough for "find the
# video, list its clips, plan the uploads"; short enough that a model looping on
# itself stops rather than running until the user gives up.
MAX_TURNS = 8
TOOL_OUTPUT_LIMIT = 4000
# What the box says when a model answers with nothing at all.
NO_ANSWER = "The model didn't answer that time. Try again, or ask for one thing at a time."

# Never offered to the model, and refused if it calls one anyway.
#
# This was empty from 2026-09-22 ("it should do what i told it to do"), so
# "process this and publish them all" posted with no click. The plan Colin
# approved on 2026-11-25 for the box puts posting behind the plan card again:
# the model plans (publish_plan, schedule_clips_plan), the window shows the
# card, and the person's click on it posts. The model is told so in plain
# words, so the second half of a request is never dropped in silence.
HUMAN_ONLY: set[str] = {"publish_plan_execute", "schedule_clips_execute", "uploadpost_publish"}

# The planning tools whose structured result the window turns into a card.
PLAN_TOOLS = ("publish_plan", "schedule_clips_plan")

# queue_video's argument for "publish when processing finishes". Taken out of
# what the model sees and out of what it sends: it would post with no click.
DEFERRED_PUBLISH = "publish_when_done"

# What the model is told when it reaches for posting.
NEEDS_CLICK = (
    "Not done: posting needs the person's click on the plan card in the app. "
    "Call publish_plan (YouTube) or schedule_clips_plan (other platforms) so the "
    "card shows, and tell them to press its button. If the clips are still being "
    "made, tell them to ask again once they are done."
)

SYSTEM = (
    "You drive Clips Kitty, a local video clipping app, through its tools.\n"
    "NEVER ask the person for something you can look up. If they describe a "
    "video by its subject or title, call list_videos and match it yourself. If "
    "you need clip ids, call list_clips. Asking for an id you could have "
    "fetched is a failure.\n"
    # "schedule my clips 5 a day" came back as "which clip or video ids?",
    # which a person never knows.
    "When they ask about, post or schedule 'my clips' without naming a "
    "video, call list_videos and use the newest finished one (the first "
    "marked done). If none is finished, or two could be meant, ask which "
    "video by its title. Never ask the person for an id of any kind.\n"
    "Work in steps: call a tool, read the result, call the next one. A request "
    "with two halves needs at least two calls.\n"
    "Processing a video takes tens of minutes: queue it, report the job id, and "
    "do not wait for it.\n"
    "When they say how they want the clips, pass it as arguments rather than "
    "mentioning it in your reply: captions on or off and how they look, "
    "longer clips, a watermark by name, podcast footage, or a horizontal "
    "longform video. Ignoring one silently gives them the wrong render.\n"
    "To publish or schedule, call publish_plan (YouTube) or schedule_clips_plan "
    "(other platforms) and describe what it returns. You cannot post: the app "
    "shows the plan as a card and the person presses its button. Say so.\n"
    "If they ask you to process a video AND publish it, the clips do not exist "
    "yet when you queue the job. Queue it, then say that once the clips are "
    "made they can ask you to plan the posts.\n"
    "If they want particular hashtags on every clip, pass hashtags to "
    "queue_video. Adding them afterwards clip by clip is not the way.\n"
    "When they say what the clips should be about or include (a topic, a "
    "moment, a time range, funny moments), pass their words as focus to "
    "queue_video or queue_local_file. It adds weight to those moments and "
    "never removes others.\n"
    "Never drop part of a request in silence. If you cannot do something they "
    "asked for, say which part and why, in the same reply.\n"
    "Keep answers short and concrete.\n"
    # The box shows text as it comes: Markdown reached the person as raw
    # stars, and "check job_status(job_id=5)" told them to run a function.
    "Write plain sentences for a person, not a developer: no Markdown at all "
    "(no ** or * for bold or bullets, no # headings, no backticks, no tables). "
    "For a list, put each item on its own line. Never name a tool, a function "
    "or its arguments: say what you did, or what they can do in the app, "
    "like 'the Queue page shows its progress'."
)


def where_note(context: str) -> str:
    """Where the person is in the app, from the window (the page, and the clip
    they have open), so 'this clip' means something. Empty: nothing said."""
    context = " ".join(str(context or "").split())[:300]
    if not context:
        return ""
    return (
        f"Where the person is in the app right now: {context}. When they say "
        "'this clip' or 'this video' without an id, they mean that one."
    )


def tool_specs(tools: list[dict]) -> list[dict]:
    """The MCP tool list in the shape Ollama's chat API wants."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": _without_deferred_publish(tool["inputSchema"]),
            },
        }
        for tool in tools
        if tool["name"] not in HUMAN_ONLY
    ]


def _without_deferred_publish(schema: dict) -> dict:
    """The schema with publish_when_done taken out, so the model never sees it.
    A copy: the MCP server's own TOOLS keep it for outside agents."""
    props = (schema or {}).get("properties") or {}
    if DEFERRED_PUBLISH not in props:
        return schema
    return {**schema, "properties": {k: v for k, v in props.items() if k != DEFERRED_PUBLISH}}


# The engine's posting refusals, as the step that makes posting work. The
# model turned "WoopSocial publishing is not enabled" into "let me know when
# that service is reactivated". Only the box sees these words.
REFUSAL_NEXT_STEP = {
    "YouTube publishing is not enabled": "Connect YouTube in Settings to post from here.",
    "WoopSocial publishing is not enabled": "Add your WoopSocial key in Settings to post from here.",
}


def refusal_text(detail: str) -> str:
    """What the box hands the model when the engine says no: the next step
    for a refusal that has one, else the engine's own reason."""
    detail = str(detail or "").strip()
    return REFUSAL_NEXT_STEP.get(detail) or f"That was refused: {detail}"


def with_defaults(arguments: dict, defaults: dict | None) -> dict:
    """queue_video's or queue_local_file's arguments with the bar's defaults
    filled in where the model said nothing (an explicit "no captions" still wins). Never
    publish_when_done, even from the defaults: posting waits for a click."""
    for key, value in (defaults or {}).items():
        if key != DEFERRED_PUBLISH:
            arguments.setdefault(key, value)
    return arguments


def guarded_call(name: str, args: dict, call_tool) -> tuple[str, dict | None]:
    """One tool call from the model, with the posting rule applied: the
    HUMAN_ONLY tools are refused with NEEDS_CLICK, and publish_when_done is
    dropped (and the model told), so nothing posts without a click."""
    if name in HUMAN_ONLY:
        return NEEDS_CLICK, None
    dropped = args.pop(DEFERRED_PUBLISH, None) if isinstance(args, dict) else None
    text, structured = call_tool(name, args)
    if dropped:
        text = f"{text}\n{NEEDS_CLICK}"
    return text, structured


def can_call_tools(host: str, model: str) -> bool:
    """Whether Ollama says this model supports tool calling.

    Asked rather than assumed: the answer differs between gemma3 and gemma4,
    and a model that cannot call tools fails by inventing an answer, which is
    the worst possible failure for something that is supposed to act.
    """
    import requests

    try:
        r = requests.post(f"{host}/api/show", json={"model": model}, timeout=15)
        r.raise_for_status()
        return "tools" in (r.json().get("capabilities") or [])
    except Exception:
        return False


def usable_model(host: str, preferred: str = "") -> str:
    """A tool-capable model that is actually installed, or "".

    Prefers the one already chosen for clip scoring, so most people never think
    about it, and falls back to any installed model that can call tools.
    """
    import requests

    if preferred and can_call_tools(host, preferred):
        return preferred
    try:
        r = requests.get(f"{host}/api/tags", timeout=15)
        r.raise_for_status()
        installed = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return ""
    # Gemma 4 first, by name. It is what this feature was built around, what
    # the Models page recommends, and what the app ships the rest of its AI on:
    # reaching past it for some other installed model would be a surprise.
    ordered = sorted(installed, key=lambda n: (not n.startswith("gemma4"), n))
    for name in ordered:
        if can_call_tools(host, name):
            return name
    return ""


# What a fresh pull of each build downloads, in GB, rounded up. Read off
# Ollama's registry on 2026-10-03 (6.6 and 4.6): a figure to put beside the
# button so nobody starts it on a metered connection unawares, not a promise.
GEMMA4_DOWNLOAD_GB = {"gemma4:e4b": 7, "gemma4:e2b": 5}


def install_offer(vram_gb: float | None) -> dict:
    """The Gemma 4 build the box offers when no installed model can call tools.

    The same two edge builds and the same 6 GB line as RECOMMENDATIONS in
    llm/manager.py: e4b where it fits the card, e2b on anything smaller, which
    includes a PC with no graphics card at all. It is installed beside the
    model that picks the clips, never in place of it, so a 12 GB card is
    offered e4b too: the box needs nothing bigger.
    """
    model = "gemma4:e4b" if vram_gb and vram_gb >= 6 else "gemma4:e2b"
    return {"model": model, "size_gb": GEMMA4_DOWNLOAD_GB[model]}


def run(
    message: str,
    history: list[dict],
    tools: list[dict],
    call_tool,
    host: str,
    model: str,
    num_ctx: int = 16384,
    context: str = "",
) -> dict:
    """One exchange: the model calls tools until it can answer.

    `call_tool(name, arguments)` runs one tool and returns its text, so the
    loop itself never touches the API and stays testable without a network.

    Returns the reply, the steps taken (for the box to show its working) and
    the most recent publishing plan, which the window turns into a Confirm
    button.
    """
    # A model has no clock. Without this, "tomorrow at noon" came back as a
    # date in 2025 and the schedule was refused as being in the past.
    from datetime import datetime

    import requests

    now = datetime.now().astimezone()
    when = (
        f"The time right now is {now.isoformat(timespec='seconds')}. "
        "Work out any 'tomorrow' or 'tonight' from that, and always pass times "
        "in RFC 3339 WITH the offset, like 2026-09-18T12:00:00-05:00."
    )
    messages = [{"role": "system", "content": chr(10).join(x for x in (SYSTEM, when, where_note(context)) if x)}]
    messages += [m for m in history if m.get("role") in ("user", "assistant")]
    messages.append({"role": "user", "content": message})

    specs = tool_specs(tools)
    steps: list[dict] = []
    plan: dict | None = None

    for _ in range(MAX_TURNS):
        r = requests.post(
            f"{host}/api/chat",
            json={
                "model": model, "messages": messages, "tools": specs, "stream": False,
                # The system prompt and the tool list alone are ~3,700 tokens.
                # In Ollama's default 4,096 a thinking model (Gemma 4) ran out
                # of room mid-thought about one time in three, and answered
                # nothing at all: no tool call, no reply.
                "options": {"num_ctx": num_ctx},
            },
            timeout=600,
        )
        r.raise_for_status()
        reply = r.json().get("message") or {}
        messages.append(reply)

        calls = reply.get("tool_calls") or []
        if not calls:
            text = (reply.get("content") or "").strip()
            return {
                # An empty box reads as the app ignoring you. Say so instead.
                "reply": text or NO_ANSWER,
                "steps": steps,
                "plan": plan,
                "model": model,
            }

        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            text, structured = guarded_call(name, args, call_tool)
            if name in PLAN_TOOLS and structured:
                plan = structured
            steps.append({"tool": name, "arguments": args, "result": text[:400]})
            messages.append({"role": "tool", "content": text[:TOOL_OUTPUT_LIMIT]})

    return {
        "reply": (
            "I could not finish that in a reasonable number of steps. "
            "Try asking for one thing at a time."
        ),
        "steps": steps,
        "plan": plan,
        "model": model,
    }


def run_cloud(message: str, history: list[dict], tools: list[dict], call_tool, backend,
              context: str = "") -> dict:
    """The same exchange as run(), on a cloud model with the user's own key.

    run() is the local path and is left exactly as it is. This is its twin for
    a CloudBackend: the same system prompt, the same tools, the same rules
    about publishing, and the same answer shape, with each provider's own
    message format handled inside its adapter (backend.chat).
    """
    from datetime import datetime

    now = datetime.now().astimezone()
    when = (
        f"The time right now is {now.isoformat(timespec='seconds')}. "
        "Work out any 'tomorrow' or 'tonight' from that, and always pass times "
        "in RFC 3339 WITH the offset, like 2026-09-18T12:00:00-05:00."
    )
    messages = [{"role": "system", "content": chr(10).join(x for x in (SYSTEM, when, where_note(context)) if x)}]
    messages += [{"role": m["role"], "content": str(m.get("content") or "")}
                 for m in history if m.get("role") in ("user", "assistant")]
    messages.append({"role": "user", "content": message})

    specs = tool_specs(tools)
    steps: list[dict] = []
    plan: dict | None = None

    for _ in range(MAX_TURNS):
        turn = backend.chat(messages, specs)
        messages.append({
            "role": "assistant",
            "content": turn.text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in turn.tool_calls],
            "raw": turn.raw,
        })
        if not turn.tool_calls:
            # As run(): an empty box reads as the app ignoring you.
            return {"reply": turn.text.strip() or NO_ANSWER, "steps": steps, "plan": plan,
                    "model": backend.name}

        for call in turn.tool_calls:
            args = dict(call.arguments or {})
            text, structured = guarded_call(call.name, args, call_tool)
            if call.name in PLAN_TOOLS and structured:
                plan = structured
            steps.append({"tool": call.name, "arguments": args, "result": text[:400]})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": text[:TOOL_OUTPUT_LIMIT]})

    return {
        "reply": (
            "I could not finish that in a reasonable number of steps. "
            "Try asking for one thing at a time."
        ),
        "steps": steps,
        "plan": plan,
        "model": backend.name,
    }
