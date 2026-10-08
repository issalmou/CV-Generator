"""We Work Remotely — RSS provider."""

from __future__ import annotations

import pytest

from schemas.jobs import JobSearchContext, ProviderStatus
from services.providers.http import AccessDenied, HttpError
from services.providers.weworkremotely_provider import WeWorkRemotelyProvider


@pytest.fixture
def ctx():
    return JobSearchContext()


def test_parses_items_and_splits_title(fake_http, job_fixtures, ctx):
    fake_http.route(".rss", (job_fixtures / "wwr.rss").read_text())
    result = WeWorkRemotelyProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    dev = next(o for o in result.offers if "Backend" in o.title)
    assert dev.company == "Proxify AB"
    assert dev.title == "Senior Backend Developer (Python)"
    assert dev.remote_type == "remote"
    assert dev.source == "weworkremotely"
    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == "internship"


def test_deduplicates_within_feeds(fake_http, job_fixtures, ctx):
    fake_http.route(".rss", (job_fixtures / "wwr.rss").read_text())
    result = WeWorkRemotelyProvider().search(ctx, limit=10)
    ids = [o.source_job_id for o in result.offers]
    assert len(ids) == len(set(ids))


def test_malformed_xml_yields_nothing(fake_http, ctx):
    fake_http.route(".rss", (200, "<rss><channel><item>broken"))
    result = WeWorkRemotelyProvider().search(ctx, limit=10)
    assert result.offers == []


def test_all_feeds_blocked_is_unavailable(fake_http, ctx):
    fake_http.route(".rss", AccessDenied("HTTP 403"))
    result = WeWorkRemotelyProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable


def test_feed_http_error_is_skipped_not_fatal(fake_http, ctx, job_fixtures, monkeypatch):
    # first feed errors, a later one succeeds
    calls = {"n": 0}
    good = (job_fixtures / "wwr.rss").read_text()

    def _resp(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HttpError("timeout")
        return good

    fake_http.route(".rss", _resp)
    result = WeWorkRemotelyProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert result.offers
