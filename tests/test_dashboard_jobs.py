"""Phase 6 — `GET /api/dashboard/jobs` (the jobs the agent retained).

Strictly owner-scoped. Each selected job carries its offer detail + the most
recent application (so the frontend renders a per-job Apply button).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import GeneratedCV, JobApplication, JobOffer, SavedJob, User


def _now():
    return datetime.now(timezone.utc)


def _user(db, email="u@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _offer(db, sid="o1", title="Backend Engineer", company="Globex"):
    o = JobOffer(source="ashby", source_job_id=sid, content_hash=f"h{sid}",
                 source_url=f"https://jobs.ashby.example/{sid}", title=title, company=company,
                 city="Paris", country="France", description="We use Python and PostgreSQL. " * 60,
                 skills=["Python", "PostgreSQL"], remote_type="remote",
                 first_seen_at=_now(), scraped_at=_now(), last_verified_at=_now(),
                 posted_at=_now() - timedelta(days=2),
                 is_active=True, freshness="fresh", expires_at=_now() + timedelta(days=20))
    db.add(o); db.commit(); db.refresh(o)
    return o


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------

def test_empty_selection_is_a_clean_200(db):
    u = _user(db)
    r = _client(db, u).get("/api/dashboard/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "success", "total": 0, "limit": 20, "offset": 0, "jobs": []}


def test_agent_selected_job_without_application(db):
    u = _user(db)
    o = _offer(db)
    db.add(SavedJob(user_id=u.id, job_offer_id=o.id, origin="agent",
                    match_score=0.72, conversation_id="conv-1"))
    db.commit()

    body = _client(db, u).get("/api/dashboard/jobs").json()
    assert body["total"] == 1
    job = body["jobs"][0]
    assert job["job_offer_id"] == o.id
    assert job["title"] == "Backend Engineer"
    assert job["company"] == "Globex"
    assert job["source_url"].startswith("https://")
    assert job["match_score"] == 0.72
    assert job["origin"] == "agent"
    assert job["conversation_id"] == "conv-1"
    assert job["freshness"] == "fresh"
    assert len(job["summary"]) <= 700         # truncated
    assert job["application"] is None          # not applied yet → Apply button


def test_selected_job_with_application_shows_status(db):
    u = _user(db)
    o = _offer(db)
    cv = GeneratedCV(user_id=u.id, filename="c.pdf", storage_key="k", minio_bucket="b", language="en")
    db.add(cv); db.add(SavedJob(user_id=u.id, job_offer_id=o.id, origin="agent"))
    db.commit()
    db.add(JobApplication(user_id=u.id, job_offer_id=o.id, status="prepared",
                          source="ashby", cv_id=cv.id, match_score=0.66,
                          application_url=o.source_url))
    db.commit()

    job = _client(db, u).get("/api/dashboard/jobs").json()["jobs"][0]
    assert job["application"]["status"] == "prepared"
    assert job["application"]["cv_id"] == cv.id
    assert job["application"]["match_score"] == 0.66


def test_manual_and_agent_selections_both_appear_newest_first(db):
    u = _user(db)
    o1, o2 = _offer(db, "a"), _offer(db, "b", title="Data Engineer")
    db.add(SavedJob(user_id=u.id, job_offer_id=o1.id, origin="agent",
                    created_at=_now() - timedelta(hours=2)))
    db.add(SavedJob(user_id=u.id, job_offer_id=o2.id, origin="manual",
                    created_at=_now()))
    db.commit()
    jobs = _client(db, u).get("/api/dashboard/jobs").json()["jobs"]
    assert [j["title"] for j in jobs] == ["Data Engineer", "Backend Engineer"]
    assert {j["origin"] for j in jobs} == {"agent", "manual"}


def test_pagination(db):
    u = _user(db)
    for i in range(5):
        o = _offer(db, f"p{i}")
        db.add(SavedJob(user_id=u.id, job_offer_id=o.id,
                        created_at=_now() - timedelta(minutes=i)))
    db.commit()
    body = _client(db, u).get("/api/dashboard/jobs?limit=2&offset=2").json()
    assert body["total"] == 5 and body["limit"] == 2 and body["offset"] == 2
    assert len(body["jobs"]) == 2


def test_owner_scoped_user_b_sees_nothing_of_user_a(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    o = _offer(db)
    db.add(SavedJob(user_id=a.id, job_offer_id=o.id, origin="agent"))
    db.commit()
    assert _client(db, b).get("/api/dashboard/jobs").json() == {
        "status": "success", "total": 0, "limit": 20, "offset": 0, "jobs": []}


def test_only_the_users_own_application_is_attached(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    o = _offer(db)
    db.add(SavedJob(user_id=a.id, job_offer_id=o.id, origin="agent"))
    db.commit()
    # B has an application to the same offer — must NOT leak onto A's card
    db.add(JobApplication(user_id=b.id, job_offer_id=o.id, status="prepared", source="ashby"))
    db.commit()
    job = _client(db, a).get("/api/dashboard/jobs").json()["jobs"][0]
    assert job["application"] is None


def test_requires_auth(db):
    main.app.dependency_overrides[get_db] = lambda: db
    assert TestClient(main.app).get("/api/dashboard/jobs").status_code == 401
