"""Ashby — public ATS JSON provider (no browser, no key)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.ashby_provider import AshbyProvider
from services.providers.http import AccessDenied, HttpError


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "acme")


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------

def test_ashby_parses_full_offer(fake_http, job_fixtures, ctx):
    fake_http.route("api.ashbyhq.com/posting-api/job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    assert result.status is ProviderStatus.success
    titles = {o.title for o in result.offers}
    assert {"Senior Backend Engineer", "Data Science Intern"} <= titles

    be = next(o for o in result.offers if o.title == "Senior Backend Engineer")
    assert be.source == "ashby"
    assert be.source_job_id == "acme:3f1c9a20-1111-4a10-9c00-aaaa00000001"
    assert be.source_url.startswith("https://jobs.ashbyhq.com/acme/")
    assert be.company == "Acme"
    assert be.city == "San Francisco"
    assert be.description and "payments platform" in be.description.lower()
    assert "<strong>" not in (be.description or "")          # HTML stripped to text
    assert be.salary_min == 180000 and be.salary_max == 220000
    assert be.salary_currency == "USD"
    assert be.employment_type == "FullTime"
    assert be.job_type == JobType.job


def test_ashby_intern_type_and_remote(fake_http, job_fixtures, ctx):
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(ctx, limit=10)
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == JobType.internship          # from employmentType=Intern
    assert intern.remote_type == "remote"                 # from isRemote=true


def test_ashby_skips_unlisted_rows(fake_http, job_fixtures, ctx):
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(ctx, limit=10)
    assert all("Product Designer" not in o.title for o in result.offers)   # isListed=false


def test_ashby_internship_context_filter(fake_http, job_fixtures):
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(JobSearchContext(job_type=JobType.internship), limit=10)
    assert [o.title for o in result.offers] == ["Data Science Intern"]


def test_ashby_keyword_filter(fake_http, job_fixtures):
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(JobSearchContext(query="backend"), limit=10)
    assert [o.title for o in result.offers] == ["Senior Backend Engineer"]


def test_ashby_disabled_without_boards(monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "")
    p = AshbyProvider()
    assert p.enabled is False
    assert p.search(ctx, limit=10).state is ProviderState.disabled


def test_ashby_empty_board_is_available_zero_results(fake_http, ctx):
    fake_http.route("job-board/acme", '{"jobs": []}')
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available       # 0 results is not a failure
    assert result.offers == []


def test_ashby_one_board_404_others_returned(fake_http, job_fixtures, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "dead,acme")
    fake_http.route("job-board/dead", (404, ""))
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available
    assert result.offers


def test_ashby_all_boards_unreachable_is_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "a,b")
    fake_http.route("api.ashbyhq.com", HttpError("down"))
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable
    assert result.status is ProviderStatus.unavailable


def test_ashby_all_boards_403_is_blocked(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "a,b")
    fake_http.route("api.ashbyhq.com", AccessDenied("HTTP 403"))
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.blocked


def test_ashby_429_is_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "a")
    fake_http.route("api.ashbyhq.com", AccessDenied("HTTP 429"))
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable


def test_ashby_malformed_json_is_temporarily_unavailable(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "acme")
    fake_http.route("job-board/acme", "not json {{{")
    result = AshbyProvider().search(ctx, limit=10)
    assert result.state is ProviderState.temporarily_unavailable


def test_ashby_no_artificial_data(fake_http, job_fixtures, ctx):
    fake_http.route("job-board/acme", _fx(job_fixtures, "ashby.json"))
    result = AshbyProvider().search(ctx, limit=10)
    intern = next(o for o in result.offers if "Intern" in o.title)
    # the intern row has no compensation block -> salary stays None, not 0
    assert intern.salary_min is None and intern.salary_max is None
