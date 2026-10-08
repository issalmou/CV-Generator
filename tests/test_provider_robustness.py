"""Phase 20 — every ATS provider survives a hostile / broken upstream:
timeouts, 4xx/5xx, junk JSON, empty bodies, null-riddled rows, wrong shapes,
oversized payloads. A provider never raises out of ``search()``; a bad
provider never sinks a multi-provider search."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, ProviderState, ProviderStatus
from services.providers.ashby_provider import AshbyProvider
from services.providers.greenhouse_provider import GreenhouseProvider
from services.providers.http import AccessDenied, HttpError, SsrfError
from services.providers.lever_provider import LeverProvider
from services.providers.oracle_hcm_provider import OracleHcmProvider
from services.providers.phenom_provider import PhenomProvider
from services.providers.rippling_provider import RipplingProvider
from services.providers.smartrecruiters_provider import SmartRecruitersProvider
from services.providers.talentbrew_provider import TalentBrewProvider
from services.providers.workday_provider import WorkdayProvider

# (provider factory, config attr, config value, a substring the fetch URL contains)
_ATS = [
    (AshbyProvider, "ASHBY_BOARDS", "acme", "ashbyhq.com"),
    (GreenhouseProvider, "GREENHOUSE_BOARDS", "acme", "greenhouse.io"),
    (LeverProvider, "LEVER_BOARDS", "acme", "lever.co"),
    (SmartRecruitersProvider, "SMARTRECRUITERS_BOARDS", "Acme", "smartrecruiters.com"),
    (RipplingProvider, "RIPPLING_BOARDS", "acme", "rippling.com"),
    (WorkdayProvider, "WORKDAY_TENANTS", "https://acme.wd1.myworkdayjobs.com/AcmeCareers", "myworkdayjobs.com"),
    (OracleHcmProvider, "ORACLE_HCM_SITES",
     "https://x.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions", "oraclecloud.com"),
    (TalentBrewProvider, "TALENTBREW_BOARDS", "https://acme.example/search-jobs/results?x=1", "search-jobs/results"),
]
_IDS = [p.__name__ for p, *_ in _ATS]


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "ATS_ENRICH_DESCRIPTIONS", False)
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)


def _phenom(monkeypatch):
    import json
    monkeypatch.setattr(settings, "PHENOM_BOARDS", json.dumps(
        [{"company": "Acme", "endpoint": "https://careers.acme.example/widgets", "payload": {}}]))
    return PhenomProvider(), "careers.acme.example/widgets"


@pytest.mark.parametrize("factory, attr, value, host", _ATS, ids=_IDS)
@pytest.mark.parametrize("bad", [
    HttpError("timeout"),
    AccessDenied("HTTP 500"),
    AccessDenied("HTTP 429"),
    AccessDenied("HTTP 403"),
    SsrfError("host resolves to non-public address"),
    "",                       # empty body
    "not json at all <<<",    # junk
    "null",                   # valid JSON, wrong type
    "[]",                     # valid JSON, wrong container
    '{"unexpected": {"deeply": {"nested": [1,2,3]}}}',
], ids=lambda b: type(b).__name__ if isinstance(b, Exception) else repr(b)[:18])
def test_ats_never_raises_on_hostile_upstream(fake_http, monkeypatch, factory, attr, value, host, bad):
    monkeypatch.setattr(settings, attr, value)
    fake_http.route(host, bad)
    result = factory().search(JobSearchContext(query="engineer"), limit=10)
    # never an exception; always a structured result
    assert result.status in ProviderStatus
    assert result.state in ProviderState
    assert result.offers == [] or all(o.title for o in result.offers)


def test_phenom_never_raises_on_hostile_upstream(fake_http, monkeypatch):
    for bad in (HttpError("x"), AccessDenied("HTTP 500"), "null", "[]", "junk"):
        fake_http.routes.clear()
        prov, host = _phenom(monkeypatch)
        fake_http.route(host, bad)
        r = prov.search(JobSearchContext(query="x"), limit=10)
        assert r.state in ProviderState


@pytest.mark.parametrize("factory, attr, value, host", _ATS, ids=_IDS)
def test_ats_survives_null_riddled_rows(fake_http, monkeypatch, factory, attr, value, host):
    monkeypatch.setattr(settings, attr, value)
    junk = (
        '{"jobs":[null,{},{"id":null,"title":null},{"title":123}],'
        '"content":[null,{"id":null}],"items":[{"requisitionList":[null,{}]}],'
        '"refineSearch":{"data":{"jobs":[null,{}]}},"results":"<a href=\\"/job/x\\"></a>",'
        '"jobPostings":[null,{"title":null,"externalPath":null}]}'
    )
    fake_http.route(host, junk)
    result = factory().search(JobSearchContext(), limit=10)
    assert result.state in (ProviderState.available, ProviderState.degraded)
    assert result.offers == []          # nothing valid -> nothing emitted, no crash


@pytest.mark.parametrize("factory, attr, value, host", _ATS, ids=_IDS)
def test_ats_oversized_response_is_capped(fake_http, monkeypatch, factory, attr, value, host):
    monkeypatch.setattr(settings, attr, value)
    # the real size cap raises HttpError once the stream passes JOB/ATS_HTTP_MAX_BYTES
    fake_http.route(host, HttpError("response exceeded 500 bytes"))
    result = factory().search(JobSearchContext(), limit=10)
    assert result.state is ProviderState.temporarily_unavailable
    assert result.offers == []


def test_multi_provider_search_survives_every_provider_failing(db, swap_providers, fake_http):
    from _jobs_helpers import FakeJobProvider, make_offer
    swap_providers([
        FakeJobProvider("arbeitnow", [make_offer()]),
        FakeJobProvider("linkedin", behaviour="raise"),
        FakeJobProvider("indeed", behaviour="timeout"),
        FakeJobProvider("remotive", state="blocked"),
        FakeJobProvider("jobicy", state="auth_required"),
    ])
    from schemas.jobs import JobSearchRequest
    from services.jobs.search_service import JobSearchService
    resp = JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="x")))
    assert resp.status == "success"
    assert {r.source for r in resp.results} == {"arbeitnow"}
    assert resp.provider_states["linkedin"] == ProviderState.error
    assert resp.provider_states["remotive"] == ProviderState.blocked
