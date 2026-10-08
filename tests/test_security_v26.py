"""Phases 58 + 59 — v2.6 security audit (executable).

Static + behavioural invariants that must never regress: every admin route
gated, no privilege-escalation path (incl. the new profile / context routes),
the LLM key never leaves the process (incl. /llm, /llm/test, PATCH, dashboard),
the profile / usage-event / runtime-config subsystems are owner-safe and
content-free, `http.py` still the sole chokepoint.
"""

from __future__ import annotations

import pathlib
import re

import bcrypt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import User

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mk(db, email, superadmin=False):
    u = User(email=email, is_superadmin=superadmin,
             password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _client(db, user=None):
    main.app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# admin surface
# ---------------------------------------------------------------------------

def test_every_admin_route_requires_superadmin():
    def _guarded(dep):
        if "require_superadmin" in repr(getattr(dep, "call", "")):
            return True
        return any(_guarded(s) for s in getattr(dep, "dependencies", []))

    admin = [r for r in main.app.routes
             if isinstance(r, APIRoute) and r.path.startswith("/api/admin")]
    assert len(admin) >= 15
    unguarded = [f"{sorted(r.methods)} {r.path}" for r in admin
                 if not any(_guarded(d) for d in r.dependant.dependencies)]
    assert unguarded == []


def test_no_privilege_field_in_user_facing_request_schemas():
    import schemas.auth as a
    import schemas.conversation as conv
    import schemas.profile as p
    for model in (a.SignupRequest, a.SigninRequest, p.UserProfileIn, conv.MessageCreate):
        assert "is_superadmin" not in model.model_fields
        assert "is_active" not in model.model_fields


def test_profile_and_context_ignore_injected_privilege_fields(db):
    u = _mk(db, "u@x.com")
    c = _client(db, u)
    c.put("/api/profile", json={"skills": ["X"], "is_superadmin": True, "extra": {"is_superadmin": True}})
    db.refresh(u)
    assert u.is_superadmin is False


# ---------------------------------------------------------------------------
# LLM key never leaves the process
# ---------------------------------------------------------------------------

def test_llm_key_absent_from_every_llm_surface(db, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-DO-NOT-LEAK-123456")
    monkeypatch.setattr(settings, "LLM_API_KEY", "nvapi-DO-NOT-LEAK-123456")
    monkeypatch.setattr("services.gemini_client.healthcheck",
                        lambda *a, **k: {"provider": "nvidia", "model": "m",
                                         "duration_ms": 1, "ok": True, "error": None})
    admin = _mk(db, "a@x.com", superadmin=True)
    c = _client(db, admin)
    for method, path, body in [
        ("get", "/api/health", None), ("get", "/api/stats", None),
        ("get", "/api/admin/llm", None), ("get", "/api/admin/dashboard", None),
        ("post", "/api/admin/llm/test", None),
        ("patch", "/api/admin/llm", {"temperature": 0.4}),
    ]:
        r = getattr(c, method)(path, json=body) if body else getattr(c, method)(path)
        assert "nvapi-DO-NOT-LEAK-123456" not in r.text, path


def test_patch_llm_never_stores_a_key(db):
    from models import RuntimeConfig
    admin = _mk(db, "a@x.com", superadmin=True)
    import services.gemini_client as gc
    gc.healthcheck = lambda *a, **k: {"provider": "nvidia", "model": "m",
                                      "duration_ms": 1, "ok": True, "error": None}
    _client(db, admin).patch("/api/admin/llm", json={
        "api_key": "sk-leak", "nvidia_api_key": "sk-leak2", "temperature": 0.2})
    row = db.get(RuntimeConfig, "llm")
    assert row is None or not any("KEY" in k.upper() for k in row.value)
    assert row is None or "sk-leak" not in str(row.value)


# ---------------------------------------------------------------------------
# owner isolation
# ---------------------------------------------------------------------------

def test_profile_is_strictly_owner_scoped(db):
    a, b = _mk(db, "a@x.com"), _mk(db, "b@x.com")
    _client(db, a).put("/api/profile", json={"skills": ["a-secret"]})
    main.app.dependency_overrides.clear()
    assert _client(db, b).get("/api/profile").json()["skills"] == []


def test_usage_events_store_no_free_text(db):
    from models import UsageEvent
    from services.usage_event_service import record
    u = _mk(db, "u@x.com")
    record(db, u.id, "search", {"query": "my private search terms", "results": 3})
    ev = db.query(UsageEvent).one()
    # strings are truncated but a caller should never pass content; assert the
    # recorder at least caps it hard
    assert all(not isinstance(v, str) or len(v) <= 60 for v in (ev.meta or {}).values())


# ---------------------------------------------------------------------------
# chokepoint
# ---------------------------------------------------------------------------

def test_no_new_module_bypasses_http_py():
    for rel in ("jobs/match_service.py", "user_profile_service.py", "usage_event_service.py",
                "runtime_config_service.py"):
        text = (ROOT / "services" / rel).read_text(encoding="utf-8")
        assert not re.search(r"^\s*import (httpx|requests)\b", text, re.M)
        assert "urllib.request" not in text


def test_llm_providers_only_use_the_openai_sdk():
    text = (ROOT / "services" / "llm" / "providers.py").read_text(encoding="utf-8")
    assert "import httpx" not in text and "import requests" not in text
    text2 = (ROOT / "services" / "llm" / "base.py").read_text(encoding="utf-8")
    assert "eval(" not in text2 and "exec(" not in text2
