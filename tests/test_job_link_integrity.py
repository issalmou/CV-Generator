"""Phase 26 — every surfaced offer keeps a usable link to the original posting.

The link must survive provider -> normalize -> dedup -> persist -> API, verbatim,
and an offer with no usable ``source_url`` is dropped (never shown with a dead
link, never given a fabricated one).
"""

from __future__ import annotations

import pytest

from _jobs_helpers import FakeJobProvider, make_offer
from models import JobOffer
from schemas.jobs import JobSearchContext, JobSearchRequest
from services.jobs.search_service import JobSearchService


def _search(db, **ctx):
    req = JobSearchRequest(context=JobSearchContext(query="engineer", **ctx))
    return JobSearchService(db).search(req)


def test_source_url_survives_provider_to_api_verbatim(db, swap_providers):
    url = "https://jobs.example.com/postings/abc-123?utm_source=x&gh_jid=42"
    prov = FakeJobProvider("ashby", [make_offer("ashby", "abc-123", url=url)])
    swap_providers([prov])

    resp = _search(db)
    assert len(resp.results) == 1
    assert resp.results[0].source_url == url
    assert resp.results[0].source == "ashby"
    assert resp.results[0].source_job_id == "abc-123"

    # and it is what got persisted
    row = db.query(JobOffer).one()
    assert row.source_url == url


def test_offer_without_usable_url_is_dropped_not_fabricated(db, swap_providers):
    good = make_offer("lever", "1", url="https://jobs.lever.co/acme/1")
    bad_empty = make_offer("lever", "2", url="   ")
    bad_scheme = make_offer("lever", "3", url="javascript:alert(1)")
    bad_relative = make_offer("lever", "4", url="/acme/4")
    swap_providers([FakeJobProvider("lever", [good, bad_empty, bad_scheme, bad_relative])])

    resp = _search(db)
    assert {r.source_job_id for r in resp.results} == {"1"}
    assert db.query(JobOffer).count() == 1


def test_dedup_keeps_primary_link_and_records_the_other(db, swap_providers):
    a = FakeJobProvider(
        "arbeitnow",
        [make_offer("arbeitnow", "1", url="https://arbeitnow.com/view/xyz")],
        priority=1,
    )
    b = FakeJobProvider(
        "linkedin",
        [make_offer("linkedin", "2", url="https://linkedin.com/jobs/view/999")],
        priority=100,
    )
    swap_providers([a, b])

    resp = _search(db)
    assert len(resp.results) == 1
    merged = resp.results[0]
    assert merged.source == "linkedin"
    assert merged.source_url == "https://linkedin.com/jobs/view/999"
    assert "https://arbeitnow.com/view/xyz" in merged.also_seen_on


def test_detail_endpoint_and_search_agree_on_the_link(db, test_user, swap_providers, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from database import get_db
    from dependencies.auth import get_current_user

    url = "https://boards.greenhouse.io/acme/jobs/55"
    swap_providers([FakeJobProvider("greenhouse", [make_offer("greenhouse", "55", url=url)])])

    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: test_user
    try:
        # the agent runs the search internally; persist the result as a selection
        from models import SavedJob
        from schemas.jobs import JobSearchContext, JobSearchRequest
        from services.jobs.search_service import JobSearchService

        resp = JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="engineer")))
        job = resp.results[0]
        assert job.source_url == url
        db.add(SavedJob(user_id=test_user.id, job_offer_id=job.id, origin="agent"))
        db.commit()

        client = TestClient(main.app)
        detail = client.get(f"/api/jobs/{job.id}")
        assert detail.status_code == 200
        assert detail.json()["job"]["source_url"] == url
        # the apply URL falls back to the real posting URL
        assert detail.json()["application"]["apply_url"] == url
    finally:
        main.app.dependency_overrides.clear()


def test_whitespace_wrapped_url_is_trimmed_not_dropped(db, swap_providers):
    prov = FakeJobProvider("remotive", [make_offer("remotive", "1", url="  https://remotive.com/x  ")])
    swap_providers([prov])
    resp = _search(db)
    assert resp.results[0].source_url == "https://remotive.com/x"


# ---------------------------------------------------------------------------
# Phase 45 — the URL-safety matrix
# ---------------------------------------------------------------------------

import pytest as _pytest


@_pytest.mark.parametrize("bad_url", [
    "",
    "   ",
    "/acme/jobs/1",                              # relative
    "javascript:alert(1)",
    "ftp://example.com/job",
    "data:text/html,<h1>x</h1>",
    "https://user:secret@jobs.example.com/1",    # credentials in URL
    "https://token@jobs.example.com/1",
    "https://",                                  # no host
    "https:///path",                             # no host
    "https://localhost/job",                     # internal host
    "https://127.0.0.1/job",
    "https://10.1.2.3/job",
    "https://169.254.169.254/latest/meta-data",  # cloud metadata
    "https://[::1]/job",
    "https://jobs.example.com/\nSet-Cookie: x",   # control chars
])
def test_unsafe_urls_drop_the_offer(db, swap_providers, bad_url):
    good = make_offer("ashby", "ok", url="https://jobs.ashbyhq.com/acme/ok")
    bad = make_offer("ashby", "bad", url=bad_url)
    swap_providers([FakeJobProvider("ashby", [good, bad])])

    resp = JobSearchService(db).search(
        JobSearchRequest(context=JobSearchContext(query="engineer"))
    )
    ids = {r.source_job_id for r in resp.results}
    assert "bad" not in ids
    assert "ok" in ids


@_pytest.mark.parametrize("good_url", [
    "https://jobs.ashbyhq.com/acme/1",
    "http://boards.greenhouse.io/acme/jobs/2",
    "https://acme.com/careers/3?utm_source=x&gh_jid=9",
    "https://sub.domain.example.co.uk/job/4#section",
])
def test_safe_urls_are_kept_verbatim(db, swap_providers, good_url):
    swap_providers([FakeJobProvider("ashby", [make_offer("ashby", "1", url=good_url)])])
    resp = JobSearchService(db).search(
        JobSearchRequest(context=JobSearchContext(query="engineer"))
    )
    assert resp.results and resp.results[0].source_url == good_url


def test_normalizedoffer_validator_blanks_credential_urls():
    from schemas.jobs import NormalizedOffer
    o = NormalizedOffer(source="x", source_job_id="1",
                        source_url="https://user:pw@example.com/1", title="T")
    assert o.source_url == ""   # blanked -> the service then drops it
