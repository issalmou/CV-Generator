"""CareerPagesProvider — browser-rendered public career pages -> JSON-LD offers."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, ProviderState
from services.providers.browser import BrowserUnavailable
from services.providers.career_pages_provider import CareerPagesProvider


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_ENABLED", True)


def _html(job_fixtures):
    return (job_fixtures / "career_page_jsonld.html").read_text(encoding="utf-8")


def test_disabled_without_urls(monkeypatch):
    monkeypatch.setattr(settings, "CAREER_PAGE_URLS", "")
    p = CareerPagesProvider()
    assert p.enabled is False
    assert p.search(JobSearchContext(), limit=10).state is ProviderState.disabled


def test_renders_and_extracts_jsonld(mock_browser, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "CAREER_PAGE_URLS", "https://careers.globex.example/jobs")
    mock_browser.route("careers.globex.example", _html(job_fixtures))
    result = CareerPagesProvider().search(JobSearchContext(), limit=10)
    assert result.state is ProviderState.available
    titles = {o.title for o in result.offers}
    assert {"Staff Data Scientist", "Data Analyst Intern"} <= titles
    ds = next(o for o in result.offers if o.title == "Staff Data Scientist")
    assert ds.source == "career_pages"
    assert ds.company == "Globex" and ds.city == "Paris"
    assert ds.salary_min == 70000 and ds.salary_currency == "EUR"
    assert ds.expires_at is not None
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == "internship"


def test_keyword_filter(mock_browser, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "CAREER_PAGE_URLS", "https://careers.globex.example/jobs")
    mock_browser.route("globex", _html(job_fixtures))
    result = CareerPagesProvider().search(JobSearchContext(query="analyst"), limit=10)
    assert [o.title for o in result.offers] == ["Data Analyst Intern"]


def test_browser_unavailable_is_temporarily_unavailable(mock_browser, monkeypatch):
    monkeypatch.setattr(settings, "CAREER_PAGE_URLS", "https://careers.globex.example/jobs")
    mock_browser.route("globex", BrowserUnavailable("selenium not installed"))
    result = CareerPagesProvider().search(JobSearchContext(), limit=10)
    # every URL failed -> the provider rendered nothing -> temporarily unavailable
    assert result.state in (ProviderState.temporarily_unavailable, ProviderState.degraded)


def test_one_url_crash_others_still_parsed(mock_browser, job_fixtures, monkeypatch):
    monkeypatch.setattr(
        settings, "CAREER_PAGE_URLS",
        "https://dead.example/jobs,https://careers.globex.example/jobs",
    )
    mock_browser.route("dead.example", RuntimeError("crash"))
    mock_browser.route("globex", _html(job_fixtures))
    result = CareerPagesProvider().search(JobSearchContext(), limit=10)
    assert result.state is ProviderState.available
    assert result.offers


def test_linkedin_url_is_refused(mock_browser, monkeypatch):
    monkeypatch.setattr(settings, "CAREER_PAGE_URLS", "https://www.linkedin.com/jobs/search")
    result = CareerPagesProvider().search(JobSearchContext(), limit=10)
    assert "linkedin.com" not in " ".join(mock_browser.calls)
    assert result.offers == []
