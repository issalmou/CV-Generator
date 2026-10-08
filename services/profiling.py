"""
Lightweight, opt-in latency + LLM-call profiling (v2.9 Phase 1).

Purpose: measure *precisely* where time goes in every pipeline
(extraction / CV generation / cover letter / chat) before any optimisation
is attempted — "measure, then change", never guess.

Privacy: this module records **durations, token counts, model / provider
names and boolean flags only**. It NEVER touches a prompt, an LLM response,
a CV, a job description or a conversation message. Nothing here is ever
logged in production beyond one structured summary line of counters.

Overhead: a `span(...)` is two ``perf_counter()`` reads plus a lock-guarded
list append (~1 µs). When ``settings.PROFILING_ENABLED`` is false every
public call is a no-op. Negligible next to a multi-second LLM round-trip.

Design:
- one :class:`RequestProfile` per top-level pipeline run, held in a
  ``ContextVar`` so nested ``span(...)`` blocks and every ``call_gemini``
  attach to it automatically;
- :func:`bind` re-applies the current profile inside a worker thread, so the
  extraction pipeline's 4 parallel section calls (and the CV pipeline's own
  executor hop) still land in the right profile;
- on exit, the profile is pushed to a bounded ring buffer that
  ``GET /api/admin/profiling/recent`` reads.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from config import settings

logger = logging.getLogger("profiling")

_current: contextvars.ContextVar["RequestProfile | None"] = contextvars.ContextVar(
    "request_profile", default=None
)

# module-level ring buffer of finished profiles (summaries only)
_ring: list[dict] = []
_ring_lock = threading.Lock()


def enabled() -> bool:
    return bool(getattr(settings, "PROFILING_ENABLED", True))


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

@dataclass
class LLMCall:
    request_type: str
    provider: str
    model: str | None
    duration_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    max_tokens: int | None = None
    attempts: int = 1              # how many models in the chain were tried
    fallback_used: bool = False    # True when the answer came from a non-primary model
    cache_hit: bool = False        # served from a cache, no network call
    ok: bool = True
    error_kind: str | None = None  # exception class name, never a message with content


@dataclass
class Span:
    name: str
    duration_ms: float


@dataclass
class RequestProfile:
    kind: str                      # "extraction" | "cv_generation" | "letter" | "chat"
    request_id: str
    started: float = field(default_factory=time.perf_counter)
    spans: list[Span] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_span(self, name: str, ms: float) -> None:
        with self._lock:
            self.spans.append(Span(name, round(ms, 1)))

    def add_llm(self, call: LLMCall) -> None:
        with self._lock:
            self.llm_calls.append(call)

    def summary(self) -> dict:
        total_ms = (time.perf_counter() - self.started) * 1000.0
        with self._lock:
            spans = list(self.spans)
            calls = list(self.llm_calls)
        llm_dur = sum(c.duration_ms for c in calls)
        return {
            "kind": self.kind,
            "request_id": self.request_id,
            "total_ms": round(total_ms, 1),
            "llm_ms": round(llm_dur, 1),
            "local_ms": round(max(0.0, total_ms - llm_dur), 1),
            "spans": [{"name": s.name, "ms": s.duration_ms} for s in spans],
            "llm": {
                "count": len([c for c in calls if not c.cache_hit]),
                "cache_hits": sum(1 for c in calls if c.cache_hit),
                "fallbacks": sum(1 for c in calls if c.fallback_used),
                "errors": sum(1 for c in calls if not c.ok),
                "prompt_tokens": sum(c.prompt_tokens or 0 for c in calls),
                "completion_tokens": sum(c.completion_tokens or 0 for c in calls),
                "by_call": [
                    {
                        "request_type": c.request_type,
                        "provider": c.provider,
                        "model": c.model,
                        "ms": round(c.duration_ms, 1),
                        "in_tokens": c.prompt_tokens,
                        "out_tokens": c.completion_tokens,
                        "max_tokens": c.max_tokens,
                        "attempts": c.attempts,
                        "fallback": c.fallback_used,
                        "cache_hit": c.cache_hit,
                        "ok": c.ok,
                        "error": c.error_kind,
                    }
                    for c in calls
                ],
            },
        }


# ---------------------------------------------------------------------------
# context management
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def profile_request(kind: str, request_id: str | None = None):
    """Open a profile for one top-level pipeline run. Yields the
    :class:`RequestProfile` (or ``None`` when profiling is off)."""
    if not enabled():
        yield None
        return
    rp = RequestProfile(kind=kind, request_id=(request_id or uuid.uuid4().hex[:12]))
    token = _current.set(rp)
    try:
        yield rp
    finally:
        _current.reset(token)
        try:
            s = rp.summary()
            _push(s)
            logger.info("[PROFILE] %s", _format_line(s))
        except Exception:  # noqa: BLE001 — profiling must never break a request
            logger.debug("[PROFILE] summary failed", exc_info=True)


@contextlib.contextmanager
def span(name: str):
    """Time a block and attach it to the current request profile (if any)."""
    if not enabled():
        yield
        return
    rp = _current.get()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        if rp is not None:
            try:
                rp.add_span(name, (time.perf_counter() - t0) * 1000.0)
            except Exception:  # noqa: BLE001
                pass


def add_span_ms(name: str, ms: float) -> None:
    """Attach an already-measured span to the current profile — for blocks
    that cannot be wrapped in the ``span(...)`` context manager without a
    risky re-indentation."""
    if not enabled():
        return
    rp = _current.get()
    if rp is not None:
        try:
            rp.add_span(name, ms)
        except Exception:  # noqa: BLE001
            pass


def record_llm_call(
    *,
    request_type: str,
    provider: str,
    model: str | None,
    duration_ms: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    max_tokens: int | None = None,
    attempts: int = 1,
    fallback_used: bool = False,
    cache_hit: bool = False,
    ok: bool = True,
    error_kind: str | None = None,
) -> None:
    """Record one LLM call (or cache hit) on the current request profile.
    Called from ``services.gemini_client.call_gemini`` — never anywhere that
    would pass it prompt / response text."""
    if not enabled():
        return
    rp = _current.get()
    if rp is None:
        return
    try:
        rp.add_llm(LLMCall(
            request_type=request_type, provider=provider, model=model,
            duration_ms=duration_ms, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, max_tokens=max_tokens,
            attempts=attempts, fallback_used=fallback_used, cache_hit=cache_hit,
            ok=ok, error_kind=error_kind,
        ))
    except Exception:  # noqa: BLE001
        pass


def record_semantic_cache_hit(request_type: str, lookup_ms: float) -> None:
    """A hit on the ``generation_service`` semantic cache — the LLM call was
    skipped entirely. Shows up in ``by_call`` as ``provider="<semantic-cache>"``."""
    record_llm_call(
        request_type=request_type, provider="<semantic-cache>", model=None,
        duration_ms=lookup_ms, cache_hit=True, ok=True,
    )


def current() -> "RequestProfile | None":
    return _current.get()


def bind(fn):
    """Return a wrapper that runs ``fn`` with the CURRENT request profile
    re-applied — for ``ThreadPoolExecutor`` workers / ``run_in_executor`` hops,
    so the ``call_gemini`` calls they make attach to the right profile.
    A plain pass-through when profiling is off or there is no active profile."""
    if not enabled():
        return fn
    rp = _current.get()
    if rp is None:
        return fn

    def _run(*args, **kwargs):
        token = _current.set(rp)
        try:
            return fn(*args, **kwargs)
        finally:
            _current.reset(token)

    return _run


# ---------------------------------------------------------------------------
# ring buffer + read API (for GET /api/admin/profiling/recent)
# ---------------------------------------------------------------------------

def _push(summary: dict) -> None:
    cap = int(getattr(settings, "PROFILING_RING_SIZE", 200) or 200)
    with _ring_lock:
        _ring.append(summary)
        if len(_ring) > cap:
            del _ring[: len(_ring) - cap]


def recent(limit: int = 50) -> dict:
    """Last-N request profiles + per-(kind, request_type) aggregates."""
    with _ring_lock:
        rows = list(_ring)[-max(1, limit):]
        all_rows = list(_ring)
    return {
        "enabled": enabled(),
        "count": len(rows),
        "profiles": list(reversed(rows)),
        "aggregates": _aggregate(all_rows),
    }


def reset() -> None:
    with _ring_lock:
        _ring.clear()


def _aggregate(rows: list[dict]) -> dict:
    by_kind: dict[str, list[float]] = {}
    by_rt: dict[str, dict] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["total_ms"])
        for c in r.get("llm", {}).get("by_call", []):
            b = by_rt.setdefault(c["request_type"], {
                "n": 0, "ms": [], "in": 0, "out": 0, "fallbacks": 0, "cache_hits": 0,
                "errors": 0, "models": {},
            })
            b["n"] += 1
            b["ms"].append(c["ms"])
            b["in"] += c.get("in_tokens") or 0
            b["out"] += c.get("out_tokens") or 0
            b["fallbacks"] += 1 if c.get("fallback") else 0
            b["cache_hits"] += 1 if c.get("cache_hit") else 0
            b["errors"] += 0 if c.get("ok", True) else 1
            key = f"{c.get('provider')} {c.get('model')}"
            b["models"][key] = b["models"].get(key, 0) + 1
    return {
        "by_kind": {
            k: {"n": len(v), "p50_ms": _pct(v, 50), "p95_ms": _pct(v, 95),
                "avg_ms": round(sum(v) / len(v), 1) if v else 0.0}
            for k, v in by_kind.items()
        },
        "by_request_type": {
            rt: {
                "n": b["n"],
                "p50_ms": _pct(b["ms"], 50), "p95_ms": _pct(b["ms"], 95),
                "avg_ms": round(sum(b["ms"]) / len(b["ms"]), 1) if b["ms"] else 0.0,
                "avg_in_tokens": round(b["in"] / b["n"], 0) if b["n"] else 0,
                "avg_out_tokens": round(b["out"] / b["n"], 0) if b["n"] else 0,
                "fallback_rate": round(b["fallbacks"] / b["n"], 3) if b["n"] else 0.0,
                "cache_hit_rate": round(b["cache_hits"] / b["n"], 3) if b["n"] else 0.0,
                "error_rate": round(b["errors"] / b["n"], 3) if b["n"] else 0.0,
                "models": b["models"],
            }
            for rt, b in by_rt.items()
        },
    }


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[k], 1)


def _format_line(s: dict) -> str:
    spans = " ".join(f"{sp['name']}={sp['ms']:.0f}" for sp in s.get("spans", []))
    llm = s.get("llm", {})
    calls = " ".join(
        f"[{c['request_type']} {c['provider']} {c['model']} {c['ms']:.0f}ms "
        f"in={c['in_tokens']} out={c['out_tokens']} att={c['attempts']}"
        f"{' fb' if c['fallback'] else ''}{' cache' if c['cache_hit'] else ''}"
        f"{'' if c['ok'] else ' ERR:' + str(c['error'])}]"
        for c in llm.get("by_call", [])
    )
    return (
        f"kind={s['kind']} id={s['request_id']} total={s['total_ms']:.0f}ms "
        f"llm={s['llm_ms']:.0f}ms local={s['local_ms']:.0f}ms | "
        f"spans: {spans} | "
        f"llm: n={llm.get('count', 0)} cache_hits={llm.get('cache_hits', 0)} "
        f"fallbacks={llm.get('fallbacks', 0)} errors={llm.get('errors', 0)} "
        f"in={llm.get('prompt_tokens', 0)}tok out={llm.get('completion_tokens', 0)}tok | "
        f"calls: {calls}"
    )
