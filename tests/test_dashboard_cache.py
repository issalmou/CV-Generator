"""v2.8 — Redis (via `services/cache_service.py`) caching `GET /api/dashboard`.

Covers: HIT/MISS, per-user isolation of the cache key, invalidation on a
write that changes the dashboard's own figures, and — the mission's explicit
requirement — a broken cache backend never breaks the dashboard (SQL is
always the correct fallback).
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import JobApplication, JobOffer, User
from services.cache_service import cache
from services.jobs.application_service import JobApplicationService
from services.user_dashboard_service import UserDashboardService, _cache_key, invalidate
from schemas.applications import ApplyRequest


def _user(db, email="u@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _offer(db, sid="o1"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    o = JobOffer(source="ashby", source_job_id=sid, content_hash=f"h{sid}",
                 source_url=f"https://x/{sid}", title="Dev",
                 first_seen_at=now, scraped_at=now, last_verified_at=now,
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
# key shape / isolation
# ---------------------------------------------------------------------------

def test_cache_key_is_scoped_to_the_user_id_only():
    k1 = _cache_key("user-a")
    k2 = _cache_key("user-b")
    assert k1 != k2
    assert "user-a" in k1 and "user-b" not in k1


def test_two_users_dashboards_never_share_a_cache_entry(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    _offer(db)
    db.add(JobApplication(user_id=a.id, job_offer_id=db.query(JobOffer).first().id, status="prepared"))
    db.commit()

    body_a = UserDashboardService(db, a).build()
    body_b = UserDashboardService(db, b).build()
    assert body_a["applications"]["total"] == 1
    assert body_b["applications"]["total"] == 0
    assert body_a["user_id"] != body_b["user_id"]


# ---------------------------------------------------------------------------
# HIT / MISS / invalidate
# ---------------------------------------------------------------------------

def test_second_build_is_a_cache_hit_with_stale_data(db):
    u = _user(db)
    first = UserDashboardService(db, u).build()
    assert first["applications"]["total"] == 0

    # mutate the DB directly, bypassing every invalidation hook
    offer = _offer(db)
    db.add(JobApplication(user_id=u.id, job_offer_id=offer.id, status="prepared"))
    db.commit()

    second = UserDashboardService(db, u).build()
    assert second["applications"]["total"] == 0   # HIT — still the stale cached value
    assert second == first


def test_invalidate_forces_a_fresh_read(db):
    u = _user(db)
    UserDashboardService(db, u).build()   # warms the cache at 0

    offer = _offer(db)
    db.add(JobApplication(user_id=u.id, job_offer_id=offer.id, status="prepared"))
    db.commit()
    invalidate(u.id)

    fresh = UserDashboardService(db, u).build()
    assert fresh["applications"]["total"] == 1


def test_cache_survives_a_missing_ttl_configuration_change(db, monkeypatch):
    monkeypatch.setattr(settings, "DASHBOARD_CACHE_TTL", 1)
    u = _user(db)
    body = UserDashboardService(db, u).build()
    assert cache.get(_cache_key(u.id)) is not None
    assert body["user_id"] == u.id


# ---------------------------------------------------------------------------
# write paths actually invalidate (real service calls, not direct DB writes)
# ---------------------------------------------------------------------------

def test_apply_invalidates_the_caller_dashboard(db):
    u = _user(db)
    offer = _offer(db)
    UserDashboardService(db, u).build()   # warm at 0 applications

    JobApplicationService(db).apply(u, offer.id, ApplyRequest())

    fresh = UserDashboardService(db, u).build()
    assert fresh["applications"]["total"] == 1


def test_save_job_invalidates_the_caller_dashboard(db):
    u = _user(db)
    offer = _offer(db)
    UserDashboardService(db, u).build()   # warm at 0 saved

    JobApplicationService(db).save_job(u, offer.id)

    fresh = UserDashboardService(db, u).build()
    assert fresh["jobs"]["offers_saved"] == 1

    JobApplicationService(db).unsave_job(u, offer.id)
    fresh2 = UserDashboardService(db, u).build()
    assert fresh2["jobs"]["offers_saved"] == 0


def test_dashboard_route_reflects_invalidation_end_to_end(db):
    u = _user(db)
    offer = _offer(db)
    client = _client(db, u)

    assert client.get("/api/dashboard").json()["applications"]["total"] == 0
    JobApplicationService(db).apply(u, offer.id, ApplyRequest())
    assert client.get("/api/dashboard").json()["applications"]["total"] == 1


# ---------------------------------------------------------------------------
# Redis (or any backend) unavailable — best-effort, SQL always wins
# ---------------------------------------------------------------------------

class _BrokenBackend:
    def get(self, key):
        raise ConnectionError("redis down")
    def set(self, key, value, ttl=None):
        raise ConnectionError("redis down")
    def delete(self, key):
        raise ConnectionError("redis down")
    def clear(self):
        raise ConnectionError("redis down")


def test_dashboard_works_when_the_cache_backend_is_down(db, monkeypatch):
    monkeypatch.setattr(cache, "_backend", _BrokenBackend())
    u = _user(db)
    offer = _offer(db)
    db.add(JobApplication(user_id=u.id, job_offer_id=offer.id, status="prepared"))
    db.commit()

    body = UserDashboardService(db, u).build()   # must not raise
    assert body["applications"]["total"] == 1


def test_dashboard_route_works_when_the_cache_backend_is_down(db, monkeypatch):
    monkeypatch.setattr(cache, "_backend", _BrokenBackend())
    u = _user(db)
    r = _client(db, u).get("/api/dashboard")
    assert r.status_code == 200


def test_invalidate_is_a_noop_when_backend_is_down(monkeypatch):
    monkeypatch.setattr(cache, "_backend", _BrokenBackend())
    invalidate("some-user-id")   # must not raise


# ---------------------------------------------------------------------------
# admin dashboard reuses the same TTL setting (no second cache system)
# ---------------------------------------------------------------------------

def test_dashboard_cache_payload_never_contains_the_llm_key(db, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "SECRET-LLM-KEY-DO-NOT-LEAK-777")
    u = _user(db)
    UserDashboardService(db, u).build()
    cached = cache.get(_cache_key(u.id))
    assert "SECRET-LLM-KEY-DO-NOT-LEAK-777" not in str(cached)


def test_admin_dashboard_uses_the_configurable_ttl(db, monkeypatch):
    calls = {}
    real_set = cache.set

    def _spy_set(key, value, ttl=None):
        if "admin" in key and "dashboard" in key:
            calls["ttl"] = ttl
        return real_set(key, value, ttl)

    monkeypatch.setattr(settings, "DASHBOARD_CACHE_TTL", 123)
    monkeypatch.setattr(cache, "set", _spy_set)

    admin = _user(db, "admin@x.com")
    admin.is_superadmin = True
    db.commit()
    _client(db, admin).get("/api/admin/dashboard")
    assert calls.get("ttl") == 123
