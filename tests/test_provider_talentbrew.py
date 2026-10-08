"""TalentBrew — legacy AJAX search-results provider (HTML fragment parsing)."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState
from services.providers.http import AccessDenied, HttpError
from services.providers.talentbrew_provider import TalentBrewProvider

_URL = "https://careers.acme.com/search-jobs/results?ActiveFacetID=0&CurrentPage=1&RecordsPerPage=15"


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "TALENTBREW_BOARDS", _URL)


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_talentbrew_parses_fragment(fake_http, job_fixtures, ctx):
    fake_http.route("careers.acme.com/search-jobs/results", _fx(job_fixtures, "talentbrew.json"))
    result = TalentBrewProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    titles = {o.title for o in result.offers}
    assert {"Quantitative Developer", "Risk Analyst Intern"} <= titles
    assert "Search" not in titles                             # nav <a> rejected

    qd = next(o for o in result.offers if "Quantitative" in o.title)
    assert qd.source == "talentbrew"
    assert qd.source_url == "https://careers.acme.com/job/new-york/quantitative-developer/12345/98765"
    assert qd.source_job_id == "careers.acme.com:98765"
    assert qd.location == "New York, NY, United States"
    assert qd.city == "New York"

    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == JobType.internship


def test_talentbrew_keyword_filter(fake_http, job_fixtures):
    fake_http.route("search-jobs/results", _fx(job_fixtures, "talentbrew.json"))
    result = TalentBrewProvider().search(JobSearchContext(query="risk"), limit=10)
    assert [o.title for o in result.offers] == ["Risk Analyst Intern"]


def test_talentbrew_disabled_without_boards(monkeypatch, ctx):
    monkeypatch.setattr(settings, "TALENTBREW_BOARDS", "")
    assert TalentBrewProvider().enabled is False
    assert TalentBrewProvider().search(ctx, limit=10).state is ProviderState.disabled


def test_talentbrew_pagination_stops_when_no_new(fake_http, job_fixtures, ctx):
    calls = {"n": 0}

    def _resp(_full):
        calls["n"] += 1
        return _fx(job_fixtures, "talentbrew.json")            # same rows every page

    fake_http.route("search-jobs/results", _resp)
    result = TalentBrewProvider().search(ctx, limit=50)
    assert len(result.offers) == 2
    assert calls["n"] <= 3                                     # de-dup break, no 12x loop


def test_talentbrew_empty_results(fake_http, ctx):
    fake_http.route("search-jobs/results", '{"results": "", "totalHits": 0}')
    result = TalentBrewProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available and result.offers == []


def test_talentbrew_all_boards_403(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "TALENTBREW_BOARDS",
                        "https://a.example/search-jobs/results?x=1,https://b.example/search-jobs/results?x=1")
    fake_http.route("search-jobs/results", AccessDenied("HTTP 403"))
    assert TalentBrewProvider().search(ctx, limit=10).state is ProviderState.blocked


def test_talentbrew_all_boards_down(fake_http, monkeypatch, ctx):
    monkeypatch.setattr(settings, "TALENTBREW_BOARDS",
                        "https://a.example/search-jobs/results?x=1,https://b.example/search-jobs/results?x=1")
    fake_http.route("search-jobs/results", HttpError("down"))
    assert TalentBrewProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable


def test_talentbrew_malformed_json(fake_http, ctx):
    fake_http.route("search-jobs/results", "not json at all")
    assert TalentBrewProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable
