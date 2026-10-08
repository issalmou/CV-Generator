"""
CV Assistant - CV Generation
Centralised NVIDIA NIM API client with caching, rate-limiting and logging.

All LLM interactions MUST go through this module.
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from threading import Lock

from dotenv import load_dotenv
load_dotenv()

from dataclasses import replace

from config import DEFAULT_LLM_MODELS, settings
from services import profiling
from services.llm import BaseLLMProvider, LLMError, build_active_provider
from services.llm import circuit as _circuit
from services.llm import providers as _providers
from services.llm import routing as _routing
from services.llm.base import _sanitize
from services.llm.routing import RouteStep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM CLIENT  (multi-provider: NVIDIA / OpenAI / Gemini / OpenAI-compatible /
# custom — see services/llm/). Everything configurable lives in
# `config.Settings`; this module keeps the SHA-256 response cache, the
# per-minute rate limit and the call/char counters, and delegates the actual
# API call to the active `BaseLLMProvider`. The provider is rebuilt on
# `reset_client()` (after an admin changes the config, and by tests).
# ---------------------------------------------------------------------------

NVIDIA_MODELS_FALLBACK = list(DEFAULT_LLM_MODELS)   # kept for backward compat

_provider: BaseLLMProvider | None = None
_CLIENT_LOCK: Lock = Lock()


def _active_provider() -> BaseLLMProvider:
    global _provider
    with _CLIENT_LOCK:
        if _provider is None:
            _provider = build_active_provider()
        return _provider


def _get_client():
    """The active provider's underlying OpenAI-compatible client (compat shim
    for older call sites / tests)."""
    return getattr(_active_provider(), "client", None)


def reset_client() -> None:
    """Drop the cached provider(s) + resolved routes so the next call rebuilds
    everything from current settings (after an admin config change, and by tests)."""
    global _provider
    with _CLIENT_LOCK:
        _provider = None
    _providers.reset_instances()
    _routing.reset()
    _circuit.reset()


def _ordered_steps(route) -> list[RouteStep]:
    """Route steps reordered for this call:
      1. the sticky last-good step for this request_type (if still in the route);
      2. steps whose circuit is CLOSED, in route order;
      3. steps whose circuit is OPEN — still tried, as a genuine last resort.
    """
    steps = list(route.steps)
    lg = _circuit.last_good(route.request_type)
    if lg is not None:
        lg_step = RouteStep(lg[0], lg[1])
        if lg_step in steps:
            steps = [lg_step] + [s for s in steps if s != lg_step]
    closed = [s for s in steps if not _circuit.is_open(s.provider, s.model)]
    opened = [s for s in steps if _circuit.is_open(s.provider, s.model)]
    if opened:
        logger.info("[llm.route] %s: %d step(s) skipped first pass (circuit open)",
                    route.request_type, len(opened))
    return closed + opened


def _run_route(request_type: str, system: str, prompt: str, *,
               response_format: dict | None, temperature: float | None):
    """Walk the routed provider/model chain for ``request_type``. Returns
    ``(Completion, winning_provider_name)``. Raises :class:`LLMError` when every
    step fails.

    Per constraint #3: per-request_type timeout (from the route), controlled
    retry (each step tried once — no double SDK retry, ``max_retries=0``), fast
    fail-over to the next step on ANY provider error (429 / 5xx / timeout
    included), a circuit breaker per (provider, model), and a sticky last-good
    so a recovered fallback is kept rather than re-probing a sick primary."""
    route = _routing.resolve(request_type)
    eff_temp = temperature if temperature is not None else route.temperature
    steps = _ordered_steps(route)
    last: Exception | None = None
    for i, step in enumerate(steps):
        prov = _providers.build_for(step.provider, step.model, timeout=route.timeout)
        if not prov.configured:
            continue
        try:
            comp = prov.complete(system, prompt,
                                 response_format=response_format, temperature=eff_temp)
        except LLMError as exc:
            last = exc
            _circuit.record_failure(step.provider, step.model)
            logger.warning("[llm.route] %s step %d/%d (%s) failed: %s",
                           request_type, i + 1, len(steps), step, _sanitize(str(exc)))
            continue
        _circuit.record_success(step.provider, step.model, request_type=request_type)
        return replace(comp, attempts=i + 1, fallback_used=bool(i)), step.provider
    raise LLMError(f"no model available for {request_type!r} (last error: {last})")


# ---------------------------------------------------------------------------
# CACHE / RATE LIMIT / STATS (UNCHANGED)
# ---------------------------------------------------------------------------

_RESPONSE_CACHE: dict[str, str] = {}
_CACHE_LOCK: Lock = Lock()

_CALL_TIMESTAMPS: list[float] = []
_RATE_LOCK: Lock = Lock()

_CALL_STATS: dict[str, int | float] = {
    "total_calls": 0,
    "cache_hits": 0,
    "total_input_chars": 0,
    "total_output_chars": 0,
    "total_duration_ms": 0.0,
    "fallback_calls": 0,
    "error_calls": 0,
}

_STATS_LOCK: Lock = Lock()


# ---------------------------------------------------------------------------
# INIT (kept for compatibility)
# ---------------------------------------------------------------------------

def _get_model() -> list[str]:
    """The active provider's model fallback chain."""
    return _active_provider().models or NVIDIA_MODELS_FALLBACK


