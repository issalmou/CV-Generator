"""Greenhouse & Lever — public ATS JSON providers (no browser)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.greenhouse_provider import GreenhouseProvider
from services.providers.http import AccessDenied, HttpError
from services.providers.lever_provider import LeverProvider


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "acme")
    monkeypatch.setattr(settings, "LEVER_BOARDS", "acme")


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def test_greenhouse_parses(fake_http, job_fixtures, ctx):
    fake_http.route("boards-api.greenhouse.io/v1/boards/acme/jobs", _fx(job_fixtures, "greenhouse.json"))
    result = GreenhouseProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available
    titles = {o.title for o in result.offers}
    assert {"Senior Data Scientist", "Machine Learning Intern"} <= titles
    ds = next(o for o in result.offers if o.title == "Senior Data Scientist")
    assert ds.source == "greenhouse"
    assert ds.source_job_id == "acme:5501001"
    assert ds.city == "Paris"
    assert ds.description and "python" in ds.description.lower()
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == "internship"


def test_greenhouse_internship_filter(fake_http, job_fixtures):
    fake_http.route("greenhouse.io", _fx(job_fixtures, "greenhouse.json"))
    result = GreenhouseProvider().search(JobSearchContext(job_type=JobType.internship), limit=10)
    assert [o.title for o in result.offers] == ["Machine Learning Intern"]


def test_greenhouse_disabled_without_boards(monkeypatch, ctx):
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "")
    p = GreenhouseProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_greenhouse_one_board_404_others_still_returned(fake_http, job_fixtures, monkeypatch, ctx):
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "dead,acme")
    fake_http.route("boards/dead/jobs", (404, ""))
    fake_http.route("boards/acme/jobs", _fx(job_fixtures, "greenhouse.json"))
    result = GreenhouseProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available
    assert result.offers


def test_greenhouse_all_boards_fail_is_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "a,b")
    fake_http.route("greenhouse.io", HttpError("down"))
    result = GreenhouseProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable
    assert result.status is ProviderStatus.unavailable


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def test_lever_parses(fake_http, job_fixtures, ctx):
    fake_http.route("api.lever.co/v0/postings/acme", _fx(job_fixtures, "lever.json"))
    result = LeverProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available
    be = next(o for o in result.offers if "Backend" in o.title)
    assert be.source_job_id == "acme:abc-123-def"
    assert be.remote_type == "hybrid"
    assert be.city == "Berlin"
    assert be.posted_at is not None and be.posted_at.year == 2026   # ms epoch handled
    intern = next(o for o in result.offers if "Internship" in o.title)
    assert intern.job_type == "internship" and intern.remote_type == "remote"


def test_lever_403_is_blocked(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "LEVER_BOARDS", "acme")
    fake_http.route("api.lever.co", AccessDenied("HTTP 403"))
    result = LeverProvider().search(ctx, limit=10)
    assert result.state is ProviderState.blocked
