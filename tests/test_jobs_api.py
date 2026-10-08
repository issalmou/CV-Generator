"""HTTP API for jobs — the narrow public façade the dashboard needs (Phase 6).

There is NO manual job-search API: `/context`, `/search` (+async), `/sources`
were removed — the conversation agent runs `JobSearchService` internally. What
remains is job detail (scoped to the caller's selection), the Apply button,
application history, and selection management.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _jobs_helpers import FakeJobProvider, make_offer as _offer
from models import JobApplication, JobOffer, SavedJob


def _now():
    return datetime.now(timezone.utc)


def _seed_offer(db, *, sid="1", title="Backend Engineer", stale=False):
    ts = _now() - timedelta(days=5) if stale else _now()
    row = JobOffer(
        source="arbeitnow", source_job_id=sid, content_hash=f"h{sid}",
        source_url=f"https://arbeitnow.example/{sid}", title=title,
        first_seen_at=ts, scraped_at=ts, last_verified_at=ts,
        is_active=True, freshness="stale" if stale else "fresh",
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


def _select(db, user_id, offer_id):
    db.add(SavedJob(user_id=user_id, job_offer_id=offer_id, origin="agent"))
    db.commit()


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("get", "/api/jobs/applications"),
    ("get", "/api/jobs/saved"),
    ("get", "/api/jobs/anything"),
    ("post", "/api/jobs/anything/apply"),
    ("post", "/api/jobs/anything/save"),
])
def test_every_route_requires_a_token(anon_client, method, path):
    assert getattr(anon_client, method)(path).status_code == 401


def test_removed_manual_search_routes_are_gone():
    """No `POST /api/jobs/search|context` and no `GET /api/jobs/sources` in the
    live OpenAPI (they'd collide with `GET /api/jobs/{job_id}` → 405, so check
    the schema, not a status code)."""
    import main
    paths = set(main.app.openapi()["paths"])
    for p in ("/api/jobs/search", "/api/jobs/search/{search_id}",
              "/api/jobs/context", "/api/jobs/sources", "/api/stats"):
        assert p not in paths, p
    # what a search-like GET actually hits now: the scoped detail route
    assert "/api/jobs/{job_id}" in paths


# ---------------------------------------------------------------------------
# GET /{id} — scoped to the caller's selection / applications
# ---------------------------------------------------------------------------

def test_get_unknown_job_404(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_get_job_not_in_my_selection_is_404(client, db):
    """An offer that exists but the agent never retained for me is invisible."""
    offer = _seed_offer(db)
    assert client.get(f"/api/jobs/{offer.id}").status_code == 404


def test_get_job_detail_when_selected(client, db, test_user):
    offer = _seed_offer(db)
    _select(db, test_user.id, offer.id)
    r = client.get(f"/api/jobs/{offer.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["job"]["id"] == offer.id
    assert body["application"]["can_be_assisted"] is False
    assert body["application"]["apply_url"]


def test_get_job_visible_once_applied(client, db, test_user):
    offer = _seed_offer(db)
    db.add(JobApplication(user_id=test_user.id, job_offer_id=offer.id,
                          status="prepared", source="arbeitnow"))
    db.commit()
    assert client.get(f"/api/jobs/{offer.id}").status_code == 200


def test_get_job_revalidates_a_stale_row(client, db, test_user, swap_providers):
    swap_providers([FakeJobProvider("arbeitnow", revalidation="gone")])
    offer = _seed_offer(db, sid="99", stale=True)
    _select(db, test_user.id, offer.id)
    r = client.get(f"/api/jobs/{offer.id}")
    assert r.status_code == 200
    assert r.json()["freshness"] == "expired"
    db.refresh(offer)
    assert offer.is_active is False


# ---------------------------------------------------------------------------
# Apply — the button; delegates to JobApplicationService
# ---------------------------------------------------------------------------

def test_apply_records_an_application(client, db, test_user):
    offer = _seed_offer(db)
    _select(db, test_user.id, offer.id)
    r = client.post(f"/api/jobs/{offer.id}/apply", json={"prepare": True})
    assert r.status_code == 200
    body = r.json()
    assert body["application_status"] == "prepared"
    assert body["application_status"] != "submitted"
    assert db.query(JobApplication).filter_by(user_id=test_user.id).count() == 1


def test_apply_unknown_job_is_404(client):
    assert client.post("/api/jobs/nope/apply", json={}).status_code == 404


# ---------------------------------------------------------------------------
# selection management (save / unsave / list)
# ---------------------------------------------------------------------------

def test_save_then_unsave(client, db, test_user):
    offer = _seed_offer(db)
    assert client.post(f"/api/jobs/{offer.id}/save").status_code == 204
    assert [o["id"] for o in client.get("/api/jobs/saved").json()] == [offer.id]
    assert client.delete(f"/api/jobs/{offer.id}/save").status_code == 204
    assert client.get("/api/jobs/saved").json() == []


def test_saved_is_owner_scoped(client, db, test_user):
    import bcrypt
    from models import User
    other = User(email="x@y.com", password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode())
    db.add(other); db.commit(); db.refresh(other)
    offer = _seed_offer(db)
    db.add(SavedJob(user_id=other.id, job_offer_id=offer.id, origin="agent"))
    db.commit()
    assert client.get("/api/jobs/saved").json() == []


# ---------------------------------------------------------------------------
# applications history
# ---------------------------------------------------------------------------

def test_applications_list_and_detail(client, db, test_user):
    offer = _seed_offer(db)
    _select(db, test_user.id, offer.id)
    app_id = client.post(f"/api/jobs/{offer.id}/apply", json={"prepare": True}).json()["application_id"]

    lst = client.get("/api/jobs/applications")
    assert lst.status_code == 200 and any(a["id"] == app_id for a in lst.json())
    one = client.get(f"/api/jobs/applications/{app_id}")
    assert one.status_code == 200 and one.json()["status"] == "prepared"