def is_configured() -> bool:
    return settings.llm_configured


def get_config() -> dict:
    """Safe LLM configuration snapshot for the admin dashboard — **never the
    API key**. Keys: provider, base_url, model, fallback_models, temperature,
    max_output_tokens, request_timeout, max_calls_per_minute,
    available_providers, configured."""
    return settings.llm_public_config()


def healthcheck(prompt: str = "ping") -> dict:
    """Live check of the active LLM provider — a minimal completion, timeout +
    fallback chain respected. Returns ``{provider, model, duration_ms, ok,
    error}`` with the error **sanitised** (never a key or URL). Rate-limited."""
    try:
        _check_rate_limit()
    except RuntimeError as exc:
        return {"provider": _active_provider().name, "model": None,
                "duration_ms": 0, "ok": False, "error": str(exc)}
    h = _active_provider().healthcheck(prompt=prompt)
    return {"provider": h.provider, "model": h.model,
            "duration_ms": h.duration_ms, "ok": h.ok, "error": h.error}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _cache_key(prompt: str, *, request_type: str = "generic",
               json_schema: dict | None = None, temperature: float | None = None) -> str:
    """Cache key = prompt + everything that changes the model's output:
    the routed request_type, whether a strict schema was enforced, and any
    per-call temperature override."""
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(request_type.encode("utf-8"))
    if json_schema:
        h.update(b"\x00schema:")
        h.update(str(json_schema.get("name", "1")).encode("utf-8"))
    if temperature is not None:
        h.update(f"\x00t={temperature}".encode("utf-8"))
    return h.hexdigest()


def _check_rate_limit() -> None:
    now = time.time()
    with _RATE_LOCK:
        cutoff = now - 60.0
        while _CALL_TIMESTAMPS and _CALL_TIMESTAMPS[0] < cutoff:
            _CALL_TIMESTAMPS.pop(0)

        rpm = settings.LLM_MAX_CALLS_PER_MINUTE
        if len(_CALL_TIMESTAMPS) >= rpm:
            raise RuntimeError(f"[LLMClient] Rate limit reached: {rpm} calls/min")

        _CALL_TIMESTAMPS.append(now)


def _update_stats(input_text: str, output_text: str) -> None:
    with _STATS_LOCK:
        _CALL_STATS["total_calls"] += 1
        _CALL_STATS["total_input_chars"] += len(input_text)
        _CALL_STATS["total_output_chars"] += len(output_text)


# ---------------------------------------------------------------------------
# MAIN API (SAME FUNCTION NAME → call_gemini)
# ---------------------------------------------------------------------------

