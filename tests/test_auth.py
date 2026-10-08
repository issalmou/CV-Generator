"""
Auth API: signup / signin / me / the two-step verification-code password
reset. The DB runs on the per-test SQLite schema; e-mail is in dev-mode so
``forgot-password`` echoes the code.
"""

from __future__ import annotations

import pytest

from models import PasswordResetCode, User

_STRONG = "Str0ngPass1"
_STRONG2 = "An0therPass2"


def _signup(client, email="alice@example.com", password=_STRONG):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# signup / signin / me
# ---------------------------------------------------------------------------

def test_signup_returns_token_and_public_user(anon_client):
    r = _signup(anon_client)
    assert r.status_code == 201
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "alice@example.com"
    assert "password_hash" not in body["user"]
    assert "password" not in body["user"]


def test_signup_rejects_weak_password(anon_client):
    r = anon_client.post("/api/auth/signup", json={"email": "x@y.com", "password": "short"})
    assert r.status_code == 422


def test_signup_duplicate_email_is_409(anon_client):
    _signup(anon_client)
    r = _signup(anon_client)
    assert r.status_code == 409


def test_signin_ok_and_wrong_password_401(anon_client):
    _signup(anon_client)
    ok = anon_client.post("/api/auth/signin", json={"email": "alice@example.com", "password": _STRONG})
    assert ok.status_code == 200 and ok.json()["access_token"]

    bad = anon_client.post("/api/auth/signin", json={"email": "alice@example.com", "password": "Wr0ngPass9"})
    assert bad.status_code == 401


def test_me_requires_and_accepts_token(anon_client):
    token = _signup(anon_client).json()["access_token"]
    assert anon_client.get("/api/auth/me").status_code == 401

    r = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"
    assert "password_hash" not in r.json()


# ---------------------------------------------------------------------------
# forgot-password  — no user enumeration
# ---------------------------------------------------------------------------

def test_forgot_password_same_response_for_known_and_unknown(anon_client):
    _signup(anon_client)

    known = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    unknown = anon_client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]
    # dev-mode: a real account gets a code; an unknown one does not
    assert known.json()["reset_code"] is not None
    assert unknown.json()["reset_code"] is None


def test_forgot_password_invalidates_the_previous_code(anon_client, db):
    _signup(anon_client)
    first = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]
    anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})

    # the old code no longer works
    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": first, "new_password": _STRONG2,
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------

def test_reset_password_happy_path(anon_client):
    _signup(anon_client)
    code = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]

    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": code, "new_password": _STRONG2,
    })
    assert r.status_code == 200

    # old password no longer valid, new one is
    assert anon_client.post("/api/auth/signin", json={"email": "alice@example.com", "password": _STRONG}).status_code == 401
    assert anon_client.post("/api/auth/signin", json={"email": "alice@example.com", "password": _STRONG2}).status_code == 200


def test_reset_password_wrong_code_is_generic_400(anon_client):
    _signup(anon_client)
    anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": "000000", "new_password": _STRONG2,
    })
    assert r.status_code == 400
    assert r.json()["message"] == "Invalid or expired verification code."


def test_reset_password_code_is_single_use(anon_client):
    _signup(anon_client)
    code = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]
    body = {"email": "alice@example.com", "code": code, "new_password": _STRONG2}
    assert anon_client.post("/api/auth/reset-password", json=body).status_code == 200
    assert anon_client.post("/api/auth/reset-password", json=body).status_code == 400


def test_reset_password_locks_after_max_attempts(anon_client):
    _signup(anon_client)
    code = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]

    for _ in range(5):
        anon_client.post("/api/auth/reset-password", json={
            "email": "alice@example.com", "code": "999999", "new_password": _STRONG2,
        })
    # even the correct code is now dead
    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": code, "new_password": _STRONG2,
    })
    assert r.status_code == 400


def test_reset_password_expired_code_is_400(anon_client, db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _signup(anon_client)
    code = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]

    row = db.query(PasswordResetCode).first()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": code, "new_password": _STRONG2,
    })
    assert r.status_code == 400


def test_reset_password_rejects_weak_new_password(anon_client):
    _signup(anon_client)
    code = anon_client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json()["reset_code"]
    r = anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": code, "new_password": "weak",
    })
    assert r.status_code == 422


def test_password_hash_never_in_any_response(anon_client):
    r = _signup(anon_client)
    token = r.json()["access_token"]
    for resp in (
        r,
        anon_client.post("/api/auth/signin", json={"email": "alice@example.com", "password": _STRONG}),
        anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}),
    ):
        assert "password_hash" not in resp.text
