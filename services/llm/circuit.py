"""Per-(provider, model) circuit breaker + sticky last-good for LLM routing (LOT 4).

**Scope: per process / per worker.** State lives in a module dict + lock, NOT
in Redis. Each Gunicorn/Uvicorn worker learns provider health independently;
with N workers a sick provider is discovered up to N times before every worker
has tripped — acceptable because tripping is cheap (a few failed calls that
fail over in <1 s) and convergence is fast.

Deliberately NOT backed by the shared cache: LLM routing is a short, hot path
and adding a Redis round-trip per call to share breaker state would cost more
than it saves at today's scale. **Evolve to a shared (cache-backed) breaker —
like ``services/providers/circuit.py`` for job search — only if** a
multi-worker deployment shows repeated, correlated provider incidents where
per-worker rediscovery becomes a real cost.

States: CLOSED -> (``fail_threshold`` consecutive failures) -> OPEN ->
(after ``cooldown``) -> HALF-OPEN -> (1 success) CLOSED | (1 failure) OPEN.

"Sticky last-good": the last (provider, model) that actually answered for a
``request_type`` is tried FIRST on the next call, so once routing has fallen
over to a healthy fallback it stays there instead of hammering a sick primary
every request.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class _Breaker:
    fails: int = 0
    opened_at: float = 0.0
    half_open: bool = False


_BREAKERS: dict[tuple[str, str], _Breaker] = {}
_LAST_GOOD: dict[str, tuple[str, str]] = {}
_LOCK = Lock()


def _key(provider: str, model: str) -> tuple[str, str]:
    return (provider, model)


def is_open(provider: str, model: str) -> bool:
    """True when this (provider, model) is currently tripped and still cooling
    down. A breaker past its cooldown flips to half-open here and returns
    False (one probe allowed)."""
    if not settings.LLM_CIRCUIT_ENABLED:
        return False
    with _LOCK:
        b = _BREAKERS.get(_key(provider, model))
        if b is None or b.opened_at == 0.0:
            return False
        if time.monotonic() - b.opened_at >= settings.LLM_CIRCUIT_COOLDOWN_SECONDS:
            b.half_open = True
            b.opened_at = 0.0
            return False
        return True


def record_success(provider: str, model: str, *, request_type: str | None = None) -> None:
    with _LOCK:
        b = _BREAKERS.get(_key(provider, model))
        if b is not None:
            if b.fails or b.half_open:
                logger.info("[llm.circuit] %s/%s recovered", provider, model)
            b.fails = 0
            b.opened_at = 0.0
            b.half_open = False
        if request_type:
            _LAST_GOOD[request_type] = _key(provider, model)


def record_failure(provider: str, model: str) -> None:
    if not settings.LLM_CIRCUIT_ENABLED:
        return
    with _LOCK:
        b = _BREAKERS.setdefault(_key(provider, model), _Breaker())
        b.fails += 1
        if b.half_open or b.fails >= settings.LLM_CIRCUIT_FAIL_THRESHOLD:
            if b.opened_at == 0.0:
                logger.warning("[llm.circuit] %s/%s OPEN after %d failures", provider, model, b.fails)
            b.opened_at = time.monotonic()
            b.half_open = False


def last_good(request_type: str) -> tuple[str, str] | None:
    with _LOCK:
        return _LAST_GOOD.get(request_type)


def snapshot() -> dict:
    """Read-only state for the admin view — no secrets, just health."""
    now = time.monotonic()
    with _LOCK:
        return {
            "breakers": {
                f"{p}/{m}": {
                    "fails": b.fails,
                    "open": b.opened_at != 0.0
                    and now - b.opened_at < settings.LLM_CIRCUIT_COOLDOWN_SECONDS,
                    "half_open": b.half_open,
                }
                for (p, m), b in _BREAKERS.items()
            },
            "last_good": {rt: f"{p}/{m}" for rt, (p, m) in _LAST_GOOD.items()},
        }


def reset() -> None:
    with _LOCK:
        _BREAKERS.clear()
        _LAST_GOOD.clear()
