"""Phase 17 — per-provider run metrics (cache-backed, no secrets)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, ProviderState
from services.cache_service import cache
from services.providers.ashby_provider import AshbyProvider
from services.providers.http import AccessDenied
from services.providers.metrics import (
    ProviderMetrics, _safe_label, error_rate, health_note,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    cache.clear()
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "acme")
    monkeypatch.setattr(settings, "ATS_ENRICH_DESCRIPTIONS", False)
    yield
    cache.clear()


def test_successful_run_records_offer_count_and_duration(fake_http, job_fixtures):
    fake_http.route("job-board/acme", (job_fixtures / "ashby.json").read_text(encoding="utf-8"))
    prov = AshbyProvider()
    prov.search(JobSearchContext(), limit=10)

    snap = prov.metrics.snapshot()
    assert snap["runs"] == 1 and snap["ok"] == 1 and snap["fail"] == 0
    assert snap["last_state"] == "available"
    assert snap["last_offer_count"] == 2
    assert snap["last_duration_ms"] is not None and snap["last_duration_ms"] >= 0
    assert snap["last_ok_at"] is not None
    assert snap["last_error"] is None
    assert error_rate(snap) == 0.0


def test_failed_run_records_sanitised_error(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "a,b")
    fake_http.route("api.ashbyhq.com", AccessDenied("HTTP 403"))
    prov = AshbyProvider()
    r = prov.search(JobSearchContext(), limit=10)

    assert r.state is ProviderState.blocked
    snap = prov.metrics.snapshot()
    assert snap["runs"] == 1 and snap["fail"] == 1 and snap["ok"] == 0
    assert snap["last_state"] == "blocked"
    # label carries the last HTTP reason for the operator, sanitised
    assert snap["last_error"] and "403" in snap["last_error"]
    assert "denied access" in snap["last_error"]
    assert error_rate(snap) == 1.0


def test_disabled_run_is_not_counted_as_failure(monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "")
    prov = AshbyProvider()
    prov.search(JobSearchContext(), limit=10)
    snap = prov.metrics.snapshot()
    assert snap["runs"] == 1 and snap["fail"] == 0 and snap["ok"] == 0
    assert snap["last_state"] == "disabled"


def test_counters_accumulate_across_runs(fake_http, job_fixtures):
    fx = (job_fixtures / "ashby.json").read_text(encoding="utf-8")
    prov = AshbyProvider()
    fake_http.route("job-board/acme", fx)
    prov.search(JobSearchContext(), limit=10)
    fake_http.routes.clear()
    fake_http.route("api.ashbyhq.com", AccessDenied("HTTP 429"))
    prov.search(JobSearchContext(), limit=10)
    snap = prov.metrics.snapshot()
    assert snap["runs"] == 2 and snap["ok"] == 1 and snap["fail"] == 1
    assert error_rate(snap) == 0.5


def test_safe_label_strips_urls_and_long_tokens():
    assert _safe_label("blocked (https://internal.example/secret?token=abc)") == "blocked ([…])"
    assert _safe_label("auth for AKIAIOSFODNN7EXAMPLEKEY1234567890") == "auth for […]"
    assert _safe_label("this is a long sentence " * 20).startswith("this is a long sentence")
    assert len(_safe_label("this is a long sentence " * 20)) <= 120
    assert _safe_label(None) is None
    assert _safe_label("") is None


def test_metrics_surface_in_registry_all_info(db, swap_providers, fake_http, job_fixtures):
    """Phase 6 — `GET /api/jobs/sources` is gone; provider run-metrics are read
    from `registry.all_info()` (used by `GET /api/admin/providers`)."""
    from _jobs_helpers import FakeJobProvider, make_offer
    from schemas.jobs import JobSearchContext, JobSearchRequest
    from services.jobs.search_service import JobSearchService
    from services.providers.registry import registry

    swap_providers([FakeJobProvider("arbeitnow", [make_offer()])])
    JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="x")))

    rows = registry.all_info()
    row = next(r for r in rows if r.name == "arbeitnow")
    assert row.runs >= 1
    assert row.last_run_at is not None
    assert row.last_offer_count == 1
    assert hasattr(row, "error_rate")
    assert all(hasattr(r, "last_error") for r in rows)


# ---------------------------------------------------------------------------
# Phase 18 — broken-provider detection (health_note)
# ---------------------------------------------------------------------------

def test_no_health_note_for_a_fresh_or_legit_empty_provider(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_STALE_MIN_OFFERS", 5)
    monkeypatch.setattr(settings, "PROVIDER_STALE_EMPTY_RUNS", 6)
    m = ProviderMetrics("fresh")
    assert health_note(m.snapshot(), "closed") is None
    # a provider that never returned much, now empty -> NOT flagged (could be niche)
    for _ in range(10):
        m.record(ProviderState.available, offers=0, duration_ms=5, error_kind=None)
    assert health_note(m.snapshot(), "closed") is None


def test_health_note_flags_proven_then_empty(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_STALE_MIN_OFFERS", 5)
    monkeypatch.setattr(settings, "PROVIDER_STALE_EMPTY_RUNS", 3)
    m = ProviderMetrics("proven")
    m.record(ProviderState.available, offers=42, duration_ms=5, error_kind=None)  # proved it works
    assert health_note(m.snapshot(), "closed") is None
    for _ in range(3):
        m.record(ProviderState.available, offers=0, duration_ms=5, error_kind=None)
    note = health_note(m.snapshot(), "closed")
    assert note and "0 results" in note and "42" in note
    # one good run clears it
    m.record(ProviderState.available, offers=7, duration_ms=5, error_kind=None)
    assert health_note(m.snapshot(), "closed") is None


def test_health_note_reports_open_circuit(monkeypatch):
    m = ProviderMetrics("down")
    for _ in range(4):
        m.record(ProviderState.temporarily_unavailable, offers=0, duration_ms=1,
                 error_kind="http_error")
    note = health_note(m.snapshot(), "open")
    assert note and "circuit open" in note and "4 consecutive failures" in note


def test_health_note_surfaces_in_registry_all_info(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_STALE_MIN_OFFERS", 3)
    monkeypatch.setattr(settings, "PROVIDER_STALE_EMPTY_RUNS", 2)
    m = ProviderMetrics("linkedin")
    m.record(ProviderState.available, offers=20, duration_ms=5, error_kind=None)
    m.record(ProviderState.available, offers=0, duration_ms=5, error_kind=None)
    m.record(ProviderState.available, offers=0, duration_ms=5, error_kind=None)
    from services.providers.registry import registry
    row = next(r for r in registry.all_info() if r.name == "linkedin")
    assert row.health_note and "upstream" in row.health_note


def test_metrics_never_break_a_search(monkeypatch, fake_http, job_fixtures):
    """A metrics-cache failure must be swallowed, not propagate into the search."""
    real_set = cache.set

    def _selective_boom(key, *a, **k):
        if "metrics" in key:
            raise RuntimeError("metrics cache down")
        return real_set(key, *a, **k)

    monkeypatch.setattr("services.providers.metrics.cache.set", _selective_boom)
    fake_http.route("job-board/acme", (job_fixtures / "ashby.json").read_text(encoding="utf-8"))
    r = AshbyProvider().search(JobSearchContext(), limit=10)
    assert r.state is ProviderState.available and r.offers
