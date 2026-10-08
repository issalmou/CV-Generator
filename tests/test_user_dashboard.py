"""Phase 69 — the per-user dashboard (`GET /api/dashboard`).

Strictly owner-scoped: every figure is the caller's own; no `user_id` is
accepted from the client. Nothing implies an external submission.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import (
    GeneratedCV, GeneratedLetter, JobApplication, JobOffer, SavedJob, UsageEvent, User,
)


def _now():
    return datetime.now(timezone.utc)


def _user(db, email="u@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _offer(db, sid="o1"):
    o = JobOffer(source="ashby", source_job_id=sid, content_hash=f"h{sid}",
                 source_url=f"https://jobs.ashby.example/{sid}", title="Dev",
                 first_seen_at=_now(), scraped_at=_now(), last_verified_at=_now(),
                 is_active=True, freshness="fresh")
    db.add(o); db.commit(); db.refresh(o)
    return o


def _client(db, user=None):
    main.app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


def _seed(db, user, *, apps=3, cvs=2, letters=1, searches=5, views=4, logins=2):
    o = _offer(db, f"o-{user.id[:6]}")
    for i in range(apps):
        db.add(JobApplication(
            user_id=user.id, job_offer_id=o.id,
            status=["prepared", "manual_required", "duplicate"][i % 3],
            source=["ashby", "lever"][i % 2], match_score=0.5 + 0.1 * i,
            cv_id=f"cv{i}" if i == 0 else None,
            missing_skills=["Kubernetes", "Terraform"] if i == 0 else None,
        ))
    for i in range(cvs):
        db.add(GeneratedCV(user_id=user.id, filename=f"c{i}.pdf", storage_key=f"k{i}",
                           minio_bucket="b", language="en"))
    for i in range(letters):
        db.add(GeneratedLetter(user_id=user.id, filename=f"l{i}.pdf", storage_key=f"lk{i}",
                               minio_bucket="b", language="en"))
    db.add(SavedJob(user_id=user.id, job_offer_id=o.id))
    for _ in range(searches):
        db.add(UsageEvent(user_id=user.id, kind="search"))
    for _ in range(views):
        db.add(UsageEvent(user_id=user.id, kind="job_view"))
    for _ in range(logins):
        db.add(UsageEvent(user_id=user.id, kind="login"))
    db.commit()


# ---------------------------------------------------------------------------

def test_dashboard_has_all_sections(db):
    u = _user(db)
    _seed(db, u)
    body = _client(db, u).get("/api/dashboard").json()
    assert set(body) == {"user_id", "activity", "jobs", "applications",
                         "auto_apply", "documents", "matching", "providers", "generated_at"}
    assert body["user_id"] == u.id


def test_counts_reflect_seeded_data(db):
    u = _user(db)
    _seed(db, u, apps=3, cvs=2, letters=1, searches=5, views=4, logins=2)
    body = _client(db, u).get("/api/dashboard").json()

    assert body["jobs"]["searches"] == 5
    assert body["jobs"]["offers_viewed"] == 4
    assert body["jobs"]["offers_saved"] == 1
    assert body["activity"]["sessions"]["month"] == 2

    assert body["applications"]["total"] == 3
    assert body["applications"]["by_status"]["prepared"] == 1
    assert body["applications"]["duplicates_avoided"] == 1
    assert body["applications"]["by_window"]["today"] == 3

    assert body["auto_apply"]["prepared_count"] == 1
    assert body["auto_apply"]["cv_attached_count"] == 1
    assert 0.0 <= body["auto_apply"]["avg_match_score"] <= 1.0

    assert body["documents"]["cv_total"] == 2
    assert body["documents"]["cover_letter_total"] == 1
    assert body["documents"]["cv_used_in_application"] == 1

    assert body["matching"]["scored_applications"] == 3
    assert {"skill": "Kubernetes", "count": 1} in body["matching"]["top_missing_skills"]

    provs = {p["provider"]: p["application_count"] for p in body["providers"]["by_provider"]}
    assert provs.get("ashby", 0) + provs.get("lever", 0) == 3


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------

def test_user_a_never_sees_user_b_data(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    _seed(db, a, apps=5, searches=10)
    # b has nothing
    body_b = _client(db, b).get("/api/dashboard").json()
    assert body_b["applications"]["total"] == 0
    assert body_b["jobs"]["searches"] == 0
    assert body_b["documents"]["cv_total"] == 0
    assert body_b["user_id"] == b.id


def test_no_user_id_query_param_is_honoured(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    _seed(db, b, apps=7)
    # a passes ?user_id=b — must be ignored
    body = _client(db, a).get(f"/api/dashboard?user_id={b.id}").json()
    assert body["user_id"] == a.id
    assert body["applications"]["total"] == 0


def test_unauthenticated_401(db):
    main.app.dependency_overrides[get_db] = lambda: db
    assert TestClient(main.app).get("/api/dashboard").status_code == 401


def test_dashboard_never_says_submitted(db):
    u = _user(db)
    _seed(db, u)
    body = _client(db, u).get("/api/dashboard").json()
    assert "submitted" not in str(body).lower() or "never submits" in body["auto_apply"]["note"]
    assert "submitted" not in body["applications"]["by_status"]


def test_empty_user_dashboard_is_all_zeros_not_an_error(db):
    u = _user(db)
    body = _client(db, u).get("/api/dashboard").json()
    assert body["applications"]["total"] == 0
    assert body["matching"]["avg_score"] is None
    assert body["providers"]["most_applied_provider"] is None
