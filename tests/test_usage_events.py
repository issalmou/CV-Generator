"""Phase 54 — privacy-safe usage-event log + event-based analytics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import JobOffer, UsageEvent, User
from services import usage_event_service


def _now():
    return datetime.now(timezone.utc)


def _user(db, email="e@x.com", superadmin=False):
    u = User(email=email, is_superadmin=superadmin,
             password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
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


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

def test_record_writes_a_row(db):
    u = _user(db)
    usage_event_service.record(db, u.id, "search", {"results": 12})
    ev = db.query(UsageEvent).one()
    assert ev.kind == "search" and ev.meta == {"results": 12}


def test_record_rejects_unknown_kind_and_missing_user(db):
    u = _user(db)
    usage_event_service.record(db, u.id, "not_a_kind")
    usage_event_service.record(db, None, "search")
    assert db.query(UsageEvent).count() == 0


def test_record_strips_non_primitive_meta(db):
    u = _user(db)
    usage_event_service.record(db, u.id, "search",
                               {"ok": 1, "blob": {"nested": "x"}, "text": "a" * 200})
    ev = db.query(UsageEvent).one()
    assert "blob" not in ev.meta and len(ev.meta["text"]) <= 60


def test_disabled_flag_stops_recording(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_EVENTS_ENABLED", False)
    usage_event_service.record(db, _user(db).id, "login")
    assert db.query(UsageEvent).count() == 0


def test_prune_deletes_old_rows(db):
    u = _user(db)
    old = UsageEvent(user_id=u.id, kind="login",
                     created_at=_now() - timedelta(days=999))
    new = UsageEvent(user_id=u.id, kind="login")
    db.add_all([old, new]); db.commit()
    usage_event_service.prune(db)
    kinds = db.query(UsageEvent).all()
    assert len(kinds) == 1


# ---------------------------------------------------------------------------
# routes emit events
# ---------------------------------------------------------------------------

def test_search_and_job_view_emit_events(db, mock_llm, swap_providers):
    """The agent's search turn emits a `search` event; a `GET /api/jobs/{id}`
    on a selected job emits a `job_view` event."""
    import json as _json

    from _jobs_helpers import FakeJobProvider, make_offer
    from models import Conversation, SavedJob

    u = _user(db)
    swap_providers([FakeJobProvider("ashby", [make_offer("ashby", "1", title="Engineer")])])
    c = _client(db, u)
    conv = Conversation(user_id=u.id); db.add(conv); db.commit(); db.refresh(conv)

    from tests.conftest import _default_llm_router

    def _r(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return _json.dumps({"reply": "ok", "intent": "search_jobs",
                                "search_patch": {"query": "engineer"},
                                "apply": None, "question": None})
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)
    mock_llm.set(_r)

    c.post(f"/api/conversations/{conv.id}/messages", json={"content": "cherche engineer"})
    offer = db.query(JobOffer).first()
    assert db.query(SavedJob).filter_by(user_id=u.id).count() >= 1
    c.get(f"/api/jobs/{offer.id}")

    kinds = {e.kind for e in db.query(UsageEvent).filter_by(user_id=u.id)}
    assert "search" in kinds and "job_view" in kinds


# ---------------------------------------------------------------------------
# analytics use events
# ---------------------------------------------------------------------------

def test_usage_overview_switches_to_event_source(db):
    admin = _user(db, "a@x.com", superadmin=True)
    other = _user(db, "b@x.com")
    db.add(UsageEvent(user_id=other.id, kind="search"))
    db.add(UsageEvent(user_id=other.id, kind="search"))
    db.commit()

    body = _client(db, admin).get("/api/admin/stats/usage").json()
    assert body["activity_source"] == "events"
    assert body["dau"] == 1        # only `other` has events today


def test_per_user_stats_carry_event_counts(db):
    admin = _user(db, "a@x.com", superadmin=True)
    bob = _user(db, "bob@x.com")
    db.add_all([
        UsageEvent(user_id=bob.id, kind="login"),
        UsageEvent(user_id=bob.id, kind="search"),
        UsageEvent(user_id=bob.id, kind="search"),
        UsageEvent(user_id=bob.id, kind="job_view"),
    ])
    db.commit()

    rows = {r["email"]: r for r in _client(db, admin).get("/api/admin/stats/users").json()["users"]}
    assert rows["bob@x.com"]["session_count"] == 1
    assert rows["bob@x.com"]["search_count"] == 2
    assert rows["bob@x.com"]["job_view_count"] == 1
