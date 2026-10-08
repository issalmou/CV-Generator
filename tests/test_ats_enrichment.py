"""Phase 15 — bounded description enrichment spends its budget on the most
relevant offers first (LLM-free ranking), not on whatever the board returned
first."""

from __future__ import annotations

import pytest

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers.ats_common import rank_enrichment_targets
from services.providers.smartrecruiters_provider import SmartRecruitersProvider


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "AcmeCorp")
    monkeypatch.setattr(settings, "ATS_ENRICH_DESCRIPTIONS", True)
    monkeypatch.setattr(settings, "ATS_ENRICH_MAX", 2)


def _o(sid, title):
    return NormalizedOffer(source="x", source_job_id=sid,
                           source_url=f"https://x.test/{sid}", title=title)


def test_rank_enrichment_targets_orders_by_relevance():
    ctx = JobSearchContext(query="data scientist", skills=["python"])
    offers = [_o("1", "Office Manager"), _o("2", "Senior Data Scientist"),
              _o("3", "Warehouse Associate"), _o("4", "Junior Data Scientist, Python")]
    ranked = rank_enrichment_targets(offers, ctx)
    assert ranked[0].title in ("Senior Data Scientist", "Junior Data Scientist, Python")
    assert ranked[-1].title in ("Office Manager", "Warehouse Associate")


def test_rank_enrichment_targets_no_query_is_stable_and_safe():
    ctx = JobSearchContext()
    offers = [_o("1", "A Role"), _o("2", "B Role")]
    ranked = rank_enrichment_targets(offers, ctx)
    assert {o.source_job_id for o in ranked} == {"1", "2"}


def test_enrichment_budget_goes_to_the_matching_offers(fake_http):
    # page: 50 unrelated roles + 2 that match the query, all missing a description
    rows = [
        '{"id":"%d","name":"Facilities Coordinator %d","company":{"name":"Acme"},'
        '"location":{"city":"NYC","country":"us"}}' % (i, i)
        for i in range(50)
    ]
    rows += [
        '{"id":"901","name":"Staff Machine Learning Engineer","company":{"name":"Acme"},'
        '"location":{"city":"NYC","country":"us"}}',
        '{"id":"902","name":"Machine Learning Engineer, Platform","company":{"name":"Acme"},'
        '"location":{"city":"NYC","country":"us"}}',
    ]
    page = '{"content": [%s], "totalFound": 52}' % ",".join(rows)
    fake_http.route("companies/AcmeCorp/postings?", page)

    enriched: list[str] = []

    def _detail(full):
        # /postings/<id> — record which id got a detail fetch
        enriched.append(full.rsplit("/postings/", 1)[-1].split("?")[0])
        return ('{"jobAd":{"sections":{"jobDescription":{"text":"<p>Build ML systems.</p>"}}}}')

    fake_http.route("/postings/", _detail)

    result = SmartRecruitersProvider().search(
        JobSearchContext(query="machine learning engineer"), limit=50,
    )
    # only the 2 matching offers are returned (keyword filter) and both enriched
    assert {o.title for o in result.offers} == {
        "Staff Machine Learning Engineer", "Machine Learning Engineer, Platform",
    }
    assert set(enriched) == {"901", "902"}
    assert all("build ml systems" in (o.description or "").lower() for o in result.offers)
