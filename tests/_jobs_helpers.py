"""Shared helpers for the job-search test suite (fake providers + offer builders)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from schemas.jobs import NormalizedOffer, ProviderState, ProviderStatus
from services.providers.base import ApplicationMethod, ProviderResult, RevalidationResult
from services.providers.circuit import CircuitBreaker

# map the legacy `status=` kwarg to a fine-grained state
_STATUS_TO_STATE = {
    "success": ProviderState.available,
    "partial": ProviderState.degraded,
    "unavailable": ProviderState.temporarily_unavailable,
    "disabled": ProviderState.disabled,
}


def make_offer(source="arbeitnow", sid="1", **kw) -> NormalizedOffer:
    data = dict(
        source=source, source_job_id=sid,
        source_url=kw.pop("url", f"https://{source}.example/{sid}"),
        title="Data Scientist", company="Globex", city="Paris",
        posted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    data.update(kw)
    return NormalizedOffer(**data)


class FakeJobProvider:
    """In-memory provider mirroring the ``JobProvider`` surface the service uses."""

    def __init__(self, name, offers=None, *, status="success", priority=0,
                 application=None, revalidation="unknown", behaviour="ok", state=None,
                 delay=0.0):
        self.name = name
        self.delay = delay
        self.application_priority = priority
        self.allowed_hosts = ("example.com",)
        self.breaker = CircuitBreaker(f"jobs:{name}")
        from services.providers.metrics import ProviderMetrics
        self.metrics = ProviderMetrics(name)
        self._offers = offers or []
        self._status = status
        self._state = (ProviderState(state) if isinstance(state, str) else state) \
            or _STATUS_TO_STATE.get(status, ProviderState.available)
        self._application = application
        self._revalidation = revalidation
        self._behaviour = behaviour
        self._last_status = None
        self._last_state = None
        self.calls = 0

    @property
    def enabled(self):
        return self._status != "disabled" and self._state is not ProviderState.disabled

    @property
    def last_status(self):
        return self._last_status

    @property
    def last_state(self):
        return self._last_state

    def _finish(self, state, *, offers=None, strategy=None):
        self._last_state = state
        self._last_status = state.to_status()
        if state.to_status() is not ProviderStatus.success and state is not ProviderState.disabled:
            self.breaker.record_failure()
        self.metrics.record(state, offers=len(offers or []), duration_ms=1, error_kind="test")
        return ProviderResult(self.name, state.to_status(), offers=offers or [],
                              strategy_used=strategy, state=state, error_kind="test")

    def search(self, ctx, *, limit):
        self.calls += 1
        if self.delay:
            import time
            time.sleep(self.delay)
        if self._behaviour == "raise":
            raise RuntimeError("boom")
        if self._behaviour == "timeout":
            import time
            time.sleep(3)
        if self._state.to_status() is not ProviderStatus.success:
            return self._finish(self._state)
        return self._finish(ProviderState.available,
                            offers=list(self._offers)[:limit], strategy="fake")

    def revalidate(self, offer):
        return RevalidationResult(self._revalidation)

    def application_method(self, offer):
        if self._application is not None:
            return self._application
        return ApplicationMethod("external_url", offer.source_url)
