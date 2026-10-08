"""The fine-grained provider state machine (Part A)."""

from __future__ import annotations

import pytest

from _jobs_helpers import FakeJobProvider, make_offer
from schemas.jobs import (
    JobSearchContext, JobSearchRequest, ProviderState, ProviderStatus,
)
from services.providers.base import (
    JobProvider, ProviderAuthRequired, ProviderBlocked, ProviderTemporarilyUnavailable,
)
from services.providers.http import AccessDenied, HttpError
from services.jobs.search_service import JobSearchService


# ---------------------------------------------------------------------------
# ProviderState -> ProviderStatus coarsening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state, coarse", [
    (ProviderState.available, ProviderStatus.success),
    (ProviderState.degraded, ProviderStatus.partial),
    (ProviderState.disabled, ProviderStatus.disabled),
    (ProviderState.auth_required, ProviderStatus.unavailable),
    (ProviderState.temporarily_unavailable, ProviderStatus.unavailable),
    (ProviderState.blocked, ProviderStatus.unavailable),
    (ProviderState.error, ProviderStatus.unavailable),
])
def test_to_status_mapping(state, coarse):
    assert state.to_status() is coarse


# ---------------------------------------------------------------------------
# base.search() outcome -> state
# ---------------------------------------------------------------------------

class _P(JobProvider):
    name = "state_probe"
    allowed_hosts = ("example.com",)

    def __init__(self, outcome):
        super().__init__()
        self._outcome = outcome

    def _search(self, ctx, *, limit, deadline):
        o = self._outcome
        if o == "ok":
            return [make_offer("state_probe", "1")], "probe"
        if o == "ok_zero":
            return [], "probe"                       # strategy ran, no offers
        if o == "degraded":
            return [], None                          # no strategy, no offers
        if o == "authwall":
            raise ProviderAuthRequired("authwall")
        if o == "hardblock":
            raise ProviderBlocked("999")
        if o == "temp":
            raise ProviderTemporarilyUnavailable("timeout")
        if o == "403":
            raise AccessDenied("HTTP 403 from example.com")
        if o == "429":
            raise AccessDenied("HTTP 429 from example.com")
        if o == "http":
            raise HttpError("boom")
        if o == "crash":
            raise RuntimeError("kaboom")
        raise AssertionError(o)


@pytest.mark.parametrize("outcome, state", [
    ("ok", ProviderState.available),
    ("ok_zero", ProviderState.available),            # 0 results != unavailable
    ("degraded", ProviderState.degraded),
    ("authwall", ProviderState.auth_required),
    ("hardblock", ProviderState.blocked),
    ("403", ProviderState.blocked),
    ("429", ProviderState.temporarily_unavailable),
    ("temp", ProviderState.temporarily_unavailable),
    ("http", ProviderState.temporarily_unavailable),
    ("crash", ProviderState.error),
])
def test_search_outcome_to_state(outcome, state):
    p = _P(outcome)
    result = p.search(JobSearchContext(query="x"), limit=5)
    assert result.state is state
    assert result.status is state.to_status()
    assert p.last_state is state


def test_disabled_and_circuit_open():
    class _Off(_P):
        @property
        def enabled(self):
            return False

    assert _Off("ok").search(JobSearchContext(), limit=5).state is ProviderState.disabled

    p = _P("ok")
    for _ in range(5):
        p.breaker.record_failure()
    assert p.search(JobSearchContext(), limit=5).state is ProviderState.temporarily_unavailable


# ---------------------------------------------------------------------------
# Surfaced in the search response + registry info
# ---------------------------------------------------------------------------

def test_provider_states_in_search_response(db, swap_providers):
    swap_providers([
        FakeJobProvider("arbeitnow", [make_offer("arbeitnow", "1")]),
        FakeJobProvider("linkedin", state="auth_required"),
        FakeJobProvider("indeed", state="blocked"),
    ])
    resp = JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="data")))
    assert resp.provider_states["linkedin"] == ProviderState.auth_required
    assert resp.provider_states["indeed"] == ProviderState.blocked
    assert resp.provider_states["arbeitnow"] == ProviderState.available
    # coarse `sources` stays backward-compatible
    assert resp.sources["linkedin"] == ProviderStatus.unavailable
    assert resp.sources["arbeitnow"] == ProviderStatus.success
    # the auth_required source did not break the others
    assert [r.source for r in resp.results] == ["arbeitnow"]


def test_provider_states_survive_cache_roundtrip(db, swap_providers):
    swap_providers([FakeJobProvider("linkedin", state="blocked"),
                    FakeJobProvider("arbeitnow", [make_offer("arbeitnow", "1")])])
    req = JobSearchRequest(context=JobSearchContext(query="data"))
    JobSearchService(db).search(req)
    cached = JobSearchService(db).search(req)
    assert cached.from_cache is True
    assert cached.provider_states["linkedin"] == ProviderState.blocked


def test_registry_all_info_exposes_state(swap_providers):
    """`GET /api/jobs/sources` was removed in Phase 6; `registry.all_info()`
    (behind `GET /api/admin/providers`) still carries the fine-grained state."""
    swap_providers([FakeJobProvider("linkedin", state="auth_required")])
    from services.providers.registry import registry
    entry = next(s for s in registry.all_info() if s.name == "linkedin")
    assert hasattr(entry, "state")
