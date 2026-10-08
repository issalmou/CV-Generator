"""Indeed best-effort provider — JSON-LD parse + graceful Cloudflare block."""

from __future__ import annotations

import pytest

from schemas.jobs import JobSearchContext, ProviderStatus
from services.providers.http import AccessDenied
from services.providers.indeed_provider import IndeedProvider


@pytest.fixture
def ctx():
    return JobSearchContext(query="data scientist", location="Paris")


def test_parses_serp_jsonld(fake_http, job_fixtures, ctx):
    fake_http.route("/jobs", (job_fixtures / "indeed_serp.html").read_text())
    result = IndeedProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    titles = {o.title for o in result.offers}
    assert {"Data Scientist", "Junior Data Analyst"} <= titles
    ds = next(o for o in result.offers if o.title == "Data Scientist")
    assert ds.source == "indeed"
    assert ds.company == "DataCorp"
    assert ds.city == "Paris"
    assert ds.expires_at is not None


def test_cloudflare_block_is_unavailable(fake_http, ctx):
    fake_http.route("/jobs", AccessDenied("HTTP 403"))
    result = IndeedProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable
    assert result.offers == []


def test_empty_page_stops_pagination(fake_http, ctx):
    fake_http.route("/jobs", (200, "<html><body>no results</body></html>"))
    result = IndeedProvider().search(ctx, limit=10)
    assert result.offers == []
    assert result.status in (ProviderStatus.success, ProviderStatus.partial)


def test_revalidate(fake_http, job_fixtures):
    class _Offer:
        source_url = "https://www.indeed.com/viewjob?jk=aaaa1111bbbb2222"

    fake_http.route("viewjob", (job_fixtures / "indeed_viewjob_jsonld.html").read_text())
    assert IndeedProvider().revalidate(_Offer()).state == "alive"

    fake_http.routes.clear()
    fake_http.route("viewjob", (404, ""))
    assert IndeedProvider().revalidate(_Offer()).state == "gone"

    fake_http.routes.clear()
    fake_http.route("viewjob", AccessDenied("HTTP 403"))
    assert IndeedProvider().revalidate(_Offer()).state == "unknown"
