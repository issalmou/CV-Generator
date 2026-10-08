"""Phase 40 — executable security-audit checks for the v2.5 surface.

Static invariants that must never regress: admin routes gated, no privilege
escalation path, the LLM key never leaves the process, `http.py` still the sole
outbound-HTTP chokepoint, scraped content stays untrusted.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.routing import APIRoute

import main

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# admin surface
# ---------------------------------------------------------------------------

def test_every_admin_route_requires_superadmin():
    def _guarded(dep) -> bool:
        if "require_superadmin" in repr(getattr(dep, "call", "")):
            return True
        return any(_guarded(s) for s in getattr(dep, "dependencies", []))

    admin = [r for r in main.app.routes
             if isinstance(r, APIRoute) and r.path.startswith("/api/admin")]
    assert admin, "no admin routes registered?"
    unguarded = [r.path for r in admin
                 if not any(_guarded(d) for d in r.dependant.dependencies)]
    assert unguarded == []


def test_is_superadmin_not_in_any_user_facing_request_schema():
    import schemas.auth as auth_schemas

    for name in ("SignupRequest", "SigninRequest", "ResetPasswordRequest",
                 "ForgotPasswordRequest"):
        model = getattr(auth_schemas, name)
        assert "is_superadmin" not in model.model_fields


def test_signup_ignores_injected_privilege_fields(anon_client, db):
    from sqlalchemy import select
    from models import User

    r = anon_client.post("/api/auth/signup", json={
        "email": "x@y.com", "password": "Passw0rd!",
        "is_superadmin": True, "is_active": True, "id": "forced-id",
    })
    assert r.status_code == 201
    u = db.scalar(select(User).where(User.email == "x@y.com"))
    assert u.is_superadmin is False and u.id != "forced-id"


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def test_no_real_looking_api_key_committed():
    pat = re.compile(r"nvapi-[A-Za-z0-9_-]{24,}|sk-[A-Za-z0-9]{32,}")
    for path in ROOT.rglob("*"):
        if path.suffix not in {".py", ".md", ".txt", ".tex", ".example", ".json", ".yml", ".yaml"}:
            continue
        if "__pycache__" in str(path) or path.name.startswith("test_cv.db"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pat.finditer(text):
            assert "xxxx" in m.group(0).lower(), f"{path}: {m.group(0)[:12]}..."


def test_llm_key_absent_from_public_config_and_health(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "LLM_API_KEY", "SECRET-KEY-DO-NOT-LEAK-42")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "SECRET-KEY-DO-NOT-LEAK-42")
    for path in ("/api/health", "/api/stats"):
        assert "SECRET-KEY-DO-NOT-LEAK-42" not in client.get(path).text


# ---------------------------------------------------------------------------
# chokepoint + untrusted content
# ---------------------------------------------------------------------------

def test_no_provider_imports_a_raw_http_client():
    prov_dir = ROOT / "services" / "providers"
    for path in prov_dir.glob("*_provider.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*import (httpx|requests)\b", text, re.M), path.name
        assert not re.search(r"^\s*from (httpx|requests) import", text, re.M), path.name


def test_ats_board_registry_never_lets_db_error_break_resolution(monkeypatch):
    """A DB failure in the board overlay must fall back to the file/env list."""
    import database
    import services.jobs.ats_board_registry as reg

    monkeypatch.setattr(reg.cache, "get", lambda *_: None)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(database, "SessionLocal", _boom)
    assert reg.resolved_tokens("greenhouse", ["stripe", "airbnb"]) == ["stripe", "airbnb"]


def test_admin_service_module_has_no_dangerous_calls():
    for rel in ("admin_service.py", "stats_service.py", "jobs/ats_board_registry.py"):
        text = (ROOT / "services" / rel).read_text(encoding="utf-8")
        assert not re.search(r"\b(eval|exec)\s*\(", text)
        assert "subprocess" not in text and "os.system" not in text
