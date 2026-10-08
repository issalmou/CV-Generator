"""The **single** LLM provider implementation.

Every backend — nvidia / openai / gemini / mistral / groq / openai_compatible /
custom — speaks the OpenAI Chat Completions API, so there is exactly **one**
class: :class:`OpenAICompatibleProvider`. A "provider" is just a configuration
``(name, base_url, api_key, model)``; "OpenAI-compatible" is the transport, not
a reason for N classes.

``build_active_provider()`` reads ``config.settings`` (`LLM_PROVIDER` + the
per-provider block, falling back to the generic ``LLM_*``) and returns one
configured instance.
"""

from __future__ import annotations

import logging
from threading import Lock as _Lock

from openai import OpenAI

from config import DEFAULT_LLM_MODELS, _LLM_BLOCKS, LLM_PROVIDER_NAMES, settings
from services.llm.base import BaseLLMProvider, LLMError

logger = logging.getLogger(__name__)

PROVIDER_NAMES = LLM_PROVIDER_NAMES   # ("nvidia","openai","gemini","mistral","groq","openai_compatible","custom")

# --- structured-output capability (from benchmarks/bakeoff capability probes) ---
# A model id (or a substring of it) -> the best response_format it honours.
# "json_schema" implies json_object; unknown models are assumed json_object-only,
# which is itself downgraded to a plain call if the API rejects it (the caller
# always validates + can repair, so a downgrade never ships bad data silently).
_JSON_SCHEMA_MODELS: tuple[str, ...] = (
    "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3", "qwen3",
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite",
    "gpt-4o", "gpt-4.1", "o3", "o4",
    "mistral-small", "mistral-medium", "mistral-large",
)
_NO_JSON_MODELS: tuple[str, ...] = (
    "nvidia/nemotron", "nemotron",
)


def _looks_like_response_format_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "response_format" in msg or "json_schema" in msg or "json mode" in msg or (
        "400" in msg and "schema" in msg)


def _supported_response_format(model: str, requested: dict | None) -> dict | None:
    """Downgrade ``requested`` to what ``model`` is known to support."""
    if not requested:
        return None
    m = (model or "").lower()
    kind = requested.get("type")
    if any(tok in m for tok in _NO_JSON_MODELS):
        return None
    if kind == "json_schema" and not any(tok in m for tok in _JSON_SCHEMA_MODELS):
        return {"type": "json_object"}
    return requested


class OpenAICompatibleProvider(BaseLLMProvider):
    """One implementation for every OpenAI-compatible LLM endpoint."""

    def __init__(self, *, name: str, base_url: str, api_key: str, models: list[str],
                 temperature: float, max_tokens: int, timeout: float) -> None:
        self.name = name
        super().__init__(base_url=base_url, api_key=api_key, models=models,
                         temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        # placeholder key avoids a constructor raise when unconfigured;
        # ``complete`` refuses up front via ``configured``.
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-configured",
            timeout=timeout,
            # No SDK-level retry: the routing layer (gemini_client._run_route)
            # owns fallback + the circuit breaker. A double retry here would
            # multiply latency on a 429/5xx before the fast fail-over runs.
            max_retries=0,
        )

    @property
    def client(self) -> OpenAI:            # compat shim for gemini_client._get_client
        return self._client

    def _complete_one(self, model: str, system: str, prompt: str, *,
                      response_format: dict | None = None,
                      temperature: float | None = None) -> tuple[str, dict]:
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": self._max_tokens,
        }
        rf = _supported_response_format(model, response_format)
        if rf:
            kwargs["response_format"] = rf
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            # A model that advertises support but rejects this particular
            # response_format: retry once WITHOUT it. Not a silent quality
            # downgrade — the caller validates the JSON and repairs if needed.
            if rf is not None and _looks_like_response_format_error(exc):
                kwargs.pop("response_format", None)
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise
        try:
            text = (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError) as exc:
            raise LLMError(f"malformed completion response: {exc!r}") from exc
        usage: dict = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", None),
                "completion_tokens": getattr(u, "completion_tokens", None),
            }
        return text, usage


# --- helpers -----------------------------------------------------------------

def _first(*vals: str) -> str:
    for v in vals:
        if v:
            return v
    return ""


def _model_chain(primary: str, fallback_csv: str) -> list[str]:
    extra = [m.strip() for m in (fallback_csv or "").replace("\n", ",").split(",") if m.strip()]
    chosen = [m for m in [primary.strip(), *extra] if m]
    out: list[str] = []
    for m in chosen or DEFAULT_LLM_MODELS:
        if m not in out:
            out.append(m)
    return out


def provider_config(name: str) -> dict:
    """Resolve the config for ``name`` from ``settings`` — the per-provider
    block wins over the generic ``LLM_*``. Never returns the key to a caller
    that would expose it; used only to build the client."""
    key = (name or "nvidia").strip().lower()
    if key not in _LLM_BLOCKS:
        logger.warning("[llm] unknown provider %r — falling back to nvidia", key)
        key = "nvidia"
    key_attr, model_attr, url_attr, default_url, default_model = _LLM_BLOCKS[key]
    return {
        "name": key,
        "base_url": _first(getattr(settings, url_attr, ""), settings.LLM_BASE_URL, default_url),
        "api_key": _first(getattr(settings, key_attr, ""), settings.LLM_API_KEY),
        "models": _model_chain(
            _first(getattr(settings, model_attr, ""), settings.LLM_MODEL, default_model),
            settings.LLM_MODELS_FALLBACK,
        ),
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
        "timeout": settings.LLM_REQUEST_TIMEOUT,
    }


def build_active_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(**provider_config(settings.LLM_PROVIDER))


def build_provider(name: str) -> OpenAICompatibleProvider:
    """Build a specific provider (used by tests / an admin probe of a
    not-yet-active provider)."""
    return OpenAICompatibleProvider(**provider_config(name))


# --- routing: one client instance per (provider, model) ---------------------

_INSTANCE_CACHE: dict[tuple, OpenAICompatibleProvider] = {}
_INSTANCE_LOCK = _Lock()


def build_for(provider: str, model: str, *, timeout: float | None = None) -> OpenAICompatibleProvider:
    """A single-model :class:`OpenAICompatibleProvider` for one routing step.

    Cached by ``(provider, model, timeout)`` so the fan-out reuses one HTTP
    client per (provider, model). ``temperature`` is passed per call, not
    baked in, so it is not part of the cache key."""
    cfg = provider_config(provider)
    to = timeout if timeout is not None else cfg["timeout"]
    key = (cfg["name"], model, to)
    inst = _INSTANCE_CACHE.get(key)
    if inst is not None:
        return inst
    with _INSTANCE_LOCK:
        inst = _INSTANCE_CACHE.get(key)
        if inst is None:
            inst = OpenAICompatibleProvider(
                name=cfg["name"], base_url=cfg["base_url"], api_key=cfg["api_key"],
                models=[model] if model else cfg["models"],
                temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], timeout=to,
            )
            _INSTANCE_CACHE[key] = inst
        return inst


def reset_instances() -> None:
    """Drop cached per-(provider,model) clients — after an admin config change / tests."""
    with _INSTANCE_LOCK:
        _INSTANCE_CACHE.clear()
