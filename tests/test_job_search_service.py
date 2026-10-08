"""JobSearchService orchestration — isolation, dedup, ranking, cache, pagination."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from _jobs_helpers import FakeJobProvider, make_offer
from models import JobOffer
from schemas.jobs import (
    JobSearchContext, JobSearchRequest, JobType, ProviderState, ProviderStatus,
)
from services.jobs.search_service import JobSearchService


def FakeProvider(name, offers=None, *, behaviour="ok", priority=0):
    return FakeJobProvider(name, offers, priority=priority, behaviour=behaviour)


def _mk_offer(source, sid, title="Data Scientist", company="Globex", *, city="Paris",
              job_type="job", url=None, skills=None):
    return make_offer(source, sid, title=title, company=company, city=city,
                      job_type=job_type, url=url or f"https://{source}.example/{sid}",
                      skills=skills or [])


def _search(db, **ctx_kw):
    req = JobSearchRequest(context=JobSearchContext(query="data scientist", **ctx_kw))
    return JobSearchService(db).search(req)


# ---------------------------------------------------------------------------

def test_one_provider_down_does_not_break_search(db, swap_providers):
    ok = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])
    down = FakeProvider("linkedin", behaviour="raise")
    slow = FakeProvider("indeed", behaviour="timeout")
    ok2 = FakeProvider("remotive", [_mk_offer("remotive", "9", title="ML Engineer")])
    swap_providers([ok, down, slow, ok2])

    resp = _search(db)
    assert resp.status == "success"
    assert {r.source for r in resp.results} == {"arbeitnow", "remotive"}
    assert resp.sources["linkedin"] == ProviderStatus.unavailable
    assert resp.sources["indeed"] == ProviderStatus.unavailable
    assert resp.sources["arbeitnow"] == ProviderStatus.success


def test_results_deduplicated_across_providers(db, swap_providers):
    a = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1", skills=["Python"])], priority=10)
    b = FakeProvider("linkedin", [_mk_offer("linkedin", "2", skills=["SQL"])], priority=100)
    swap_providers([a, b])
    resp = _search(db)
    assert len(resp.results) == 1
    merged = resp.results[0]
    assert merged.source == "linkedin"
    assert set(merged.skills) == {"Python", "SQL"}
    assert merged.also_seen_on


def test_source_filter_limits_providers(db, swap_providers):
    a = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])
    b = FakeProvider("linkedin", [_mk_offer("linkedin", "2")])
    swap_providers([a, b])
    req = JobSearchRequest(context=JobSearchContext(query="data"), sources=["arbeitnow"])
    resp = JobSearchService(db).search(req)
    assert b.calls == 0
    assert {r.source for r in resp.results} == {"arbeitnow"}


def test_internship_filter(db, swap_providers):
    a = FakeProvider("arbeitnow", [
        _mk_offer("arbeitnow", "1", job_type="job"),
        _mk_offer("arbeitnow", "2", title="ML Internship", job_type="internship"),
    ])
    swap_providers([a])
    resp = _search(db, job_type=JobType.internship)
    assert [r.title for r in resp.results] == ["ML Internship"]


def test_ranking_orders_query_match_first(db, swap_providers):
    a = FakeProvider("arbeitnow", [
        _mk_offer("arbeitnow", "1", title="Frontend Developer"),
        _mk_offer("arbeitnow", "2", title="Senior Data Scientist"),
    ])
    swap_providers([a])
    resp = _search(db)
    assert resp.results[0].title == "Senior Data Scientist"
    assert resp.results[0].match_score >= resp.results[1].match_score


def test_pagination(db, swap_providers):
    offers = [
        _mk_offer("arbeitnow", str(i), title="Data Scientist", company=f"Company {i}")
        for i in range(5)
    ]
    swap_providers([FakeProvider("arbeitnow", offers)])
    req = JobSearchRequest(context=JobSearchContext(query="data scientist"), page=2, page_size=2)
    resp = JobSearchService(db).search(req)
    assert resp.page == 2 and resp.page_size == 2
    assert len(resp.results) == 2
    assert resp.total == 5


def test_cache_hit_skips_providers(db, swap_providers):
    p = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])
    swap_providers([p])
    first = _search(db)
    assert first.from_cache is False and p.calls == 1

    second = _search(db)
    assert second.from_cache is True
    assert p.calls == 1                       # served from cache — no new provider call
    assert [r.id for r in second.results] == [r.id for r in first.results]


def test_cache_hit_still_drops_a_now_expired_offer(db, swap_providers, monkeypatch):
    """A job that expires DURING the cache window must not resurface as active."""
    from datetime import timedelta
    import services.jobs.search_service as svc

    offer = _mk_offer("arbeitnow", "1")
    offer.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    swap_providers([FakeProvider("arbeitnow", [offer])])

    first = _search(db)
    assert len(first.results) == 1

    # jump past the offer's expiry but stay inside the 15-min cache TTL
    monkeypatch.setattr(svc, "_now",
                        lambda: datetime.now(timezone.utc) + timedelta(minutes=10))
    second = _search(db)
    assert second.from_cache is True
    assert second.results == []


def test_expired_cache_calls_providers_again(db, swap_providers, monkeypatch):
    from services.cache_service import cache
    p = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])
    swap_providers([p])
    _search(db)
    assert p.calls == 1
    cache.clear()                              # simulate TTL expiry
    _search(db)
    assert p.calls == 2


def test_offers_persisted_to_db(db, swap_providers):
    swap_providers([FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])])
    resp = _search(db)
    row = db.get(JobOffer, resp.results[0].id)
    assert row is not None
    assert row.scraped_at is not None and row.is_active is True


def test_provider_states_carry_precise_reason(db, swap_providers):
    ok = FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")])
    swap_providers([
        ok,
        FakeJobProvider("linkedin", state="auth_required"),
        FakeJobProvider("indeed", state="blocked"),
        FakeProvider("remotive", behaviour="raise"),
        FakeProvider("weworkremotely", behaviour="timeout"),
    ])
    resp = _search(db)
    assert resp.provider_states["arbeitnow"] == ProviderState.available
    assert resp.provider_states["linkedin"] == ProviderState.auth_required
    assert resp.provider_states["indeed"] == ProviderState.blocked
    assert resp.provider_states["remotive"] == ProviderState.error
    assert resp.provider_states["weworkremotely"] == ProviderState.temporarily_unavailable
    # coarse map still collapses every non-ok reason to `unavailable`
    assert resp.sources["linkedin"] == ProviderStatus.unavailable
    assert resp.sources["indeed"] == ProviderStatus.unavailable
    assert {r.source for r in resp.results} == {"arbeitnow"}


def test_provider_states_survive_cache_roundtrip(db, swap_providers):
    swap_providers([
        FakeProvider("arbeitnow", [_mk_offer("arbeitnow", "1")]),
        FakeJobProvider("linkedin", state="auth_required"),
    ])
    first = _search(db)
    assert first.from_cache is False
    second = _search(db)
    assert second.from_cache is True
    assert second.provider_states["linkedin"] == ProviderState.auth_required
    assert second.provider_states["arbeitnow"] == ProviderState.available


def test_zero_results_stays_available_not_unavailable(db, swap_providers):
    swap_providers([FakeProvider("arbeitnow", [])])
    resp = _search(db)
    assert resp.results == []
    assert resp.provider_states["arbeitnow"] == ProviderState.available
    assert resp.sources["arbeitnow"] == ProviderStatus.success


def test_all_providers_unavailable_still_returns_200(db, swap_providers):
    swap_providers([FakeProvider("linkedin", behaviour="raise"),
                    FakeProvider("indeed", behaviour="raise")])
    resp = _search(db)
    assert resp.status == "success"
    assert resp.results == []
    assert all(s == ProviderStatus.unavailable for s in resp.sources.values())


