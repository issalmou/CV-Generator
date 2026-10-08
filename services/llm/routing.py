"""Centralised ``request_type -> [provider/model] chain`` resolution (LOT 3).

The single place the task→model mapping is computed. ``DEFAULT_LLM_ROUTING``
(in ``config``) is merged with the ``LLM_ROUTING_OVERRIDES`` env JSON, steps
whose provider has no key / is disabled are dropped, and the result is cached
per ``request_type`` until :func:`reset` (called after an admin config change,
and by tests).

Nothing here builds a client or calls an LLM — ``gemini_client`` consumes the
:class:`Route` and drives the actual calls (with the circuit breaker, LOT 4).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from threading import Lock

from config import DEFAULT_LLM_ROUTING, settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, "Route"] = {}
_LOCK = Lock()


@dataclass(frozen=True)
class RouteStep:
    provider: str
    model: str

    def __str__(self) -> str:  # "groq/openai/gpt-oss-120b"
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class Route:
    request_type: str
    steps: tuple[RouteStep, ...]
    temperature: float | None
    timeout: float | None

    @property
    def primary(self) -> RouteStep:
        return self.steps[0]


def _overrides() -> dict:
    raw = (settings.LLM_ROUTING_OVERRIDES or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("[llm.routing] LLM_ROUTING_OVERRIDES is not valid JSON — ignored")
        return {}


def _timeout_overrides() -> dict:
    raw = (settings.LLM_ROUTING_TIMEOUT_OVERRIDES or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _merged_table() -> dict:
    table = {k: dict(v) for k, v in DEFAULT_LLM_ROUTING.items()}
    for rt, entry in _overrides().items():
        if isinstance(entry, dict):
            table[rt] = {**table.get(rt, {}), **entry}
    return table


def _usable(step: RouteStep, disabled: set[str]) -> bool:
    if step.provider in disabled:
        return False
    if not settings.provider_has_key(step.provider):
        return False
    return True


def _fallback_step() -> RouteStep:
    """Last resort: the single global provider from settings."""
    models = settings.llm_models
    return RouteStep(settings.llm_provider, models[0] if models else "")


def resolve(request_type: str) -> Route:
    """The effective provider/model chain for ``request_type``.

    Always returns at least one step. When routing is disabled, or nothing in
    the table is usable, the single global ``LLM_PROVIDER`` is used."""
    cached = _CACHE.get(request_type)
    if cached is not None:
        return cached

    with _LOCK:
        cached = _CACHE.get(request_type)
        if cached is not None:
            return cached

        if not settings.LLM_ROUTING_ENABLED:
            route = Route(request_type, (_fallback_step(),),
                          settings.LLM_TEMPERATURE, settings.LLM_REQUEST_TIMEOUT)
            _CACHE[request_type] = route
            return route

        table = _merged_table()
        entry = table.get(request_type) or table.get("generic") or {}
        disabled = set(settings.llm_disabled_providers_list)

        raw_chain = entry.get("chain") or []
        steps: list[RouteStep] = []
        for pair in raw_chain:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            step = RouteStep(str(pair[0]).strip().lower(), str(pair[1]).strip())
            if _usable(step, disabled) and step not in steps:
                steps.append(step)

        if not steps:
            # every configured step is unusable (no key / disabled) -> global default
            fb = _fallback_step()
            logger.warning("[llm.routing] no usable step for %r — using global default %s",
                           request_type, fb)
            steps = [fb]

        temp = entry.get("temperature", None)
        timeout = _timeout_overrides().get(request_type)
        route = Route(request_type, tuple(steps),
                      temp if temp is not None else None,
                      float(timeout) if timeout else settings.LLM_REQUEST_TIMEOUT)
        _CACHE[request_type] = route
        return route


def reset() -> None:
    """Drop the resolved-route cache (after an admin LLM config change / in tests)."""
    with _LOCK:
        _CACHE.clear()


def as_public_dict() -> dict[str, list[str]]:
    """request_type -> ["provider/model", ...] for the admin view. No keys."""
    out: dict[str, list[str]] = {}
    table = _merged_table()
    for rt in sorted(set(table) | set(DEFAULT_LLM_ROUTING)):
        try:
            out[rt] = [str(s) for s in resolve(rt).steps]
        except Exception:  # noqa: BLE001
            continue
    return out
