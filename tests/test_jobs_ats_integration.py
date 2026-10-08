"""End-to-end: the real ATS providers running through JobSearchService.

Exercises the full path — ProviderRegistry -> ThreadPoolExecutor fan-out ->
NormalizedOffer -> dedup -> freshness -> ranking -> cache -> paginate — with
the ATS HTTP mocked (``fake_http``) and no network.
"""

from __future__ import annotations

import pytest

from _jobs_helpers import FakeJobProvider
from config import settings
from schemas.jobs import (
    JobSearchContext, JobSearchRequest, ProviderState, ProviderStatus,
)
from services.providers.ashby_provider import AshbyProvider
from services.providers.greenhouse_provider import GreenhouseProvider
from services.providers.http import AccessDenied, HttpError
from services.providers.smartrecruiters_provider import SmartRecruitersProvider
from services.jobs.search_service import JobSearchService


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _ats_config(monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "acme")
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "acme")
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")


def _run(db, ctx=None, sources=None):
    req = JobSearchRequest(context=ctx if ctx is not None else JobSearchContext(), sources=sources)
    return JobSearchService(db).search(req)


def test_multiple_ats_providers_run_together(db, swap_providers, fake_http, job_fixtures):
    swap_providers([AshbyProvider(), GreenhouseProvider()])
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    fake_http.route("boards-api.greenhouse.io/v1/boards/acme/jobs", _fx(job_fixtures, "greenhouse.json"))

    resp = _run(db)
    assert resp.status == "success"
    sources = {r.source for r in resp.results}
    assert "ashby" in sources and "greenhouse" in sources
    assert resp.provider_states["ashby"] == ProviderState.available
    assert resp.provider_states["greenhouse"] == ProviderState.available


def test_one_ats_down_does_not_break_the_others(db, swap_providers, fake_http, job_fixtures):
    swap_providers([
        AshbyProvider(),
        GreenhouseProvider(),
        SmartRecruitersProvider(),
        FakeJobProvider("linkedin", state="blocked"),
    ])
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    fake_http.route("boards-api.greenhouse.io/v1/boards/acme/jobs", HttpError("greenhouse down"))
    fake_http.route("companies/AcmeCorp/postings", AccessDenied("HTTP 403"))

    resp = _run(db)
    assert resp.status == "success"
    assert {r.source for r in resp.results} == {"ashby"}
    assert resp.provider_states["ashby"] == ProviderState.available
    assert resp.provider_states["greenhouse"] == ProviderState.temporarily_unavailable
    assert resp.provider_states["smartrecruiters"] == ProviderState.blocked
    assert resp.provider_states["linkedin"] == ProviderState.blocked
    # coarse map still collapses every failure to "unavailable"
    assert resp.sources["greenhouse"] == ProviderStatus.unavailable
    assert resp.sources["smartrecruiters"] == ProviderStatus.unavailable


def test_ats_zero_results_is_available_not_unavailable(db, swap_providers, fake_http):
    swap_providers([AshbyProvider()])
    fake_http.route("job-board/acme", '{"jobs": []}')
    resp = _run(db)
    assert resp.results == []
    assert resp.provider_states["ashby"] == ProviderState.available
    assert resp.sources["ashby"] == ProviderStatus.success


def test_ats_circuit_breaker_opens_after_repeated_failure(db, swap_providers, fake_http):
    prov = AshbyProvider()
    swap_providers([prov])
    fake_http.route("api.ashbyhq.com", HttpError("down"))

    # distinct queries dodge the *search* cache without clearing the
    # cache-backed circuit breaker state
    for i in range(5):
        _run(db, ctx=JobSearchContext(query=f"role number {i}"))

    assert prov.breaker.state_name() == "open"
    resp = _run(db, ctx=JobSearchContext(query="one more role"))
    # circuit open -> provider skipped without a request, still reported
    assert resp.provider_states["ashby"] == ProviderState.temporarily_unavailable


def test_ats_dedup_across_providers(db, swap_providers, fake_http, job_fixtures):
    # same company+title+city from two ATS -> merged into one row with also_seen_on
    ashby_json = (
        '{"jobs":[{"id":"a1","title":"Platform Engineer","isListed":true,'
        '"jobUrl":"https://jobs.ashbyhq.com/acme/a1","location":"Berlin, Germany",'
        '"department":"Engineering","descriptionPlain":"Build platforms."}]}'
    )
    gh_json = (
        '{"jobs":[{"id":5551,"title":"Platform Engineer","absolute_url":'
        '"https://boards.greenhouse.io/acme/jobs/5551","location":{"name":"Berlin, Germany"},'
        '"content":"Build platforms.","updated_at":"2099-08-20T00:00:00Z"}]}'
    )
    swap_providers([AshbyProvider(), GreenhouseProvider()])
    fake_http.route("job-board/acme", ashby_json)
    fake_http.route("boards-api.greenhouse.io/v1/boards/acme/jobs", gh_json)

    resp = _run(db, ctx=JobSearchContext(query="platform"))
    platform = [r for r in resp.results if r.title == "Platform Engineer"]
    assert len(platform) == 1
    assert platform[0].also_seen_on            # the other source's URL is retained
