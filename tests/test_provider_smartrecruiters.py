"""SmartRecruiters — public postings JSON provider (limit/offset + detail enrichment)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import ExperienceLevel, JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.http import AccessDenied, HttpError
from services.providers.smartrecruiters_provider import SmartRecruitersProvider


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_smartrecruiters_parses(fake_http, job_fixtures, ctx):
    fake_http.route("/postings/744000012345678", _fx(job_fixtures, "smartrecruiters_detail.json"))
    fake_http.route("companies/AcmeCorp/postings?", _fx(job_fixtures, "smartrecruiters.json"))
    result = SmartRecruitersProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    eng = next(o for o in result.offers if "Data Engineer" in o.title)
    assert eng.source == "smartrecruiters"
    assert eng.source_job_id == "AcmeCorp:744000012345678"
    assert eng.source_url == "https://jobs.smartrecruiters.com/AcmeCorp/744000012345678"
    assert eng.company == "Acme Corp"
    assert eng.city == "Berlin"
    assert eng.country == "de"
    assert eng.employment_type == "Full-time"
    assert eng.experience_level == ExperienceLevel.mid
    assert eng.posted_at is not None
    assert eng.description and "airflow" in eng.description.lower()   # from detail enrichment
    assert "<strong>" not in eng.description


def test_smartrecruiters_case_sensitive_slug_not_lowercased(monkeypatch):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "Visa1,BoschGroup")
    assert SmartRecruitersProvider()._board_tokens() == ["Visa1", "BoschGroup"]


def test_smartrecruiters_intern_detection(fake_http, job_fixtures, ctx):
    fake_http.route("companies/AcmeCorp/postings", _fx(job_fixtures, "smartrecruiters.json"))
    result = SmartRecruitersProvider().search(JobSearchContext(job_type=JobType.internship), limit=10)
    assert [o.title for o in result.offers] == ["Product Analytics Intern"]
    assert result.offers[0].remote_type == "remote"


def test_smartrecruiters_disabled_without_boards(monkeypatch, ctx):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "")
    p = SmartRecruitersProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_smartrecruiters_empty_board(fake_http, ctx):
    fake_http.route("companies/AcmeCorp/postings", '{"content": [], "totalFound": 0}')
    result = SmartRecruitersProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available and result.offers == []


def test_smartrecruiters_404_all_boards_blocked(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "A,B")
    fake_http.route("api.smartrecruiters.com", AccessDenied("HTTP 403"))
    result = SmartRecruitersProvider().search(ctx, limit=10)
    assert result.state is ProviderState.blocked


def test_smartrecruiters_all_down_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "A,B")
    fake_http.route("api.smartrecruiters.com", HttpError("nope"))
    result = SmartRecruitersProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable


def test_smartrecruiters_malformed_json(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")
    fake_http.route("companies/AcmeCorp/postings", "}{ bad")
    result = SmartRecruitersProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable


def test_smartrecruiters_pagination(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")
    page1 = '{"content": [%s], "totalFound": 150}' % ",".join(
        '{"id":"%d","name":"Engineer Number %d","company":{"name":"Acme"},"location":{"city":"NYC","country":"us"}}' % (i, i)
        for i in range(100)
    )
    page2 = '{"content": [{"id":"901","name":"Final Engineer Role","company":{"name":"Acme"},"location":{"city":"NYC","country":"us"}}], "totalFound": 150}'

    def _resp(full):
        return page2 if "offset=100" in full else page1

    fake_http.route("companies/AcmeCorp/postings", _resp)
    result = SmartRecruitersProvider().search(ctx, limit=200)
    assert len(result.offers) == 101
    assert any(o.title == "Final Engineer Role" for o in result.offers)
