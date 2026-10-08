"""Phase 47 — the persistent structured job-search profile (`/api/profile`)."""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import User, UserProfile
from schemas.profile import UserProfileIn
from services.user_profile_service import UserProfileService


def _user(db, email="p@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


_SAMPLE = {
    "target_titles": ["Backend Developer", "Platform Engineer"],
    "skills": ["Python", "PostgreSQL", "Python"],   # dup dropped
    "languages": ["fr", "en"],
    "locations": ["Paris", "Remote EU"],
    "employment_types": ["full_time", "internship", "bogus"],  # bogus dropped
    "sectors": ["fintech"],
    "excluded_keywords": ["stage", "sales"],
    "remote_preference": "hybrid",
    "experience_level": "senior",
    "salary_min": 55000,
    "salary_max": 80000,
    "salary_currency": "eur",
}


def test_get_returns_empty_profile_when_none(db):
    r = _client(db, _user(db)).get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["target_titles"] == [] and body["skills"] == []
    assert body["salary_min"] is None


def test_put_then_get_roundtrip_and_normalisation(db):
    c = _client(db, _user(db))
    r = c.put("/api/profile", json=_SAMPLE)
    assert r.status_code == 200
    body = r.json()
    assert body["skills"] == ["Python", "PostgreSQL"]
    assert body["employment_types"] == ["full_time", "internship"]
    assert body["salary_currency"] == "EUR"
    assert body["remote_preference"] == "hybrid"

    again = c.get("/api/profile").json()
    assert again["target_titles"] == ["Backend Developer", "Platform Engineer"]
    assert again["salary_max"] == 80000


def test_put_is_upsert(db):
    c = _client(db, _user(db))
    c.put("/api/profile", json={"skills": ["Go"]})
    c.put("/api/profile", json={"skills": ["Rust"], "locations": ["Berlin"]})
    body = c.get("/api/profile").json()
    assert body["skills"] == ["Rust"] and body["locations"] == ["Berlin"]
    assert db.query(UserProfile).count() == 1


def test_bad_enum_is_422(db):
    c = _client(db, _user(db))
    assert c.put("/api/profile", json={"remote_preference": "teleport"}).status_code == 422
    assert c.put("/api/profile", json={"salary_min": -5}).status_code == 422


def test_profiles_are_owner_isolated(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    _client(db, a).put("/api/profile", json={"skills": ["secret-a"]})
    main.app.dependency_overrides.clear()
    body_b = _client(db, b).get("/api/profile").json()
    assert body_b["skills"] == []          # b never sees a's data


def test_delete(db):
    c = _client(db, _user(db))
    c.put("/api/profile", json={"skills": ["X"]})
    assert c.delete("/api/profile").status_code == 204
    assert c.get("/api/profile").json()["skills"] == []


def test_unauthenticated_401(db):
    main.app.dependency_overrides[get_db] = lambda: db
    assert TestClient(main.app).get("/api/profile").status_code == 401


def test_to_search_context_mapping(db):
    user = _user(db)
    UserProfileService(db).upsert(user, UserProfileIn(**_SAMPLE))
    ctx = UserProfileService.to_search_context(UserProfileService(db).get(user))
    assert ctx.query == "Backend Developer"
    assert "Platform Engineer" in ctx.keywords
    assert set(ctx.skills) >= {"Python", "PostgreSQL"}
    assert ctx.remote_type.value == "hybrid"
    assert ctx.experience_level.value == "senior"
    assert ctx.salary_min == 55000 and ctx.salary_currency == "EUR"
    assert "stage" in ctx.excluded_keywords