def call_gemini(
    prompt: str,
    *,
    request_type: str = "generic",
    use_cache: bool = True,
    json_schema: dict | None = None,
    temperature: float | None = None,
) -> str:
    """
    Sole LLM entrypoint for every consumer module (name kept for history).

    ``json_schema`` — a strict JSON-Schema *envelope* (``{name, strict,
    schema}``, from ``schemas.llm_schemas.json_schema_for``). When given, the
    active provider enforces it as ``response_format`` if the routed model
    supports it, and downgrades transparently otherwise. The caller still
    validates the returned text and may issue one repair retry.
    ``temperature`` — per-call override (structured calls pass ``0.0``).
    """
    _t0 = time.perf_counter()
    key = _cache_key(prompt, request_type=request_type,
                     json_schema=json_schema, temperature=temperature)
    response_format = (
        {"type": "json_schema", "json_schema": json_schema} if json_schema else None
    )

    # ---------------- CACHE ----------------
    if use_cache:
        with _CACHE_LOCK:
            cached = _RESPONSE_CACHE.get(key)

        if cached is not None:
            with _STATS_LOCK:
                _CALL_STATS["cache_hits"] += 1

            logger.debug(
                "[NVIDIAClient] Cache HIT | request_type=%s",
                request_type,
            )
            profiling.record_llm_call(
                request_type=request_type, provider="<prompt-cache>", model=None,
                duration_ms=(time.perf_counter() - _t0) * 1000.0, cache_hit=True, ok=True,
            )
            return cached

    provider = _active_provider()
    if not provider.configured:
        raise RuntimeError("[LLMClient] No API key configured for the active LLM provider.")

    # ---------------- RATE LIMIT ----------------
    try:
        _check_rate_limit()
    except RuntimeError as exc:
        profiling.record_llm_call(
            request_type=request_type, provider=provider.name, model=None,
            duration_ms=(time.perf_counter() - _t0) * 1000.0, ok=False,
            error_kind="RateLimit",
        )
        with _STATS_LOCK:
            _CALL_STATS["error_calls"] += 1
        raise

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "[LLMClient] API call | provider=%s | request_type=%s | timestamp=%s | prompt_chars=%d",
        provider.name, request_type, ts, len(prompt),
    )

    # ---------------- CALL ----------------
    # Routing ON (default): walk the request_type's provider/model chain
    #   (config.DEFAULT_LLM_ROUTING + LLM_ROUTING_OVERRIDES).
    # Routing OFF: the single LLM_PROVIDER (legacy path — used by tests).
    system_prompt = "You are a professional CV writer and career expert."
    try:
        if settings.LLM_ROUTING_ENABLED:
            completion, provider_name = _run_route(
                request_type, system_prompt, prompt,
                response_format=response_format, temperature=temperature,
            )
        else:
            completion = provider.complete(system_prompt, prompt,
                                           response_format=response_format,
                                           temperature=temperature)
            provider_name = provider.name
    except LLMError as exc:
        profiling.record_llm_call(
            request_type=request_type, provider=provider.name, model=None,
            duration_ms=(time.perf_counter() - _t0) * 1000.0, ok=False,
            error_kind=type(exc).__name__,
        )
        with _STATS_LOCK:
            _CALL_STATS["error_calls"] += 1
        raise RuntimeError(f"No LLM model available. Last error: {exc}") from exc

    result = completion.text
    model_used = completion.model
    logger.info("[LLMClient] Model used: %s/%s", provider_name, model_used)

    # ---------------- STATS + PROFILE ----------------
    _dur_ms = (time.perf_counter() - _t0) * 1000.0
    _update_stats(prompt, result)
    with _STATS_LOCK:
        _CALL_STATS["total_duration_ms"] = float(_CALL_STATS["total_duration_ms"]) + _dur_ms
        if completion.fallback_used:
            _CALL_STATS["fallback_calls"] += 1
    profiling.record_llm_call(
        request_type=request_type, provider=provider_name, model=model_used,
        duration_ms=_dur_ms,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        max_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
        attempts=completion.attempts, fallback_used=completion.fallback_used,
        cache_hit=False, ok=True,
    )

    logger.info(
        "[LLMClient] Response received | request_type=%s | output_chars=%d",
        request_type, len(result),
    )

    # ---------------- CACHE STORE ----------------
    if use_cache:
        with _CACHE_LOCK:
            _RESPONSE_CACHE[key] = result

    return result


# ---------------------------------------------------------------------------
# STATS
# ---------------------------------------------------------------------------

def get_stats() -> dict[str, int]:
    """
    Snapshot of the client's call/cache counters. Exposed through
    ``GET /api/stats`` so operators can watch LLM usage without an external
    metrics backend.

    Keys: ``total_calls``, ``cache_hits``, ``total_input_chars``,
    ``total_output_chars``, ``total_duration_ms``, ``fallback_calls``,
    ``error_calls``, ``cache_size``. All integer-valued (durations are
    rounded to whole milliseconds).
    """
    with _STATS_LOCK:
        stats = {k: int(round(v)) for k, v in _CALL_STATS.items()}

    stats["cache_size"] = len(_RESPONSE_CACHE)
    return stats