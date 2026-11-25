"""Which model the Ask Clips Kitty box runs on: its own picker.

Like the model list in VS Code's AI chat panel. A dropdown in the box lists
every model the person can use, in Colin's order: Ollama (this PC) first, then
OpenRouter, then each direct provider (tier 3) and plan sign-in. A provider
with no key is listed too, with "Add your key in Settings".

The box's choice is the box's alone. It is kept in its own flag and never
touches `llm.backend`, so the model that picks clips, the sidebar switcher and
the Dashboard chip stay exactly as they were set.

Nothing here talks to a provider in a new way. Ollama is asked what it has
(/api/tags) and what each model can do (/api/show, as agent.can_call_tools
always has); the cloud lists come from Settings → AI's own cached fetch
(server/ai_api.py), and a run goes through the provider's existing adapter.
"""

from __future__ import annotations

import concurrent.futures
import threading

from llm.spec import LOCAL, parse_spec

# The box's own choice, as a full spec ("ollama/gemma4:e4b",
# "openrouter/google/gemma-4-26b-a4b-it"). Empty: follow the app's model.
CHOICE_FLAG = "agent_model"

# How long the list waits for the providers. One that is slower is listed as
# still loading; its fetch carries on and is cached for the next look.
FETCH_SECONDS = 20.0

ADD_KEY = "Add your key in Settings"
SIGN_IN = "Sign in in Settings"
LOADING = "Still loading this list. Open it again in a moment."
COULD_NOT_LIST = "Couldn't load the models. Try again in a moment."
PLAN_NO_TOOLS = "The box can't use this plan yet. Choose another model."
OLLAMA_DOWN = "Ollama isn't running, so this PC's models aren't listed."
OLLAMA_EMPTY = "No models are installed yet. Install one on the Models page."
OLLAMA_NOT_ANSWERING = ("Ollama isn't answering right now, so this PC's models can't be checked. Try again "
                        "in a moment. If it keeps saying this, restart Clips Kitty, or choose a model with "
                        "your own key in the list.")


def loopback(host: str) -> str:
    """The Ollama address with "localhost" spelled 127.0.0.1, for the box's
    own calls. On Windows "localhost" can try IPv6 first and wait about two
    seconds per call before falling back, which made choosing a model look
    ready for seconds before its check answered. The pipeline's own setting
    is left as it is."""
    import urllib.parse

    host = host.rstrip("/")
    try:
        parts = urllib.parse.urlsplit(host)
        if (parts.hostname or "").lower() != "localhost":
            return host
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        return urllib.parse.urlunsplit(parts._replace(netloc=netloc))
    except ValueError:
        return host


def full_spec(spec: str) -> str:
    """"gemma:7b" and "ollama/gemma:7b" are the same model: one spelling."""
    provider, model = parse_spec(spec)
    return f"{provider}/{model}" if model else ""


