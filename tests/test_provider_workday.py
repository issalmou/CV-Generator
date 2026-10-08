"""Workday — public CXS JSON provider (POST list + bounded detail enrichment)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.http import AccessDenied, HttpError
from services.providers.workday_provider import WorkdayProvider

_BOARD = "https://acme.wd1.myworkdayjobs.com/AcmeCareers"


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _tenants(monkeypatch):
    monkeypatch.setattr(settings, "WORKDAY_TENANTS", _BOARD)


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_workday_parses_list(fake_http, job_fixtures, ctx):
    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _fx(job_fixtures, "workday_jobs.json"))
    fake_http.route("Principal-Software-Engineer", _fx(job_fixtures, "workday_detail.json"))
    result = WorkdayProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    eng = next(o for o in result.offers if "Principal" in o.title)
    assert eng.source == "workday"
    assert eng.source_job_id == "acme:JR1988021"
    assert eng.source_url == f"{_BOARD}/job/San-Jose-CA/Principal-Software-Engineer--Platform_JR1988021"
    assert eng.location == "San Jose, CA"
    assert eng.posted_at is not None                       # from startDate
    # "2 Locations" placeholder is dropped, not stored as a location
    intern = next(o for o in result.offers if "Internship" in o.title)
    assert intern.location is None
    assert intern.job_type == JobType.internship
    assert intern.posted_at is not None                    # "Posted Today"


def test_workday_case_preserved_in_url(fake_http, job_fixtures, ctx):
    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _fx(job_fixtures, "workday_jobs.json"))
    result = WorkdayProvider().search(ctx, limit=10)
    assert all("/AcmeCareers/" in o.source_url for o in result.offers)   # not lower-cased


def test_workday_description_enrichment(fake_http, job_fixtures, ctx):
    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _fx(job_fixtures, "workday_jobs.json"))
    fake_http.route("_JR1988021", _fx(job_fixtures, "workday_detail.json"))
    result = WorkdayProvider().search(ctx, limit=10)
    eng = next(o for o in result.offers if "Principal" in o.title)
    assert eng.description and "kubernetes" in eng.description.lower()
    assert "<b>" not in eng.description


def test_workday_enrichment_disabled(fake_http, job_fixtures, ctx, monkeypatch):
    monkeypatch.setattr(settings, "ATS_ENRICH_DESCRIPTIONS", False)
    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _fx(job_fixtures, "workday_jobs.json"))
    result = WorkdayProvider().search(ctx, limit=10)
    assert all(o.description is None for o in result.offers)   # no artificial data


def test_workday_enrichment_failure_is_non_fatal(fake_http, job_fixtures, ctx):
    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _fx(job_fixtures, "workday_jobs.json"))
    fake_http.route("_JR1988021", HttpError("detail down"))
    result = WorkdayProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available          # search still succeeds
    assert result.offers


def test_workday_pagination_stops_on_empty_page(fake_http, job_fixtures, ctx):
    calls = {"n": 0}

    def _resp(_full):
        calls["n"] += 1
        return _fx(job_fixtures, "workday_jobs.json") if calls["n"] == 1 else '{"total": 40, "jobPostings": []}'

    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _resp)
    result = WorkdayProvider().search(ctx, limit=50)
    assert result.offers                                     # got page 1
    assert calls["n"] <= 3                                   # stopped, didn't loop 15x


def test_workday_disabled_without_tenants(monkeypatch, ctx):
    monkeypatch.setattr(settings, "WORKDAY_TENANTS", "")
    p = WorkdayProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_workday_bad_tenant_url_yields_no_offers(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "WORKDAY_TENANTS", "https://not-workday.example/careers")
    result = WorkdayProvider().search(ctx, limit=10)
    assert result.offers == []
    assert result.state is ProviderState.available          # no failure, just nothing


def test_workday_all_tenants_403_is_blocked(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "WORKDAY_TENANTS",
                        "https://a.wd1.myworkdayjobs.com/A,https://b.wd1.myworkdayjobs.com/B")
    fake_http.route("myworkdayjobs.com", AccessDenied("HTTP 403"))
    result = WorkdayProvider().search(ctx, limit=10)
    assert result.state is ProviderState.blocked


def test_workday_all_tenants_down_is_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "WORKDAY_TENANTS",
                        "https://a.wd1.myworkdayjobs.com/A,https://b.wd1.myworkdayjobs.com/B")
    fake_http.route("myworkdayjobs.com", HttpError("down"))
    result = WorkdayProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable
    assert result.status is ProviderStatus.unavailable
