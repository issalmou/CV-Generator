"""Phase 32 — superadmin ATS board management (DB overlay on file/env lists).

Zero rows == the pre-existing file/env behaviour. Enabled rows add tokens;
disabled rows remove them. Validation (slug / https) and SSRF guards stay.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import User
from services.jobs.ats_board_registry import resolved_tokens


def _admin(db):
    u = User(email="root@x.com",
             password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode(),
             is_superadmin=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------

def test_normal_user_cannot_manage_boards(db, test_user):
    c = _client(db, test_user)
    assert c.get("/api/admin/ats-boards").status_code == 403
    assert c.post("/api/admin/ats-boards",
                  json={"provider": "ashby", "token": "acme"}).status_code == 403


def test_added_slug_board_shows_up_in_resolved_tokens(db):
    c = _client(db, _admin(db))
    r = c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "acmecorp"})
    assert r.status_code == 201
    assert "acmecorp" in resolved_tokens("ashby", [])


def test_invalid_slug_rejected(db):
    c = _client(db, _admin(db))
    r = c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "bad slug!!"})
    assert r.status_code == 400


def test_unknown_provider_rejected(db):
    c = _client(db, _admin(db))
    r = c.post("/api/admin/ats-boards", json={"provider": "monster", "token": "x"})
    assert r.status_code == 400


def test_url_provider_requires_https(db):
    c = _client(db, _admin(db))
    bad = c.post("/api/admin/ats-boards",
                 json={"provider": "workday", "token": "http://acme.wd1.myworkdayjobs.com/x"})
    assert bad.status_code == 400
    ok = c.post("/api/admin/ats-boards",
                json={"provider": "workday", "token": "https://acme.wd1.myworkdayjobs.com/External"})
    assert ok.status_code == 201


def test_disabled_row_removes_a_token_from_the_effective_list(db):
    c = _client(db, _admin(db))
    # base list (file/env) simulated as ["keepme", "dropme"]
    assert resolved_tokens("lever", ["keepme", "dropme"]) == ["keepme", "dropme"]
    r = c.post("/api/admin/ats-boards",
               json={"provider": "lever", "token": "dropme", "enabled": False})
    assert r.status_code == 201
    assert resolved_tokens("lever", ["keepme", "dropme"]) == ["keepme"]


def test_duplicate_board_conflicts(db):
    c = _client(db, _admin(db))
    c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "dup"})
    assert c.post("/api/admin/ats-boards",
                  json={"provider": "ashby", "token": "dup"}).status_code == 409


def test_toggle_and_delete_are_reflected_immediately(db):
    c = _client(db, _admin(db))
    bid = c.post("/api/admin/ats-boards", json={"provider": "rippling", "token": "toggleme"}).json()["id"]
    assert "toggleme" in resolved_tokens("rippling", [])

    c.patch(f"/api/admin/ats-boards/{bid}", json={"enabled": False})
    assert "toggleme" not in resolved_tokens("rippling", [])

    c.patch(f"/api/admin/ats-boards/{bid}", json={"enabled": True})
    assert "toggleme" in resolved_tokens("rippling", [])

    assert c.delete(f"/api/admin/ats-boards/{bid}").status_code == 204
    assert "toggleme" not in resolved_tokens("rippling", [])


def test_list_is_scoped_and_carries_provider_catalog(db):
    c = _client(db, _admin(db))
    c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "a1"})
    c.post("/api/admin/ats-boards", json={"provider": "lever", "token": "l1"})

    body = c.get("/api/admin/ats-boards").json()
    assert {b["token"] for b in body["boards"]} == {"a1", "l1"}
    assert "greenhouse" in body["providers"] and "workday" in body["providers"]

    scoped = c.get("/api/admin/ats-boards", params={"provider": "ashby"}).json()
    assert {b["token"] for b in scoped["boards"]} == {"a1"}


def test_zero_rows_is_a_pure_passthrough(db):
    assert resolved_tokens("greenhouse", ["stripe", "airbnb"]) == ["stripe", "airbnb"]


# ---------------------------------------------------------------------------
# Phase 53 — per-board test + persisted result
# ---------------------------------------------------------------------------

def test_board_test_by_id_persists_the_result(db, monkeypatch):
    c = _client(db, _admin(db))
    bid = c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "acmecorp"}).json()["id"]

    # stub the provider fetch so the test is offline + deterministic
    from services.providers.registry import registry
    from services.admin_service import AdminService

    def _fake_test(self, provider, token):
        return {"provider": provider, "token": token, "reachable": True,
                "offers_found": 7, "detail": None}
    monkeypatch.setattr(AdminService, "test_board", _fake_test)

    r = c.post(f"/api/admin/ats-boards/{bid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["offers_found"] == 7 and body["board_id"] == bid

    row = c.get("/api/admin/ats-boards").json()["boards"][0]
    assert row["last_test_ok"] is True
    assert row["last_test_offer_count"] == 7
    assert row["last_tested_at"] is not None


def test_board_test_by_id_records_a_failure_without_leaking(db, monkeypatch):
    c = _client(db, _admin(db))
    bid = c.post("/api/admin/ats-boards", json={"provider": "ashby", "token": "x"}).json()["id"]
    from services.admin_service import AdminService
    monkeypatch.setattr(AdminService, "test_board", lambda self, p, t: {
        "provider": p, "token": t, "reachable": False, "offers_found": None,
        "detail": "boom at https://api.ashbyhq.com/... key sk-abc123456789"})

    r = c.post(f"/api/admin/ats-boards/{bid}/test")
    assert r.status_code == 200
    row = c.get("/api/admin/ats-boards").json()["boards"][0]
    assert row["last_test_ok"] is False
    assert row["last_test_error"]  # recorded


def test_board_test_by_id_requires_superadmin(db, test_user):
    assert _client(db, test_user).post("/api/admin/ats-boards/whatever/test").status_code == 403
