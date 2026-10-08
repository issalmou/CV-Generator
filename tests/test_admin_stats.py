"""Phases 34-37 — superadmin usage / application / document stats + dashboard.

All figures are live SQL aggregates over existing tables; nothing is
fabricated. Activity windows use ``User.last_active_at``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from models import GeneratedCV, GeneratedLetter, JobApplication, JobOffer, SavedJob, User
from database import get_db
from dependencies.auth import get_current_user


def _now():
    return datetime.now(timezone.utc)


def _user(db, email, *, superadmin=False, active_days_ago=None, created_days_ago=0):
    u = User(
        email=email, is_superadmin=superadmin,
        password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode(),
        created_at=_now() - timedelta(days=created_days_ago),
        last_active_at=(None if active_days_ago is None
                        else _now() - timedelta(days=active_days_ago)),
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


def _offer(db, sid="o1"):
    o = JobOffer(source="ashby", source_job_id=sid, content_hash=f"h{sid}",
                 source_url=f"https://x/{sid}", title="Dev", company="Acme",
                 first_seen_at=_now(), scraped_at=_now(), last_verified_at=_now(),
                 is_active=True, freshness="fresh")
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
# auth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/admin/stats/usage", "/api/admin/stats/users",
    "/api/admin/stats/applications", "/api/admin/stats/applications/by-user",
    "/api/admin/stats/documents", "/api/admin/dashboard", "/api/admin/llm",
])
def test_all_stats_routes_require_superadmin(db, test_user, path):
    assert _client(db, test_user).get(path).status_code == 403


# ---------------------------------------------------------------------------
# Phase 34 — usage
# ---------------------------------------------------------------------------

def test_usage_windows_are_distinct_and_correct(db):
    admin = _user(db, "admin@x.com", superadmin=True, active_days_ago=0)
    _user(db, "today@x.com", active_days_ago=0)
    _user(db, "thisweek@x.com", active_days_ago=3)
    _user(db, "thismonth@x.com", active_days_ago=20)
    _user(db, "thisyear@x.com", active_days_ago=200)
    _user(db, "never@x.com", active_days_ago=None)

    body = _client(db, admin).get("/api/admin/stats/usage").json()
    assert body["total_users"] == 6
    assert body["dau"] == 2          # admin + today
    assert body["wau"] == 3          # + thisweek
    assert body["mau"] == 4          # + thismonth
    assert body["yau"] == 5          # + thisyear   (never@ excluded)


def test_per_user_stats_counts(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    bob = _user(db, "bob@x.com", active_days_ago=1)
    db.add(GeneratedCV(user_id=bob.id, filename="c.pdf", storage_key="k",
                       minio_bucket="b", language="en"))
    o = _offer(db)
    db.add(JobApplication(user_id=bob.id, job_offer_id=o.id, status="manual_required"))
    db.add(SavedJob(user_id=bob.id, job_offer_id=o.id))
    db.commit()

    rows = {r["email"]: r for r in _client(db, admin).get("/api/admin/stats/users").json()["users"]}
    assert rows["bob@x.com"]["cv_count"] == 1
    assert rows["bob@x.com"]["application_count"] == 1
    assert rows["bob@x.com"]["saved_job_count"] == 1
    assert rows["bob@x.com"]["applications_by_status"] == {"manual_required": 1}


# ---------------------------------------------------------------------------
# Phase 35 — applications
# ---------------------------------------------------------------------------

def test_application_stats_by_status_and_window(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    o = _offer(db)
    for st in ("manual_required", "manual_required", "unavailable", "failed"):
        db.add(JobApplication(user_id=admin.id, job_offer_id=o.id, status=st, source="ashby"))
    db.commit()

    body = _client(db, admin).get("/api/admin/stats/applications").json()
    assert body["total"] == 4
    assert body["by_status"]["manual_required"] == 2
    assert body["by_window"]["day"] == 4
    assert body["by_provider"]["ashby"] == 4
    assert "never submits" in body["note"]


def test_applications_per_user_ranked(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    heavy = _user(db, "heavy@x.com", active_days_ago=0)
    light = _user(db, "light@x.com", active_days_ago=0)
    o = _offer(db)
    for _ in range(5):
        db.add(JobApplication(user_id=heavy.id, job_offer_id=o.id, status="manual_required"))
    db.add(JobApplication(user_id=light.id, job_offer_id=o.id, status="manual_required"))
    db.commit()

    rows = _client(db, admin).get("/api/admin/stats/applications/by-user").json()["users"]
    assert rows[0]["email"] == "heavy@x.com" and rows[0]["application_count"] == 5


# ---------------------------------------------------------------------------
# Phase 36 — documents
# ---------------------------------------------------------------------------

def test_document_stats(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    for i in range(3):
        db.add(GeneratedCV(user_id=admin.id, filename=f"c{i}.pdf", storage_key=f"k{i}",
                           minio_bucket="b", language="en"))
    db.add(GeneratedLetter(user_id=admin.id, filename="l.pdf", storage_key="lk",
                           minio_bucket="b", language="en"))
    db.commit()
    cv0 = db.query(GeneratedCV).first()
    o = _offer(db)
    db.add(JobApplication(user_id=admin.id, job_offer_id=o.id, status="manual_required", cv_id=cv0.id))
    db.commit()

    body = _client(db, admin).get("/api/admin/stats/documents").json()
    assert body["cv_total"] == 3
    assert body["cover_letter_total"] == 1
    assert body["document_total"] == 4
    assert body["cv_used_in_application"] == 1
    assert body["created_by_window"]["cv"]["day"] == 3


# ---------------------------------------------------------------------------
# Phase 37 — dashboard  (+ never leaks the LLM key)
# ---------------------------------------------------------------------------

def test_dashboard_bundles_everything(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    body = _client(db, admin).get("/api/admin/dashboard").json()
    assert set(body) >= {"usage", "applications", "documents", "providers", "llm"}
    assert body["usage"]["total_users"] == 1
    assert isinstance(body["providers"], list) and body["providers"]
    assert "api_key" not in body["llm"] and "configured" in body["llm"]


def test_llm_view_never_exposes_the_key(db, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "LLM_API_KEY", "top-secret-abc123")
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    for path in ("/api/admin/llm", "/api/admin/dashboard"):
        assert "top-secret-abc123" not in _client(db, admin).get(path).text


# ---------------------------------------------------------------------------
# Phases 55 + 57 — enriched application stats + dashboard boards section
# ---------------------------------------------------------------------------

def test_application_stats_carry_the_richer_fields(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    o = _offer(db)
    db.add_all([
        JobApplication(user_id=admin.id, job_offer_id=o.id, status="prepared",
                       source="ashby", match_score=0.8, cv_id="cv1"),
        JobApplication(user_id=admin.id, job_offer_id=o.id, status="duplicate",
                       source="ashby", match_score=0.6),
        JobApplication(user_id=admin.id, job_offer_id=o.id, status="failed", source="lever"),
    ])
    db.commit()
    body = _client(db, admin).get("/api/admin/stats/applications").json()
    assert body["duplicate_count"] == 1 and body["failed_count"] == 1
    assert body["prepared_count"] == 1
    assert body["avg_match_score"] == pytest.approx(0.7, abs=0.01)
    assert body["cv_used_count"] == 1


# ---------------------------------------------------------------------------
# Phase 70 — job-search activity by window (from the UsageEvent log only)
# ---------------------------------------------------------------------------

def test_jobs_activity_stats_by_window(db):
    from models import UsageEvent
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    bob = _user(db, "bob@x.com", active_days_ago=0)
    o = _offer(db)
    # admin: 2 searches today, bob: 1 search today + 1 search 100 days ago
    db.add_all([
        UsageEvent(user_id=admin.id, kind="search"),
        UsageEvent(user_id=admin.id, kind="search"),
        UsageEvent(user_id=bob.id, kind="search"),
        UsageEvent(user_id=bob.id, kind="search",
                   created_at=_now() - timedelta(days=100)),
        UsageEvent(user_id=admin.id, kind="job_view"),
        UsageEvent(user_id=bob.id, kind="job_saved"),
    ])
    db.add(SavedJob(user_id=bob.id, job_offer_id=o.id))
    db.commit()

    body = _client(db, admin).get("/api/admin/stats/jobs").json()
    assert body["searches_by_window"]["day"] == 3
    assert body["searches_by_window"]["year"] == 4
    assert body["searching_users_by_window"]["day"] == 2
    assert body["searching_users_by_window"]["year"] == 2
    assert body["offer_views_by_window"]["day"] == 1
    assert body["offers_saved_by_window"]["day"] == 1
    assert body["total_saved_offers"] == 1


def test_jobs_activity_route_requires_superadmin(db, test_user):
    assert _client(db, test_user).get("/api/admin/stats/jobs").status_code == 403


def test_dashboard_carries_jobs_section(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    body = _client(db, admin).get("/api/admin/dashboard").json()
    assert "jobs" in body and "searches_by_window" in body["jobs"]


def test_provider_stats_expose_derived_offer_fields(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    body = _client(db, admin).get("/api/admin/providers").json()
    assert body
    row = body[0]
    assert "offers_per_run" in row and "empty_result_rate" in row


def test_dashboard_has_a_boards_section(db):
    admin = _user(db, "a@x.com", superadmin=True, active_days_ago=0)
    from models import AtsBoard
    db.add_all([
        AtsBoard(provider="ashby", token="a1", enabled=True),
        AtsBoard(provider="ashby", token="a2", enabled=False),
        AtsBoard(provider="lever", token="l1", enabled=True, last_test_ok=True),
    ])
    db.commit()
    body = _client(db, admin).get("/api/admin/dashboard").json()
    boards = {b["provider"]: b for b in body["boards"]}
    assert boards["ashby"]["total"] == 2 and boards["ashby"]["enabled"] == 1
    assert boards["lever"]["last_test_ok"] == 1