class Box:
    def __init__(self, *, config: dict, db, data_dir, ollama_host: str, list_models,
                 vram_gb=lambda: None):
        """`list_models(provider_id)` returns Settings → AI's model list for a
        keyed provider (dicts with id, name, tools, note), cached there."""
        self.config = config
        self.db = db
        self.data_dir = data_dir
        self.ollama_host = loopback(ollama_host)
        self.list_models = list_models
        self.vram_gb = vram_gb
        # (name, modified_at) -> can call tools. A model re-pulled under the
        # same name gets a new modified_at and is asked again.
        self._tools_seen: dict[tuple[str, str], bool] = {}
        self._lock = threading.Lock()

    # ---- the choice ---------------------------------------------------------

    def active(self) -> str:
        """The app's model, the one that picks clips."""
        return full_spec(self.config.get("llm", {}).get("backend") or "")

    def chosen(self) -> str:
        d = self.db()
        try:
            return d.get_flag(CHOICE_FLAG, "")
        finally:
            d.close()

    def choose(self, spec: str) -> str:
        """Remember the box's model. "" goes back to following the app's."""
        spec = full_spec(spec) if spec.strip() else ""
        d = self.db()
        try:
            d.set_flag(CHOICE_FLAG, spec)
        finally:
            d.close()
        return spec

    def current(self) -> str:
        return self.chosen() or self.active()

    # ---- Ollama ---------------------------------------------------------------

    def _can_call_tools(self, name: str, stamp: str) -> bool:
        from server.agent import can_call_tools

        key = (name, stamp)
        with self._lock:
            if key in self._tools_seen:
                return self._tools_seen[key]
        answer = can_call_tools(self.ollama_host, name)
        with self._lock:
            self._tools_seen[key] = answer
        return answer

    def _tools_known(self, name: str) -> bool:
        """Whether `name` can call tools, from what the list already asked
        when it can (the picker has just shown it), else asked now and kept."""
        from server.agent import can_call_tools

        with self._lock:
            seen = [answer for (seen_name, _), answer in self._tools_seen.items() if seen_name == name]
        if seen:
            return seen[-1]
        answer = can_call_tools(self.ollama_host, name)
        with self._lock:
            self._tools_seen[(name, "")] = answer
        return answer

    def _ollama_group(self) -> dict:
        import requests

        group = {"id": LOCAL, "label": "Ollama (this PC)", "tier": 1, "local": True,
                 "has_key": True, "settings": False, "message": "", "models": []}
        try:
            r = requests.get(f"{self.ollama_host}/api/tags", timeout=10)
            r.raise_for_status()
            entries = r.json().get("models") or []
        except Exception:
            group["message"] = OLLAMA_DOWN
            return group
        names = [(str(e.get("name") or ""), str(e.get("modified_at") or e.get("digest") or ""))
                 for e in entries if isinstance(e, dict) and e.get("name")]
        # Gemma 4 first, as agent.usable_model orders them: what the box was
        # built around and what the Models page recommends.
        names.sort(key=lambda n: (not n[0].startswith("gemma4"), n[0]))
        group["models"] = [{"spec": f"{LOCAL}/{name}", "name": name, "tools": self._can_call_tools(name, stamp),
                            "note": ""} for name, stamp in names]
        if not group["models"]:
            group["message"] = OLLAMA_EMPTY
        return group

    # ---- cloud ----------------------------------------------------------------

    def _cloud_models(self, spec) -> list[dict]:
        listed = self.list_models(spec.id) or []
        models = [{"spec": f"{spec.id}/{m['id']}", "name": str(m.get("name") or m["id"]),
                   "tools": bool(m.get("tools")), "note": str(m.get("note") or "")}
                  for m in listed if m.get("id")]
        # The provider's own pick for the job first (OpenRouter: Gemma 4
        # 26B-A4B), as Settings → AI lists it.
        preferred = (spec.preferred or {}).get("text", "")
        models.sort(key=lambda m: m["spec"] != f"{spec.id}/{preferred}")
        return models

    def groups(self) -> list[dict]:
        """Every group in the box's order, each with its models or a message."""
        from core.scrub import redact_secrets
        from llm.providers import keys
        from llm.providers.base import LLMError
        from llm.providers.catalog import PROVIDERS
        from llm.signin import catalog as signin

        cloud = sorted(PROVIDERS.values(), key=lambda s: s.tier)  # stable: OpenRouter, then tier 3
        groups: list[dict] = []
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        try:
            jobs = {LOCAL: pool.submit(self._ollama_group)}
            for spec in cloud:
                if keys.has_key(self.data_dir, spec.id):
                    jobs[spec.id] = pool.submit(self._cloud_models, spec)
            concurrent.futures.wait(list(jobs.values()), timeout=FETCH_SECONDS)

            local = jobs[LOCAL]
            groups.append(local.result() if local.done() and not local.exception() else
                          {"id": LOCAL, "label": "Ollama (this PC)", "tier": 1, "local": True, "has_key": True,
                           "settings": False, "message": LOADING if not local.done() else OLLAMA_DOWN,
                           "models": []})
            for spec in cloud:
                group = {"id": spec.id, "label": spec.label, "tier": spec.tier, "local": False,
                         "has_key": spec.id in jobs, "settings": False, "message": "", "models": []}
                job = jobs.get(spec.id)
                if job is None:
                    group["message"], group["settings"] = ADD_KEY, True
                elif not job.done():
                    group["message"] = LOADING
                elif job.exception() is not None:
                    e = job.exception()
                    group["message"] = (redact_secrets(e.message)[:300] if isinstance(e, LLMError)
                                        else COULD_NOT_LIST)
                else:
                    group["models"] = job.result()
                groups.append(group)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        # Plans signed in to instead of a key. The box needs tool calling,
        # which a plan sign-in can't do yet, so it is listed and says so.
        for plan in signin.all_for(self.data_dir):
            try:
                if not plan.available():
                    continue
                signed_in = bool(plan.status().get("signed_in"))
            except Exception:
                continue
            groups.append({"id": plan.id, "label": plan.label, "tier": 3, "local": False,
                           "has_key": signed_in, "settings": not signed_in,
                           "message": PLAN_NO_TOOLS if signed_in else SIGN_IN, "models": []})
        return groups

    def listing(self) -> dict:
        groups = self.groups()
        return {"groups": groups, "chosen": self.chosen(), "active": self.active(),
                "default": self.default(groups)}

    def default(self, groups: list[dict]) -> str:
        """The box's model: its own choice, else the app's model, else the
        first model on this PC that can use tools.

        A choice whose list is still loading, or whose provider isn't
        answering, is kept: the status line says why it can't run yet.
        A cloud model is never picked by itself (it costs money and sends
        text off the PC): only when the person chose it in the box, or it is
        the app's own model. With nothing usable, the app's model stays and
        the status offers Gemma 4 or a model from the list."""
        listed = {m["spec"]: m for g in groups for m in g["models"]}
        unlisted = {g["id"] for g in groups if not g["models"]}

        def keep(spec: str) -> bool:
            if not spec:
                return False
            if spec in listed:
                return bool(listed[spec]["tools"])
            return parse_spec(spec)[0] in unlisted

        for spec in (self.chosen(), self.active()):
            if keep(spec):
                return spec
        local = [m["spec"] for g in groups if g.get("local") for m in g["models"] if m["tools"]]
        if local:
            return local[0]
        return self.chosen() or self.active() or ""

    # ---- before a run ---------------------------------------------------------

    def check(self, spec: str = "") -> dict:
        """Whether the box can run on `spec` (default: its current model), in
        the shape /agent/status has always answered with, plus `settings`:
        the provider whose key or sign-in Settings → AI should open, or ""."""
        from llm.providers import keys
        from llm.providers.catalog import get
        from llm.signin import catalog as signin
        from server.agent import install_offer, usable_model

        explicit = bool(spec) or bool(self.chosen())
        spec = full_spec(spec) if spec else self.current()
        provider, model = parse_spec(spec)
        out = {"ready": False, "model": spec, "configured": self.active(), "reason": "",
               "install": None, "settings": ""}

        if provider == LOCAL:
            if not explicit:
                # Following the app's model, as the box always has: that model
                # if it can use tools, else any installed one that can.
                found = usable_model(self.ollama_host, model)
                if found:
                    return {**out, "ready": True, "model": f"{LOCAL}/{found}"}
            elif model and self._tools_known(model):
                return {**out, "ready": True}
            if not self._ollama_answers():
                out["reason"] = OLLAMA_NOT_ANSWERING
                return out
            other = usable_model(self.ollama_host, "")
            if explicit and model:
                if other:
                    out["reason"] = (f"{model} can't use tools, which this box needs. Choose a model marked "
                                     f"\"Uses tools\" in the list, such as {other}.")
                    return out
                out["reason"] = (f"{model} can't use tools, which this box needs. Install Gemma 4 below, or "
                                 "choose OpenRouter or another provider with your own key in the list.")
            else:
                out["reason"] = ("No installed model can use tools. Install Gemma 4 on the Models page: "
                                 "gemma4:e4b for a 6 GB graphics card, gemma4:e2b for less. "
                                 "Or use OpenRouter with your own key in Settings → AI.")
            if not other:
                out["install"] = install_offer(self.vram_gb())
            return out

        if signin.is_signin(provider):
            out["reason"] = ("The box can't use your ChatGPT plan yet. Choose Ollama, OpenRouter or "
                             "another provider with your own key in the list.")
            return out

        cloud = get(provider)
        if cloud is None or not model:
            out["reason"] = "Choose a model in the list."
            return out
        # The catalog's own id, never the text that came in with the request.
        if not keys.has_key(self.data_dir, cloud.id):
            out["reason"] = f"Add your {cloud.key_label} in Settings → AI to use {cloud.label} here."
            out["settings"] = cloud.id
            return out
        # Whether the provider says this model can call tools. A slow or failed
        # list doesn't stop the run: the provider's own answer will say why.
        listed = self._quick_list(cloud)
        entry = next((m for m in listed if m["spec"] == spec), None)
        if entry is not None and not entry["tools"]:
            better = next((m["name"] for m in listed if m["tools"]), "")
            out["reason"] = (f"{entry['name']} can't use tools on {cloud.label}, which this box needs. "
                             "Choose a model marked \"Uses tools\" in the list"
                             + (f", such as {better}." if better else "."))
            return out
        return {**out, "ready": True}

    def _ollama_answers(self) -> bool:
        import requests

        try:
            requests.get(f"{self.ollama_host}/api/version", timeout=5).raise_for_status()
            return True
        except Exception:
            return False

    def _quick_list(self, spec) -> list[dict]:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            job = pool.submit(self._cloud_models, spec)
            return job.result(timeout=FETCH_SECONDS)
        except Exception:
            return []
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def backend_config(self, spec: str) -> dict:
        """The llm config a run on `spec` uses: the app's, with only the model
        swapped, and never written anywhere."""
        llm = dict(self.config.get("llm") or {})
        llm["backend"] = full_spec(spec)
        llm["data_dir"] = llm.get("data_dir") or str(self.data_dir)
        return llm
