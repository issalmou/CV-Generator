"""The JSON-API providers: Arbeitnow, Remotive, Jobicy, RemoteOK, Himalayas, Adzuna."""

from __future__ import annotations

import pytest

from schemas.jobs import JobSearchContext, JobType, ProviderStatus
from services.providers.arbeitnow_provider import ArbeitnowProvider
from services.providers.adzuna_provider import AdzunaProvider
from services.providers.himalayas_provider import HimalayasProvider
from services.providers.http import AccessDenied
from services.providers.jobicy_provider import JobicyProvider
from services.providers.remoteok_provider import RemoteOkProvider
from services.providers.remotive_provider import RemotiveProvider


@pytest.fixture
def ctx():
    return JobSearchContext()


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Arbeitnow
# ---------------------------------------------------------------------------

def test_arbeitnow_parses(fake_http, job_fixtures, ctx):
    fake_http.route("job-board-api", _fx(job_fixtures, "arbeitnow.json"))
    result = ArbeitnowProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert {o.title for o in result.offers} == {"Data Scientist", "Machine Learning Internship"}
    ds = next(o for o in result.offers if o.title == "Data Scientist")
    assert ds.remote_type == "remote" and "Python" in ds.skills
    assert ds.posted_at is not None


def test_arbeitnow_internship_filter(fake_http, job_fixtures):
    fake_http.route("job-board-api", _fx(job_fixtures, "arbeitnow.json"))
    ctx = JobSearchContext(query="", job_type=JobType.internship)
    result = ArbeitnowProvider().search(ctx, limit=10)
    assert [o.title for o in result.offers] == ["Machine Learning Internship"]


def test_arbeitnow_malformed_json(fake_http, ctx):
    fake_http.route("job-board-api", (200, "{not json"))
    result = ArbeitnowProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable


def test_arbeitnow_empty(fake_http, ctx):
    fake_http.route("job-board-api", (200, '{"data": []}'))
    result = ArbeitnowProvider().search(ctx, limit=10)
    assert result.offers == []


# ---------------------------------------------------------------------------
# Remotive
# ---------------------------------------------------------------------------

def test_remotive_parses_and_salary(fake_http, job_fixtures, ctx):
    fake_http.route("remotive.com/api/remote-jobs", _fx(job_fixtures, "remotive.json"))
    result = RemotiveProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    ds = next(o for o in result.offers if o.title == "Data Scientist")
    assert ds.remote_type == "remote"
    assert ds.salary_min == 90000 and ds.salary_max == 120000
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == "internship"


# ---------------------------------------------------------------------------
# Jobicy
# ---------------------------------------------------------------------------

def test_jobicy_parses(fake_http, job_fixtures, ctx):
    fake_http.route("jobicy.com/api/v2/remote-jobs", _fx(job_fixtures, "jobicy.json"))
    result = JobicyProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    sdet = next(o for o in result.offers if o.title == "SDET II")
    assert sdet.experience_level == "senior"
    assert sdet.salary_min == 90000 and sdet.salary_currency == "USD"


# ---------------------------------------------------------------------------
# RemoteOK — best-effort
# ---------------------------------------------------------------------------

def test_remoteok_skips_legal_notice(fake_http, job_fixtures, ctx):
    fake_http.route("remoteok.com/api", _fx(job_fixtures, "remoteok.json"))
    result = RemoteOkProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert all(o.title for o in result.offers)
    assert {o.title for o in result.offers} == {"Data Scientist", "ML Internship"}


def test_remoteok_403_unavailable(fake_http, ctx):
    fake_http.route("remoteok.com/api", AccessDenied("HTTP 403"))
    result = RemoteOkProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable and result.offers == []


# ---------------------------------------------------------------------------
# Himalayas — best-effort
# ---------------------------------------------------------------------------

def test_himalayas_parses(fake_http, job_fixtures, ctx):
    fake_http.route("himalayas.app/jobs/api", _fx(job_fixtures, "himalayas.json"))
    result = HimalayasProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    ds = next(o for o in result.offers if o.title == "Data Scientist")
    assert ds.remote_type == "remote" and ds.experience_level == "senior"


def test_himalayas_403_unavailable(fake_http, ctx):
    fake_http.route("himalayas.app/jobs/api", AccessDenied("HTTP 429"))
    result = HimalayasProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable


# ---------------------------------------------------------------------------
# Adzuna — optional / env-gated
# ---------------------------------------------------------------------------

def test_adzuna_disabled_without_credentials(monkeypatch, fake_http, ctx):
    monkeypatch.setattr("config.settings.ADZUNA_APP_ID", "")
    monkeypatch.setattr("config.settings.ADZUNA_APP_KEY", "")
    provider = AdzunaProvider()
    assert provider.enabled is False
    result = provider.search(ctx, limit=10)
    assert result.status == ProviderStatus.disabled
    assert fake_http.calls == []                    # never called out


def test_adzuna_parses_with_credentials(monkeypatch, fake_http, job_fixtures):
    monkeypatch.setattr("config.settings.ADZUNA_APP_ID", "id")
    monkeypatch.setattr("config.settings.ADZUNA_APP_KEY", "key")
    fake_http.route("api.adzuna.com", _fx(job_fixtures, "adzuna.json"))
    ctx = JobSearchContext(query="data scientist", country="United Kingdom")
    provider = AdzunaProvider()
    assert provider.enabled is True
    result = provider.search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    ds = next(o for o in result.offers if o.title == "Data Scientist")
    assert ds.company == "Globex Ltd" and ds.salary_min == 55000
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == "internship"
