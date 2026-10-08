"""
Lightweight per-provider run metrics, shared across workers via the app cache
(same mechanism as the circuit breaker — no new state store, TTL'd).

Recorded by ``JobProvider.search()`` after every run:
``runs`` / ``ok`` / ``fail`` counters, ``last_run_at`` / ``last_ok_at``,
``last_state`` / ``last_offer_count`` / ``last_duration_ms`` and a short,
**sanitised** ``last_error`` label. Surfaced read-only via
``registry.all_info()`` → ``GET /api/admin/providers``.

Nothing sensitive is stored: the error label is a provider-controlled slug
(``blocked:...`` / ``http_error`` / ``exception`` …) run through
:func:`_safe_label`, which drops anything URL- or token-shaped and truncates.
"""

from __future__ import annotations

import logging
import re
import time

from config import settings
from schemas.jobs import ProviderState
from services.cache_service import cache

logger = logging.getLogger(__name__)

_TTL = 7 * 24 * 3600
_URLISH = re.compile(r"(https?://[^\s)>\]]+|[A-Za-z0-9/+_\-]{24,})")
_MAX_LABEL = 120

_OK_STATES = {ProviderState.available, ProviderState.degraded}


def _safe_label(value: str | None) -> str | None:
    if not value:
        return None
    text = _URLISH.sub("[…]", str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:_MAX_LABEL] or None


_DURATION_WINDOW = 30   # keep the last N run durations for avg / p95


def _blank() -> dict:
    return {
        "runs": 0, "ok": 0, "fail": 0,
        "ok_nonempty": 0, "consecutive_empty": 0, "consecutive_fail": 0,
        "max_offers": 0, "total_offers": 0,
        "last_run_at": None, "last_ok_at": None,
        "last_state": None, "last_offer_count": None,
        "last_duration_ms": None, "last_error": None,
        "stale_since": None,
        "recent_durations_ms": [],
    }


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def duration_stats(snap: dict) -> dict:
    vals = [int(v) for v in snap.get("recent_durations_ms", []) if isinstance(v, (int, float))]
    if not vals:
        return {"avg_duration_ms": None, "p95_duration_ms": None, "samples": 0}
    return {
        "avg_duration_ms": int(sum(vals) / len(vals)),
        "p95_duration_ms": _percentile(vals, 95),
        "samples": len(vals),
    }


def health_note(snap: dict, circuit_state: str) -> str | None:
    """A one-line diagnostic when a provider looks *structurally* broken —
    distinct from a search that legitimately found nothing.

    - the circuit is open  -> report the consecutive failure count
    - the provider has proven it can return results (``max_offers`` high) but
      the last N runs were all ``available`` with **zero** offers -> likely an
      upstream API / selector change even though nothing "errors"."""
    if circuit_state == "open":
        n = int(snap.get("consecutive_fail", 0) or 0)
        return f"circuit open — {n} consecutive failures ({snap.get('last_error') or 'unknown'})"
    proven = int(snap.get("max_offers", 0) or 0) >= settings.PROVIDER_STALE_MIN_OFFERS
    empty_streak = int(snap.get("consecutive_empty", 0) or 0)
    if proven and empty_streak >= settings.PROVIDER_STALE_EMPTY_RUNS:
        return (
            f"0 results in the last {empty_streak} runs despite a past best of "
            f"{snap['max_offers']} — check for an upstream API / markup change"
        )
    return None


class ProviderMetrics:
    def __init__(self, name: str) -> None:
        self._key = cache.key("jobs", "metrics", name)

    def snapshot(self) -> dict:
        data = cache.get(self._key)
        return {**_blank(), **data} if isinstance(data, dict) else _blank()

    def record(
        self,
        state: ProviderState,
        *,
        offers: int | None,
        duration_ms: int | None,
        error_kind: str | None,
    ) -> None:
        try:
            m = self.snapshot()
            now = time.time()
            n = int(offers or 0)
            m["runs"] = int(m.get("runs", 0)) + 1
            m["last_run_at"] = now
            m["last_state"] = state.value
            m["last_duration_ms"] = duration_ms
            if duration_ms is not None:
                window = list(m.get("recent_durations_ms", []))[-(_DURATION_WINDOW - 1):]
                window.append(int(duration_ms))
                m["recent_durations_ms"] = window
            if state in _OK_STATES:
                m["ok"] = int(m.get("ok", 0)) + 1
                m["last_ok_at"] = now
                m["last_offer_count"] = n
                m["last_error"] = None
                m["consecutive_fail"] = 0
                m["max_offers"] = max(int(m.get("max_offers", 0) or 0), n)
                m["total_offers"] = int(m.get("total_offers", 0) or 0) + n
                if n > 0:
                    m["ok_nonempty"] = int(m.get("ok_nonempty", 0)) + 1
                    m["consecutive_empty"] = 0
                    m["stale_since"] = None
                else:
                    prev = int(m.get("consecutive_empty", 0) or 0)
                    m["consecutive_empty"] = prev + 1
                    if m["consecutive_empty"] >= settings.PROVIDER_STALE_EMPTY_RUNS \
                            and int(m.get("max_offers", 0) or 0) >= settings.PROVIDER_STALE_MIN_OFFERS \
                            and not m.get("stale_since"):
                        m["stale_since"] = now
                        logger.warning(
                            "[jobs.health] provider %s: %d consecutive empty runs "
                            "(past best %d) — likely upstream change",
                            self._key.rsplit(":", 1)[-1], m["consecutive_empty"],
                            m.get("max_offers", 0),
                        )
            elif state is not ProviderState.disabled:
                m["fail"] = int(m.get("fail", 0)) + 1
                m["consecutive_fail"] = int(m.get("consecutive_fail", 0) or 0) + 1
                m["last_error"] = _safe_label(error_kind)
            cache.set(self._key, m, ttl=_TTL)
        except Exception:  # noqa: BLE001 — telemetry must never break a search
            pass


def error_rate(snap: dict) -> float | None:
    runs = int(snap.get("runs", 0) or 0)
    if runs == 0:
        return None
    return round(int(snap.get("fail", 0) or 0) / runs, 3)
