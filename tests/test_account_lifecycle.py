"""
Account deactivation / reactivation (Phase 5, Theme D).

Covers the full lifecycle the frontend depends on:

    signin -> deactivate -> every existing token invalidated -> access denied
    signin on a disabled account -> 403 {code: "account_disabled"}
    request-reactivation (no enumeration) -> reactivate with the key ->
    fresh session, and any pre-deactivation token stays dead.

E-mail + both dev-modes are on (conftest) so the key is echoed in the body.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import ReactivationCode, User

_PW = "Str0ngPass1"
_PW2 = "An0therPass2"


def _signup(client, email="alice@example.com", password=_PW):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------

def test_deactivate_requires_the_current_password(anon_client):
    token = _signup(anon_client).json()["access_token"]
    r = anon_client.post(
        "/api/auth/deactivate", json={"password": "Wr0ngPass9"}, headers=_bearer(token)
    )
    assert r.status_code == 401
    # still usable
    assert anon_client.get("/api/auth/me", headers=_bearer(token)).status_code == 200


def test_deactivate_needs_authentication(anon_client):
    _signup(anon_client)
    assert anon_client.post("/api/auth/deactivate", json={"password": _PW}).status_code == 401


def test_deactivate_invalidates_every_existing_token(anon_client):
    first = _signup(anon_client).json()["access_token"]
    second = anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": _PW}
    ).json()["access_token"]

    r = anon_client.post(
        "/api/auth/deactivate", json={"password": _PW}, headers=_bearer(first)
    )
    assert r.status_code == 200

    # BOTH tokens (the caller's and the other session's) are now dead.
    for tok in (first, second):
        resp = anon_client.get("/api/auth/me", headers=_bearer(tok))
        assert resp.status_code == 403
        assert resp.json()["detail"] == "account_disabled"


def test_signin_on_disabled_account_is_403_with_code(anon_client):
    token = _signup(anon_client).json()["access_token"]
    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(token))

    r = anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": _PW}
    )
    assert r.status_code == 403
    body = r.json()
    assert body["status"] == "error"
    assert body["code"] == "account_disabled"
    assert "access_token" not in body


def test_wrong_password_on_disabled_account_stays_401(anon_client):
    token = _signup(anon_client).json()["access_token"]
    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(token))
    # a wrong password must not reveal that the account exists but is disabled
    r = anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": "Wr0ngPass9"}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# request-reactivation  — no enumeration
# ---------------------------------------------------------------------------

def test_request_reactivation_same_response_regardless_of_account_state(anon_client):
    token = _signup(anon_client).json()["access_token"]

    active = anon_client.post(
        "/api/auth/request-reactivation", json={"email": "alice@example.com"}
    )
    unknown = anon_client.post(
        "/api/auth/request-reactivation", json={"email": "nobody@example.com"}
    )
    assert active.status_code == unknown.status_code == 200
    assert active.json()["message"] == unknown.json()["message"]
    # an active account and an unknown one both get NO key
    assert active.json()["reactivation_key"] is None
    assert unknown.json()["reactivation_key"] is None

    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(token))
    disabled = anon_client.post(
        "/api/auth/request-reactivation", json={"email": "alice@example.com"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["message"] == active.json()["message"]
    assert disabled.json()["reactivation_key"] is not None


# ---------------------------------------------------------------------------
# reactivate
# ---------------------------------------------------------------------------

def _deactivate_and_get_key(anon_client, token):
    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(token))
    return anon_client.post(
        "/api/auth/request-reactivation", json={"email": "alice@example.com"}
    ).json()["reactivation_key"]


def test_reactivate_happy_path_returns_fresh_session(anon_client):
    old_token = _signup(anon_client).json()["access_token"]
    key = _deactivate_and_get_key(anon_client, old_token)

    r = anon_client.post(
        "/api/auth/reactivate", json={"email": "alice@example.com", "key": key}
    )
    assert r.status_code == 200
    new_token = r.json()["access_token"]

    # the fresh session works
    assert anon_client.get("/api/auth/me", headers=_bearer(new_token)).status_code == 200
    # the pre-deactivation token is STILL dead (token_version bumped again)
    assert anon_client.get("/api/auth/me", headers=_bearer(old_token)).status_code == 401
    # normal signin works again
    assert anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": _PW}
    ).status_code == 200


def test_reactivate_wrong_key_is_generic_400(anon_client):
    token = _signup(anon_client).json()["access_token"]
    _deactivate_and_get_key(anon_client, token)
    r = anon_client.post(
        "/api/auth/reactivate", json={"email": "alice@example.com", "key": "00000000"}
    )
    assert r.status_code == 400
    assert r.json()["message"] == "Invalid or expired reactivation key."


def test_reactivate_unknown_email_is_generic_400(anon_client):
    r = anon_client.post(
        "/api/auth/reactivate", json={"email": "nobody@example.com", "key": "12345678"}
    )
    assert r.status_code == 400


def test_reactivation_key_is_single_use(anon_client):
    token = _signup(anon_client).json()["access_token"]
    key = _deactivate_and_get_key(anon_client, token)
    body = {"email": "alice@example.com", "key": key}
    assert anon_client.post("/api/auth/reactivate", json=body).status_code == 200

    # deactivate again — the old key must not work for the new deactivation
    new_token = anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": _PW}
    ).json()["access_token"]
    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(new_token))
    assert anon_client.post("/api/auth/reactivate", json=body).status_code == 400


def test_reactivation_locks_after_max_attempts(anon_client):
    token = _signup(anon_client).json()["access_token"]
    key = _deactivate_and_get_key(anon_client, token)
    for _ in range(5):
        anon_client.post(
            "/api/auth/reactivate", json={"email": "alice@example.com", "key": "99999999"}
        )
    # even the correct key is now dead
    r = anon_client.post(
        "/api/auth/reactivate", json={"email": "alice@example.com", "key": key}
    )
    assert r.status_code == 400


def test_reactivation_expired_key_is_400(anon_client, db):
    token = _signup(anon_client).json()["access_token"]
    _deactivate_and_get_key(anon_client, token)
    key = anon_client.post(
        "/api/auth/request-reactivation", json={"email": "alice@example.com"}
    ).json()["reactivation_key"]

    row = db.query(ReactivationCode).order_by(ReactivationCode.created_at.desc()).first()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    r = anon_client.post(
        "/api/auth/reactivate", json={"email": "alice@example.com", "key": key}
    )
    assert r.status_code == 400


def test_reactivating_then_deactivating_leaves_no_live_stale_keys(anon_client, db):
    token = _signup(anon_client).json()["access_token"]
    key = _deactivate_and_get_key(anon_client, token)
    anon_client.post("/api/auth/reactivate", json={"email": "alice@example.com", "key": key})
    new_token = anon_client.post(
        "/api/auth/signin", json={"email": "alice@example.com", "password": _PW}
    ).json()["access_token"]

    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(new_token))
    # the used key stays used; the second deactivation minted nothing on its own,
    # and any earlier unused key was burned.
    unused = db.query(ReactivationCode).filter(ReactivationCode.used.is_(False)).count()
    assert unused == 0


# ---------------------------------------------------------------------------
# token_version — a password reset also ends existing sessions
# ---------------------------------------------------------------------------

def test_password_reset_invalidates_old_tokens(anon_client):
    old_token = _signup(anon_client).json()["access_token"]
    code = anon_client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    ).json()["reset_code"]
    anon_client.post("/api/auth/reset-password", json={
        "email": "alice@example.com", "code": code, "new_password": _PW2,
    })
    assert anon_client.get("/api/auth/me", headers=_bearer(old_token)).status_code == 401


# ---------------------------------------------------------------------------
# cross-user isolation
# ---------------------------------------------------------------------------

def test_one_users_deactivation_does_not_touch_another(anon_client):
    alice = _signup(anon_client).json()["access_token"]
    bob = _signup(anon_client, email="bob@example.com").json()["access_token"]

    anon_client.post("/api/auth/deactivate", json={"password": _PW}, headers=_bearer(alice))

    assert anon_client.get("/api/auth/me", headers=_bearer(bob)).status_code == 200
    assert anon_client.post(
        "/api/auth/signin", json={"email": "bob@example.com", "password": _PW}
    ).status_code == 200
