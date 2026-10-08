"""
A small circuit breaker shared across workers via the application cache.

State per provider (key ``v{V}:jobs:circuit:{name}``):
``{"failures": int, "open_until": epoch_seconds}``.

- ``allow()`` — False while ``open_until`` is in the future (breaker OPEN),
  then True again (HALF-OPEN: the next call is a trial).
- ``record_failure()`` — after ``JOB_CIRCUIT_FAIL_THRESHOLD`` consecutive
  failures the breaker opens for ``JOB_CIRCUIT_COOLDOWN_SECONDS``.
- ``record_success()`` — resets the counter and closes the breaker.
"""

from __future__ import annotations

import time

from config import settings
from services.cache_service import cache


class CircuitBreaker:
    def __init__(self, name: str) -> None:
        self._key = cache.key("jobs", "circuit", name)

    def _state(self) -> dict:
        return cache.get(self._key) or {"failures": 0, "open_until": 0.0}

    def _save(self, state: dict) -> None:
        # keep the row a little longer than the cooldown so it survives the window
        cache.set(self._key, state, ttl=settings.JOB_CIRCUIT_COOLDOWN_SECONDS * 2 + 60)

    def allow(self) -> bool:
        return self._state().get("open_until", 0.0) <= time.time()

    def record_failure(self) -> None:
        state = self._state()
        state["failures"] = int(state.get("failures", 0)) + 1
        if state["failures"] >= settings.JOB_CIRCUIT_FAIL_THRESHOLD:
            state["open_until"] = time.time() + settings.JOB_CIRCUIT_COOLDOWN_SECONDS
        self._save(state)

    def record_success(self) -> None:
        self._save({"failures": 0, "open_until": 0.0})

    def state_name(self) -> str:
        state = self._state()
        if state.get("open_until", 0.0) > time.time():
            return "open"
        if state.get("failures", 0) > 0:
            return "half_open"
        return "closed"
