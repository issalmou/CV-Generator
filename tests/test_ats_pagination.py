"""ATS pagination — a keyword search reads *past the first page* until it has
``limit`` matches (not ``limit`` raw postings), stays bounded, and honours the
per-provider deadline.
"""

from __future__ import annotations

import time

import pytest

from config import settings
from schemas.jobs import JobSearchContext, ProviderState
from services.providers.smartrecruiters_provider import SmartRecruitersProvider
from services.providers.workday_provider import WorkdayProvider


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")
    monkeypatch.setattr(settings, "WORKDAY_TENANTS", "https://acme.wd1.myworkdayjobs.com/AcmeCareers")
    monkeypatch.setattr(settings, "ATS_ENRICH_DESCRIPTIONS", False)


def _sr_page(idx: int, titles: list[str], total: int) -> str:
    rows = ",".join(
        '{"id":"%s","name":"%s","company":{"name":"Acme"},"location":{"city":"NYC","country":"us"}}'
        % (f"{idx}-{i}", t) for i, t in enumerate(titles)
    )
    return '{"content": [%s], "totalFound": %d}' % (rows, total)


def test_smartrecruiters_keyword_search_reads_beyond_first_page(fake_http):
    # 100 non-matching roles on page 1, the one match on page 2
    page1 = _sr_page(0, [f"Sales Manager {i}" for i in range(100)], 101)
    page2 = _sr_page(1, ["Senior Data Scientist"], 101)

    def _resp(full):
        return page2 if "offset=100" in full else page1

    fake_http.route("companies/AcmeCorp/postings", _resp)
    result = SmartRecruitersProvider().search(JobSearchContext(query="data scientist"), limit=50)
    assert [o.title for o in result.offers] == ["Senior Data Scientist"]
    assert result.state is ProviderState.available


def test_smartrecruiters_stops_at_limit_matches_not_whole_board(fake_http):
    # every posting matches -> should stop after `limit`, not drain 10 pages
    calls = {"n": 0}

    def _resp(full):
        calls["n"] += 1
        return _sr_page(calls["n"], [f"Data Engineer {i}" for i in range(100)], 100_000)

    fake_http.route("companies/AcmeCorp/postings", _resp)
    result = SmartRecruitersProvider().search(JobSearchContext(query="engineer"), limit=50)
    assert len(result.offers) == 50
    assert calls["n"] == 1                      # 50 found on page 1 -> no page 2


def test_smartrecruiters_pagination_hard_capped(fake_http):
    # a board that never terminates must still stop at _MAX_PAGES
    calls = {"n": 0}

    def _resp(full):
        calls["n"] += 1
        return _sr_page(calls["n"], [f"Widget Role {i}" for i in range(100)], 10_000_000)

    fake_http.route("companies/AcmeCorp/postings", _resp)
    # query matches nothing -> can't hit `limit`, must rely on the page cap
    result = SmartRecruitersProvider().search(JobSearchContext(query="zzzznomatch"), limit=50)
    assert result.offers == []
    assert calls["n"] <= 15                     # _MAX_PAGES


def test_smartrecruiters_pagination_stops_at_deadline(fake_http, monkeypatch):
    calls = {"n": 0}

    def _resp(full):
        calls["n"] += 1
        return _sr_page(calls["n"], [f"Role {i}" for i in range(100)], 10_000_000)

    fake_http.route("companies/AcmeCorp/postings", _resp)

    real = time.monotonic
    # deadline is "already 5s in the past" after the first page
    seq = iter([real(), real(), real() + 999, real() + 999, real() + 999])

    def _fake_monotonic():
        try:
            return next(seq)
        except StopIteration:
            return real() + 999

    monkeypatch.setattr("services.providers.smartrecruiters_provider.time.monotonic", _fake_monotonic)
    result = SmartRecruitersProvider().search(JobSearchContext(query="zzz"), limit=50)
    assert calls["n"] <= 2                      # bailed on the deadline check


def test_workday_keyword_search_paginates(fake_http):
    p1 = '{"total": 40, "jobPostings": [%s]}' % ",".join(
        '{"title":"Account Executive %d","externalPath":"/job/x/AE-%d_JR%d","locationsText":"NYC"}' % (i, i, i)
        for i in range(20)
    )
    p2 = ('{"total": 40, "jobPostings": ['
          '{"title":"Staff Machine Learning Engineer","externalPath":"/job/x/ML_JR999","locationsText":"Remote"}]}')

    def _resp(_full):
        # workday offset is in the POST body (not the URL) -> alternate by call count
        _resp.n = getattr(_resp, "n", 0) + 1
        return p1 if _resp.n == 1 else p2

    fake_http.route("/wday/cxs/acme/AcmeCareers/jobs", _resp)
    result = WorkdayProvider().search(JobSearchContext(query="machine learning"), limit=50)
    assert [o.title for o in result.offers] == ["Staff Machine Learning Engineer"]
