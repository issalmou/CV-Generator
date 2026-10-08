"""Phase 31 — superadmin gate + user administration.

Covers the authorization matrix (normal 403 / anon 401 / superadmin ok), the
impossibility of self-granting ``is_superadmin`` over the API, the
``SUPERADMIN_EMAILS`` bootstrap, and the self-lockout guards.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import User


def _mk_user(db, email, *, superadmin=False, active=True):
    u = User(
        email=email,
        password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode(),
        is_superadmin=superadmin, is_active=active,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _client(db, user=None):
    main.app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        main.app.dependency_overrides[get_current_user] = lambda: user
    c = TestClient(main.app)
    return c


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# authorization matrix
# ---------------------------------------------------------------------------

def test_normal_user_gets_403_on_admin_routes(db, test_user):
    c = _client(db, test_user)
    assert c.get("/api/admin/users").status_code == 403
    assert c.get(f"/api/admin/users/{test_user.id}").status_code == 403
    assert c.patch(f"/api/admin/users/{test_user.id}", json={"is_active": False}).status_code == 403


def test_anonymous_gets_401_on_admin_routes(db):
    c = _client(db)
    assert c.get("/api/admin/users").status_code == 401


def test_superadmin_can_list_users(db):
    admin = _mk_user(db, "admin@x.com", superadmin=True)
    _mk_user(db, "alice@x.com")
    c = _client(db, admin)
    r = c.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {u["email"] for u in body["users"]} == {"admin@x.com", "alice@x.com"}


# ---------------------------------------------------------------------------
# privilege escalation is impossible over the API
# ---------------------------------------------------------------------------

def test_signup_cannot_set_is_superadmin(anon_client, db):
    r = anon_client.post("/api/auth/signup", json={
        "email": "sneaky@x.com", "password": "Passw0rd!", "is_superadmin": True,
    })
    assert r.status_code == 201
    assert r.json()["user"]["is_superadmin"] is False
    assert db.scalar(
        __import__("sqlalchemy").select(User).where(User.email == "sneaky@x.com")
    ).is_superadmin is False


def test_me_exposes_is_superadmin_readonly(db):
    admin = _mk_user(db, "boss@x.com", superadmin=True)
    c = _client(db, admin)
    assert c.get("/api/auth/me").json()["is_superadmin"] is True


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------

def test_bootstrap_promotes_listed_emails_only(db, monkeypatch):
    from config import settings
    from services.admin_service import bootstrap_superadmins

    _mk_user(db, "chosen@x.com")
    _mk_user(db, "other@x.com")
    monkeypatch.setattr(settings, "SUPERADMIN_EMAILS", "chosen@x.com, missing@x.com")

    n = bootstrap_superadmins(db)
    assert n == 1
    assert db.scalar(__import__("sqlalchemy").select(User).where(User.email == "chosen@x.com")).is_superadmin
    assert not db.scalar(__import__("sqlalchemy").select(User).where(User.email == "other@x.com")).is_superadmin


def test_bootstrap_never_demotes(db, monkeypatch):
    from config import settings
    from services.admin_service import bootstrap_superadmins

    _mk_user(db, "keep@x.com", superadmin=True)
    monkeypatch.setattr(settings, "SUPERADMIN_EMAILS", "")  # empty
    bootstrap_superadmins(db)
    assert db.scalar(__import__("sqlalchemy").select(User).where(User.email == "keep@x.com")).is_superadmin


# ---------------------------------------------------------------------------
# PATCH + self-lockout guards
# ---------------------------------------------------------------------------

def test_superadmin_can_promote_and_demote_another(db):
    admin = _mk_user(db, "root@x.com", superadmin=True)
    bob = _mk_user(db, "bob@x.com")
    c = _client(db, admin)

    assert c.patch(f"/api/admin/users/{bob.id}", json={"is_superadmin": True}).json()["is_superadmin"] is True
    assert c.patch(f"/api/admin/users/{bob.id}", json={"is_superadmin": False}).json()["is_superadmin"] is False


def test_cannot_revoke_own_superadmin(db):
    admin = _mk_user(db, "solo@x.com", superadmin=True)
    _mk_user(db, "backup@x.com", superadmin=True)   # not the last one
    c = _client(db, admin)
    r = c.patch(f"/api/admin/users/{admin.id}", json={"is_superadmin": False})
    assert r.status_code == 400


def test_cannot_deactivate_self(db):
    admin = _mk_user(db, "self@x.com", superadmin=True)
    c = _client(db, admin)
    assert c.patch(f"/api/admin/users/{admin.id}", json={"is_active": False}).status_code == 400


def test_cannot_remove_the_last_superadmin(db):
    a = _mk_user(db, "a@x.com", superadmin=True)
    b = _mk_user(db, "b@x.com", superadmin=True)
    c = _client(db, a)
    # a demotes b -> a is now the only superadmin
    assert c.patch(f"/api/admin/users/{b.id}", json={"is_superadmin": False}).status_code == 200
    # b (re-authenticated as a normal user can't) — use a fresh admin? none left but `a`.
    # `a` cannot demote `a` (own-revoke guard) AND `a` is the last -> both guards.
    r = c.patch(f"/api/admin/users/{a.id}", json={"is_superadmin": False})
    assert r.status_code == 400
    # re-promote b, then a can demote a? still own-revoke guard blocks self.
    c.patch(f"/api/admin/users/{b.id}", json={"is_superadmin": True})
    r2 = c.patch(f"/api/admin/users/{b.id}", json={"is_superadmin": False})
    assert r2.status_code == 200  # b is not the actor and not the last (a remains)
