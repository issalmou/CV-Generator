"""LinkedIn multi-strategy provider — parsing, fallback, isolation, enrichment."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState, ProviderStatus
from services.providers.http import AccessDenied, HttpError
from services.providers.linkedin_provider import LinkedInProvider

SEARCH_API = "seeMoreJobPostings/search"
SEARCH_PAGE = "/jobs/search"
FRAGMENT = "jobPosting/"
JOBVIEW = "/jobs/view/"


@pytest.fixture
def ctx():
    return JobSearchContext(query="data scientist", location="Paris")


def _load(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_strategy_a_parses_cards(fake_http, job_fixtures, ctx):
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, _load(job_fixtures, "linkedin_job_fragment.html"))
    fake_http.route(JOBVIEW, _load(job_fixtures, "linkedin_jobview_jsonld.html"))

    result = LinkedInProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert result.strategy_used == "guest_search_api"
    titles = {o.title for o in result.offers}
    assert "Data Scientist Intern" in titles
    ds = next(o for o in result.offers if o.title == "Data Scientist Intern")
    assert ds.source == "linkedin"
    assert ds.source_job_id == "3911223344"
    assert ds.company == "Globex"
    assert ds.city == "Paris"
    # enrichment ran: description + criteria came from the fragment
    assert ds.description and "internship" in ds.description.lower()
    assert ds.job_type == "internship"
    # prompt-injection text in the description is data, not honoured — still stored as text
    assert "ignore all previous instructions" in ds.description.lower()


def test_falls_back_to_strategy_b(fake_http, job_fixtures, ctx):
    fake_http.route(SEARCH_API, AccessDenied("HTTP 429"))
    fake_http.route(SEARCH_PAGE, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, HttpError("blocked"))
    fake_http.route(JOBVIEW, HttpError("blocked"))

    result = LinkedInProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert result.strategy_used == "guest_search_page"
    assert result.offers


def test_all_strategies_fail_is_unavailable_not_exception(fake_http, ctx):
    fake_http.route(SEARCH_API, AccessDenied("HTTP 999"))
    fake_http.route(SEARCH_PAGE, AccessDenied("HTTP 999"))
    provider = LinkedInProvider()
    result = provider.search(ctx, limit=10)
    assert result.status == ProviderStatus.unavailable
    assert result.offers == []
    assert result.error_kind


def test_403_trips_circuit_breaker(fake_http, ctx, monkeypatch):
    monkeypatch.setattr("services.providers.circuit.settings.JOB_CIRCUIT_FAIL_THRESHOLD", 1)
    fake_http.route(SEARCH_API, AccessDenied("HTTP 403"))
    fake_http.route(SEARCH_PAGE, AccessDenied("HTTP 403"))
    provider = LinkedInProvider()
    provider.search(ctx, limit=5)
    assert not provider.breaker.allow()
    # next call short-circuits without hitting HTTP
    n_before = len(fake_http.calls)
    result = provider.search(ctx, limit=5)
    assert result.status == ProviderStatus.unavailable
    assert len(fake_http.calls) == n_before


def test_internship_filter_and_param(fake_http, job_fixtures):
    calls = fake_http.calls
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, _load(job_fixtures, "linkedin_job_fragment.html"))
    fake_http.route(JOBVIEW, "<html></html>")
    ctx = JobSearchContext(query="data", location="Paris", job_type=JobType.internship)
    result = LinkedInProvider().search(ctx, limit=10)
    assert any("f_JT=I" in c for c in calls)
    # only the internship offer survives the generic job_type filter
    assert all(o.job_type == "internship" for o in result.offers)


def test_enrichment_failure_keeps_the_card(fake_http, job_fixtures, ctx):
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, AccessDenied("HTTP 403"))
    fake_http.route(JOBVIEW, AccessDenied("HTTP 403"))
    result = LinkedInProvider().search(ctx, limit=10)
    assert result.status == ProviderStatus.success
    assert len(result.offers) == 2                 # cards survive even with 0 enrichment


def test_revalidate_gone_and_alive(fake_http, job_fixtures):
    provider = LinkedInProvider()

    class _Offer:
        source_job_id = "3911223344"
        source_url = "https://www.linkedin.com/jobs/view/3911223344"

    fake_http.route(FRAGMENT, (404, ""))
    assert provider.revalidate(_Offer()).state == "gone"

    fake_http.routes.clear()
    fake_http.route(FRAGMENT, _load(job_fixtures, "linkedin_job_fragment.html"))
    assert provider.revalidate(_Offer()).state == "alive"

    fake_http.routes.clear()
    fake_http.route(FRAGMENT, AccessDenied("HTTP 429"))
    assert provider.revalidate(_Offer()).state == "unknown"


def test_application_method_prefers_external_url(fake_http, job_fixtures):
    class _Offer:
        source = "linkedin"
        source_job_id = "3911223344"
        source_url = "https://www.linkedin.com/jobs/view/3911223344"
        metadata = {"apply_url": "https://careers.globex.example/apply/data-scientist-intern"}

    method = LinkedInProvider().application_method(_Offer())
    assert method.kind == "external_url"
    assert "careers.globex.example" in method.url

    class _NoExternal:
        source = "linkedin"
        source_job_id = "1"
        source_url = "https://www.linkedin.com/jobs/view/1"
        metadata = {}

    method2 = LinkedInProvider().application_method(_NoExternal())
    assert method2.kind == "platform_login_required"


# ---------------------------------------------------------------------------
# Part B — public-provider hardening
# ---------------------------------------------------------------------------

def test_authwall_maps_to_auth_required(fake_http, job_fixtures, ctx):
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_authwall.html"))
    fake_http.route(SEARCH_PAGE, _load(job_fixtures, "linkedin_authwall.html"))
    result = LinkedInProvider().search(ctx, limit=10)
    assert result.state is ProviderState.auth_required
    assert result.status is ProviderStatus.unavailable
    assert result.offers == []


def test_hard_block_maps_to_blocked(fake_http, ctx):
    fake_http.route(SEARCH_API, AccessDenied("HTTP 999"))
    fake_http.route(SEARCH_PAGE, AccessDenied("HTTP 999"))
    result = LinkedInProvider().search(ctx, limit=10)
    assert result.state is ProviderState.blocked


def _card(job_id: int, title: str = "Data Engineer") -> str:
    return (
        f'<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">'
        f'<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{job_id}"></a>'
        f'<h3 class="base-search-card__title">{title} {job_id}</h3>'
        f'<span class="job-search-card__location">Paris, France</span></div></li>'
    )


def test_deeper_pagination_uses_page_size_25(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_MAX_PAGES", 4)
    pages = {"n": 0}

    def _serp(url):
        pages["n"] += 1
        n = pages["n"]
        if n > 4:
            return ""
        return "<ul>" + "".join(_card(1000 * n + i) for i in range(25)) + "</ul>"

    fake_http.route(SEARCH_API, _serp)
    fake_http.route(FRAGMENT, "<html></html>")
    fake_http.route(JOBVIEW, "<html></html>")
    result = LinkedInProvider().search(JobSearchContext(query="x"), limit=999)
    starts = [c for c in fake_http.calls if "seeMoreJobPostings" in c]
    assert any("start=0" in c for c in starts)
    assert any("start=75" in c for c in starts)          # page 4 with a 25-step
    assert not any("start=100" in c for c in starts)     # capped at LINKEDIN_MAX_PAGES
    assert len(result.offers) == 100                     # 4 distinct pages of 25


def test_pagination_stops_when_a_page_has_no_new_ids(fake_http, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_MAX_PAGES", 6)
    calls = {"n": 0}

    def _serp(url):
        calls["n"] += 1
        return _load(job_fixtures, "linkedin_guest_search.html")   # same 2 ids every page

    fake_http.route(SEARCH_API, _serp)
    fake_http.route(FRAGMENT, "<html></html>")
    fake_http.route(JOBVIEW, "<html></html>")
    LinkedInProvider().search(JobSearchContext(query="x"), limit=999)
    assert calls["n"] == 2       # page 1 (2 new) + page 2 (0 new) -> stop


def test_preferred_companies_triggers_one_extra_pass(fake_http, job_fixtures):
    calls = []

    def _serp(url):
        calls.append(url)
        return _load(job_fixtures, "linkedin_guest_search.html")

    fake_http.route(SEARCH_API, _serp)
    fake_http.route(FRAGMENT, "<html></html>")
    fake_http.route(JOBVIEW, "<html></html>")
    ctx = JobSearchContext(query="data", location="Paris", preferred_companies=["Globex"])
    LinkedInProvider().search(ctx, limit=999)
    assert any("keywords=data+Globex" in c or "keywords=data%20Globex" in c
               or ("Globex" in c) for c in calls)


# ---------------------------------------------------------------------------
# Phase 13 — guest coverage & data-quality improvements
# ---------------------------------------------------------------------------

def test_freshness_param_is_sent(fake_http, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_FRESHNESS_DAYS", 14)
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, "<html></html>")
    fake_http.route(JOBVIEW, "<html></html>")
    LinkedInProvider().search(JobSearchContext(query="x"), limit=5)
    assert any("f_TPR=r1209600" in c for c in fake_http.calls)   # 14 * 86400


def test_freshness_param_disabled_when_zero(fake_http, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_FRESHNESS_DAYS", 0)
    fake_http.route(SEARCH_API, _load(job_fixtures, "linkedin_guest_search.html"))
    fake_http.route(FRAGMENT, "<html></html>")
    fake_http.route(JOBVIEW, "<html></html>")
    LinkedInProvider().search(JobSearchContext(query="x"), limit=5)
    assert not any("f_TPR" in c for c in fake_http.calls)


def test_salary_parsed_from_search_card(fake_http, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_ENRICH_MAX", 0)          # isolate card parsing
    fake_http.route(SEARCH_API, AccessDenied("HTTP 429"))
    fake_http.route(SEARCH_PAGE, _load(job_fixtures, "linkedin_search_page_jsonld.html"))
    result = LinkedInProvider().search(JobSearchContext(query="data"), limit=10)
    sds = next(o for o in result.offers if o.title == "Senior Data Scientist")
    assert sds.salary_min == 65000 and sds.salary_max == 85000
    assert sds.salary_currency == "EUR"


def test_strategy_b_merges_jsonld_itemlist(fake_http, job_fixtures, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_ENRICH_MAX", 0)
    fake_http.route(SEARCH_API, AccessDenied("HTTP 429"))
    fake_http.route(SEARCH_PAGE, _load(job_fixtures, "linkedin_search_page_jsonld.html"))
    result = LinkedInProvider().search(JobSearchContext(query="data"), limit=10)
    ids = {o.source_job_id for o in result.offers}
    # the intern is only in the JSON-LD ItemList, not the HTML cards
    assert "4001112224" in ids
    intern = next(o for o in result.offers if o.source_job_id == "4001112224")
    assert intern.job_type == "internship"
    assert intern.description and "etl" in intern.description.lower()
    assert intern.posted_at is not None
    # the card-backed one gets its date/description filled from the JSON-LD
    sds = next(o for o in result.offers if o.source_job_id == "4001112223")
    assert sds.posted_at is not None
    assert sds.expires_at is not None


def test_enrichment_is_bounded_by_linkedin_enrich_max(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_ENRICH_MAX", 3)
    cards = "<ul>" + "".join(_card(5000 + i) for i in range(20)) + "</ul>"
    fake_http.route(SEARCH_API, cards)
    frag_calls = {"n": 0}

    def _frag(url):
        frag_calls["n"] += 1
        return "<html><div class='description__text'>real description text here</div></html>"

    fake_http.route(FRAGMENT, _frag)
    fake_http.route(JOBVIEW, "<html></html>")
    LinkedInProvider().search(JobSearchContext(query="x"), limit=20)
    assert frag_calls["n"] <= 3          # only LINKEDIN_ENRICH_MAX offers enriched
