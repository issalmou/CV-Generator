"""v2.8 — `User.email` is UNIQUE at the database level, normalised (trim +
lowercase) everywhere it is stored or compared, and a collision is always a
clean `409` — never a `500`, even under a signup race.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import main
from database import get_db
from models import User
from services.auth_service import AuthError, AuthService, _normalize_email


def _client(db):
    main.app.dependency_overrides[get_db] = lambda: db
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Bob@Example.com", "bob@example.com"),
    ("  bob@example.com  ", "bob@example.com"),
    ("BOB@EXAMPLE.COM", "bob@example.com"),
    (" Bob@Example.COM ", "bob@example.com"),
])
def test_normalize_email(raw, expected):
    assert _normalize_email(raw) == expected


def test_signup_stores_the_normalized_email(db):
    r = _client(db).post("/api/auth/signup",
                         json={"email": " Bob@Example.COM ", "password": "Passw0rd!234"})
    assert r.status_code == 201
    assert r.json()["user"]["email"] == "bob@example.com"
    row = db.query(User).filter(User.email == "bob@example.com").first()
    assert row is not None


def test_signin_is_case_and_whitespace_insensitive(db):
    _client(db).post("/api/auth/signup", json={"email": "bob@example.com", "password": "Passw0rd!234"})
    r = _client(db).post("/api/auth/signin",
                         json={"email": "  BOB@EXAMPLE.COM  ", "password": "Passw0rd!234"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# duplicate -> 409, never 500
# ---------------------------------------------------------------------------

def test_duplicate_signup_same_case_is_409(db):
    _client(db).post("/api/auth/signup", json={"email": "dup@x.com", "password": "Passw0rd!234"})
    r = _client(db).post("/api/auth/signup", json={"email": "dup@x.com", "password": "Passw0rd!234"})
    assert r.status_code == 409
    assert r.json()["status"] == "error"


def test_duplicate_signup_different_case_and_spacing_is_409(db):
    _client(db).post("/api/auth/signup", json={"email": "dup2@x.com", "password": "Passw0rd!234"})
    r = _client(db).post("/api/auth/signup",
                         json={"email": "  Dup2@X.com  ", "password": "Passw0rd!234"})
    assert r.status_code == 409
    assert db.query(User).filter(User.email == "dup2@x.com").count() == 1


def test_signup_race_condition_returns_409_not_500(db):
    """Simulates two concurrent signups: both pass the SELECT existence check
    before either COMMITs. The DB-level UNIQUE constraint is what actually
    stops the duplicate — AuthService must turn that IntegrityError into a
    clean AuthError (409), never let it bubble up as a 500."""
    winner = User(email="race@x.com", password_hash="x")
    db.add(winner)
    db.commit()

    service = AuthService(db)
    # Force the pre-check to lie ("no existing account"), reproducing the
    # TOCTOU window a real race would hit.
    monkey_scalar = db.scalar
    db.scalar = lambda *a, **k: None
    try:
        with pytest.raises(AuthError, match="already exists"):
            service.signup("race@x.com", "Passw0rd!234")
    finally:
        db.scalar = monkey_scalar

    # session must still be usable afterwards (rollback happened) and no
    # duplicate row was actually inserted.
    assert db.query(User).filter(User.email == "race@x.com").count() == 1


def test_database_itself_rejects_a_duplicate_email(db):
    """Proves the UNIQUE constraint is enforced by the database, not only by
    the service's pre-check — insert two rows directly through the ORM."""
    db.add(User(email="direct@x.com", password_hash="x"))
    db.commit()
    db.add(User(email="direct@x.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------------------------------------------------------------------------
# SUPERADMIN_EMAILS matching is normalised the same way
# ---------------------------------------------------------------------------

def test_superadmin_emails_list_is_normalized(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "SUPERADMIN_EMAILS", " Admin@X.com , OTHER@Y.COM ")
    assert settings.superadmin_emails_list == ["admin@x.com", "other@y.com"]


def test_bootstrap_promotes_regardless_of_stored_case(db, monkeypatch):
    from config import settings
    from services.admin_service import bootstrap_superadmins

    user = User(email="future-admin@x.com", password_hash="x")
    db.add(user); db.commit()

    monkeypatch.setattr(settings, "SUPERADMIN_EMAILS", "Future-Admin@X.com")
    promoted = bootstrap_superadmins(db)
    assert promoted == 1
    db.refresh(user)
    assert user.is_superadmin is True


# ---------------------------------------------------------------------------
# no email-change endpoint exists yet — nothing to regress
# ---------------------------------------------------------------------------

def test_no_endpoint_lets_a_normal_user_change_their_email(db, monkeypatch):
    """Documents the current contract: if an email-change feature is ever
    added, it must reuse `_normalize_email` + the same UNIQUE-constraint
    handling. For now, no schema exposes an `email` field to a normal user
    outside signup/signin — this test fails loudly the day one is added
    without updating this suite."""
    from schemas.profile import UserProfileIn

    assert "email" not in UserProfileIn.model_fields
