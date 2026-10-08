"""Hacker News 'Who is hiring?' provider (Algolia API)."""

from __future__ import annotations

import pytest

from schemas.jobs import JobSearchContext, ProviderStatus
from services.providers.hackernews_provider import HackerNewsProvider


def _wire(fake_http, job_fixtures):
    fake_http.route("search?", (job_fixtures / "hn_search.json").read_text())
    fake_http.route("/items/", (job_fixtures / "hn_whoishiring_item.json").read_text())


def test_parses_top_level_comments(fake_http, job_fixtures):
    _wire(fake_http, job_fixtures)
    result = HackerNewsProvider().search(JobSearchContext(), limit=10)
    assert result.status == ProviderStatus.success
    globex = next(o for o in result.offers if o.company == "Globex")
    assert globex.title == "Senior Data Scientist"
    assert globex.remote_type == "remote"
    assert "globex.example/jobs/ds" in globex.metadata["apply_urls"][0]
    assert globex.source_url.startswith("https://news.ycombinator.com/item?id=")
    # the deleted / author-less comment is skipped
    assert all(o.company for o in result.offers)
    assert len(result.offers) == 2


def test_keyword_filter(fake_http, job_fixtures):
    _wire(fake_http, job_fixtures)
    result = HackerNewsProvider().search(JobSearchContext(query="backend"), limit=10)
    assert [o.company for o in result.offers] == ["Initech"]


def test_no_hiring_story_returns_empty(fake_http):
    fake_http.route("search?", '{"hits": []}')
    result = HackerNewsProvider().search(JobSearchContext(), limit=10)
    assert result.offers == []
