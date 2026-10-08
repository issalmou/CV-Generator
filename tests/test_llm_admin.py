"""Phases 51 + 52 — runtime LLM config (`PATCH /api/admin/llm`) + `/llm/test`.

The API key is **never** accepted, stored or returned. A bad new config is
health-checked and rolled back.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import RuntimeConfig, User
from services import gemini_client


def _admin(db):
    u = User(email="a@x.com", is_superadmin=True,
             password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    snap = {k: getattr(settings, k) for k in (
        "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_TEMPERATURE",
        "LLM_MAX_OUTPUT_TOKENS", "LLM_REQUEST_TIMEOUT", "LLM_MAX_CALLS_PER_MINUTE",
        "LLM_MODELS_FALLBACK")}
    yield
    for k, v in snap.items():
        setattr(settings, k, v)
    gemini_client.reset_client()
    main.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _ok_health(monkeypatch):
    # a green healthcheck unless a test overrides it
    monkeypatch.setattr(gemini_client, "healthcheck",
                        lambda *a, **k: {"provider": "nvidia", "model": "m",
                                         "duration_ms": 3, "ok": True, "error": None})


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_patch_and_test_require_superadmin(db, test_user):
    c = _client(db, test_user)
    assert c.patch("/api/admin/llm", json={"temperature": 0.5}).status_code == 403
    assert c.post("/api/admin/llm/test").status_code == 403


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------

def test_patch_changes_config_and_persists(db):
    c = _client(db, _admin(db))
    r = c.patch("/api/admin/llm", json={
        "provider": "openai", "model": "gpt-4o-mini", "temperature": 0.1,
        "max_output_tokens": 2048,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai" and body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0.1
    assert "api_key" not in body

    row = db.get(RuntimeConfig, "llm")
    assert row and row.value["LLM_PROVIDER"] == "openai"
    assert settings.LLM_TEMPERATURE == 0.1


def test_patch_rejects_api_key_and_unknown_fields(db):
    c = _client(db, _admin(db))
    # extra fields are ignored by the schema; an explicit api_key is simply
    # not a field -> ignored, never stored
    r = c.patch("/api/admin/llm", json={"api_key": "sk-leak", "temperature": 0.2})
    assert r.status_code == 200
    row = db.get(RuntimeConfig, "llm")
    assert "sk-leak" not in str(row.value)
    assert not any("KEY" in k for k in row.value)


def test_patch_rejects_unknown_provider(db):
    c = _client(db, _admin(db))
    r = c.patch("/api/admin/llm", json={"provider": "skynet"})
    assert r.status_code == 400


def test_patch_rolls_back_when_healthcheck_fails(db, monkeypatch):
    monkeypatch.setattr(gemini_client, "healthcheck",
                        lambda *a, **k: {"provider": "nvidia", "model": None,
                                         "duration_ms": 1, "ok": False,
                                         "error": "connection refused"})
    before_temp = settings.LLM_TEMPERATURE
    c = _client(db, _admin(db))
    r = c.patch("/api/admin/llm", json={"temperature": 1.9, "model": "broken/model"})
    assert r.status_code == 400
    assert "rolled back" in r.json()["detail"]
    assert settings.LLM_TEMPERATURE == before_temp        # settings restored
    assert db.get(RuntimeConfig, "llm") is None           # nothing persisted


def test_overrides_reapplied_on_startup(db):
    db.add(RuntimeConfig(key="llm", value={"LLM_TEMPERATURE": 0.42, "LLM_MODEL": "persisted/m"}))
    db.commit()
    from services.runtime_config_service import apply_llm_overrides
    apply_llm_overrides(db)
    assert settings.LLM_TEMPERATURE == 0.42
    assert gemini_client._get_model()[0] == "persisted/m"


# ---------------------------------------------------------------------------
# /llm/test
# ---------------------------------------------------------------------------

def test_llm_test_endpoint_returns_health(db, monkeypatch):
    monkeypatch.setattr(gemini_client, "healthcheck",
                        lambda *a, **k: {"provider": "nvidia", "model": "m1",
                                         "duration_ms": 42, "ok": True, "error": None})
    r = _client(db, _admin(db)).post("/api/admin/llm/test")
    assert r.status_code == 200
    assert r.json() == {"provider": "nvidia", "model": "m1",
                        "duration_ms": 42, "ok": True, "error": None}


def test_llm_test_never_leaks_the_key(db, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-super-secret-key")

    def _boom(*a, **k):
        from services.llm.base import ProviderHealth, _sanitize
        return {"provider": "nvidia", "model": None, "duration_ms": 5, "ok": False,
                "error": _sanitize("401 for nvapi-super-secret-key")}

    monkeypatch.setattr(gemini_client, "healthcheck", _boom)
    r = _client(db, _admin(db)).post("/api/admin/llm/test")
    assert "nvapi-super-secret-key" not in r.text
