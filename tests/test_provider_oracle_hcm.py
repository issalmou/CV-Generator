"""Oracle HCM — public CandidateExperience REST provider (case-sensitive paths)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.http import AccessDenied, HttpError
from services.providers.oracle_hcm_provider import OracleHcmProvider

_SITE_URL = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions"


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _sites(monkeypatch):
    monkeypatch.setattr(settings, "ORACLE_HCM_SITES", _SITE_URL)


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_oracle_parses_and_preserves_case(fake_http, job_fixtures, ctx):
    fake_http.route("recruitingCEJobRequisitionDetails", _fx(job_fixtures, "oracle_hcm_detail.json"))
    fake_http.route("recruitingCEJobRequisitions?", _fx(job_fixtures, "oracle_hcm_list.json"))
    result = OracleHcmProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    dba = next(o for o in result.offers if "Database Administrator" in o.title)
    assert dba.source == "oracle_hcm"
    assert dba.source_job_id == "eeho.fa.us2.oraclecloud.com:300000123456789"
    # site + path case preserved exactly (CX_1001, hcmUI/CandidateExperience)
    assert dba.source_url == (
        "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience"
        "/en/sites/CX_1001/job/300000123456789"
    )
    assert dba.city == "Austin"
    assert dba.remote_type == "hybrid"
    assert dba.posted_at is not None
    assert dba.description and "postgresql" in dba.description.lower()   # enrichment merged 4 fields
    assert "performance tuning" in dba.description.lower()


def test_oracle_intern_and_remote(fake_http, job_fixtures, ctx):
    fake_http.route("recruitingCEJobRequisitions?", _fx(job_fixtures, "oracle_hcm_list.json"))
    result = OracleHcmProvider().search(JobSearchContext(job_type=JobType.internship), limit=10)
    assert [o.title for o in result.offers] == ["Software Development Intern"]
    assert result.offers[0].remote_type == "remote"


def test_oracle_disabled_without_sites(monkeypatch, ctx):
    monkeypatch.setattr(settings, "ORACLE_HCM_SITES", "")
    p = OracleHcmProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_oracle_rejects_non_oracle_host(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ORACLE_HCM_SITES", "https://evil.example/hcmUI/CandidateExperience/en/sites/CX_1/x")
    result = OracleHcmProvider().search(ctx, limit=10)
    assert result.offers == []


def test_oracle_empty_site(fake_http, ctx):
    fake_http.route("recruitingCEJobRequisitions?", '{"items": [{"requisitionList": [], "TotalJobsCount": 0}]}')
    result = OracleHcmProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available and result.offers == []


def test_oracle_all_sites_403(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ORACLE_HCM_SITES",
                        "https://a.oraclecloud.com/en/sites/CX_1/x,https://b.oraclecloud.com/en/sites/CX_2/y")
    fake_http.route("oraclecloud.com", AccessDenied("HTTP 403"))
    assert OracleHcmProvider().search(ctx, limit=10).state is ProviderState.blocked


def test_oracle_all_sites_down(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ORACLE_HCM_SITES",
                        "https://a.oraclecloud.com/en/sites/CX_1/x,https://b.oraclecloud.com/en/sites/CX_2/y")
    fake_http.route("oraclecloud.com", HttpError("down"))
    assert OracleHcmProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable


def test_oracle_malformed_json(fake_http, ctx):
    fake_http.route("recruitingCEJobRequisitions?", "not json")
    assert OracleHcmProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable
