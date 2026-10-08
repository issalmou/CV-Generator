"""Rippling ATS — public board JSON provider (flat array, uuid dedup)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState
from services.providers.http import AccessDenied, HttpError
from services.providers.rippling_provider import RipplingProvider


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "RIPPLING_BOARDS", "acme")


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_rippling_parses_and_dedupes_by_uuid(fake_http, job_fixtures, ctx):
    fake_http.route("api.rippling.com/platform/api/ats/v1/board/acme/jobs", _fx(job_fixtures, "rippling.json"))
    result = RipplingProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    # the billing role appears twice (same uuid) -> one offer
    titles = [o.title for o in result.offers]
    assert titles.count("Backend Engineer, Billing") == 1
    be = next(o for o in result.offers if "Billing" in o.title)
    assert be.source_job_id == "acme:ripp-1111-aaaa"
    assert be.description and "typescript" in be.description.lower()   # kept the entry that has a description
    assert "<b>" not in be.description
    assert be.skills == ["Engineering"]

    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == JobType.internship                      # employmentType=INTERN


def test_rippling_keyword_filter(fake_http, job_fixtures):
    fake_http.route("board/acme/jobs", _fx(job_fixtures, "rippling.json"))
    result = RipplingProvider().search(JobSearchContext(query="billing"), limit=10)
    assert [o.title for o in result.offers] == ["Backend Engineer, Billing"]


def test_rippling_disabled_without_boards(monkeypatch, ctx):
    monkeypatch.setattr(settings, "RIPPLING_BOARDS", "")
    p = RipplingProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_rippling_empty_board(fake_http, ctx):
    fake_http.route("board/acme/jobs", "[]")
    result = RipplingProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available and result.offers == []


def test_rippling_all_boards_403(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "RIPPLING_BOARDS", "a,b")
    fake_http.route("api.rippling.com", AccessDenied("HTTP 403"))
    assert RipplingProvider().search(ctx, limit=10).state is ProviderState.blocked


def test_rippling_all_boards_down(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "RIPPLING_BOARDS", "a,b")
    fake_http.route("api.rippling.com", HttpError("down"))
    assert RipplingProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable


def test_rippling_malformed_json(fake_http, ctx):
    fake_http.route("board/acme/jobs", "<<<")
    assert RipplingProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable
