"""Shared helpers for the provider bake-off (Phase 2a).

ISOLATED benchmark tooling — imported by nothing in production. It reuses
`services.llm.providers.provider_config` (read-only) to resolve each
provider's base_url + api_key from the existing .env, and the production
prompt builders (read-only) so the benchmark compares models on the EXACT
prompts the app uses. No production behaviour, routing, prompt or endpoint
is modified.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict

from openai import OpenAI

from services.llm.providers import provider_config

PROVIDERS = ("nvidia", "gemini", "mistral", "groq")

SYSTEM_PROMPT = "You are a professional CV writer and career expert."  # == production call_gemini


def client_for(provider: str) -> tuple[OpenAI, str, bool]:
    """Return (client, base_url, configured) for a provider, from the .env."""
    cfg = provider_config(provider)
    key = cfg["api_key"]
    return (
        OpenAI(base_url=cfg["base_url"], api_key=key or "not-configured",
               timeout=90.0, max_retries=0),
        cfg["base_url"],
        bool(key),
    )


@dataclass
class CallResult:
    provider: str
    model: str
    task: str
    ok: bool = False
    error: str | None = None
    wall_s: float = 0.0
    ttft_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tok_per_s: float | None = None
    response_chars: int = 0
    json_mode: str = "none"            # none | json_object | json_schema
    json_valid: bool | None = None
    text: str = ""                     # raw output, kept for scoring / human read
    scores: dict = field(default_factory=dict)

    def slim(self) -> dict:
        d = asdict(self)
        d["text"] = self.text[:6000]
        return d


def _sanitize_err(e: Exception) -> str:
    import re
    s = f"{type(e).__name__}: {e}"
    s = re.sub(r"\b(sk-|nvapi-|AIza|gsk_)[A-Za-z0-9_\-]{6,}", "[key]", s)
    s = re.sub(r"\b[A-Za-z0-9]{32,}\b", "[token]", s)
    return s[:300]


def run_call(provider: str, model: str, task: str, prompt: str, *,
             temperature: float = 0.3, max_tokens: int = 4096,
             json_mode: str = "none", json_schema: dict | None = None,
             stream_ttft: bool = True) -> CallResult:
    """One benchmarked completion. Never raises — failures are recorded."""
    r = CallResult(provider=provider, model=model, task=task, json_mode=json_mode)
    cli, _, configured = client_for(provider)
    if not configured:
        r.error = "no api key in .env"
        return r

    kwargs: dict = dict(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    elif json_mode == "json_schema" and json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": task, "schema": json_schema, "strict": True},
        }

    t0 = time.perf_counter()
    try:
        if stream_ttft:
            chunks: list[str] = []
            usage = None
            first_t = None
            with cli.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kwargs) as s:
                for ev in s:
                    if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
                        if first_t is None:
                            first_t = time.perf_counter()
                        chunks.append(ev.choices[0].delta.content)
                    if getattr(ev, "usage", None):
                        usage = ev.usage
            r.text = "".join(chunks)
            r.ttft_s = round(first_t - t0, 2) if first_t else None
            if usage is not None:
                r.prompt_tokens = getattr(usage, "prompt_tokens", None)
                r.completion_tokens = getattr(usage, "completion_tokens", None)
        else:
            resp = cli.chat.completions.create(**kwargs)
            r.text = (resp.choices[0].message.content or "")
            u = getattr(resp, "usage", None)
            if u is not None:
                r.prompt_tokens = getattr(u, "prompt_tokens", None)
                r.completion_tokens = getattr(u, "completion_tokens", None)
        r.wall_s = round(time.perf_counter() - t0, 2)
        r.response_chars = len(r.text)
        r.ok = bool(r.text.strip())
        if r.completion_tokens and r.wall_s:
            r.tok_per_s = round(r.completion_tokens / r.wall_s, 1)
    except Exception as e:  # noqa: BLE001 — benchmark must not crash
        r.wall_s = round(time.perf_counter() - t0, 2)
        r.error = _sanitize_err(e)
        return r

    # JSON validity for structured tasks
    if json_mode != "none" or task in _STRUCTURED_TASKS:
        r.json_valid = _json_ok(r.text)
    return r


_STRUCTURED_TASKS = {
    "extract_experience", "extract_education", "extract_projects", "extract_skills",
    "profile_analysis", "ats_keyword_extraction", "ats_content_optimization",
}


def _json_ok(text: str) -> bool:
    import re
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        json.loads(t)
        return True
    except Exception:
        return False


def strip_fences(text: str) -> str:
    import re
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t).strip()
    return t
