"""v2.8 — persistent conversations (`/api/conversations/*`).

Strictly owner-scoped: a conversation (and its messages) belongs to exactly
one user; another user gets 403/404, never a silent leak. CRUD + pagination
only here — the contextualised-agent behaviour (history order, limit, LLM
mocking) lives in test_conversation_agent.py.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from database import get_db
from dependencies.auth import get_current_user
from models import Conversation, Message, User


def _user(db, email="u@x.com"):
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


def _seed_conversation(db, user, n_messages=0):
    conv = Conversation(user_id=user.id, title="My chat")
    db.add(conv); db.commit(); db.refresh(conv)
    for i in range(n_messages):
        db.add(Message(conversation_id=conv.id, user_id=user.id,
                       role="user" if i % 2 == 0 else "assistant", content=f"msg {i}"))
    db.commit()
    return conv


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_create_conversation(db):
    u = _user(db)
    body = _client(db, u).post("/api/conversations", json={"title": "Job search help"}).json()
    assert body["title"] == "Job search help"
    assert body["message_count"] == 0
    assert "id" in body and "created_at" in body and "updated_at" in body


def test_create_conversation_without_title(db):
    u = _user(db)
    r = _client(db, u).post("/api/conversations", json={})
    assert r.status_code == 201
    assert r.json()["title"] is None


def test_list_conversations_ordered_by_recent_first(db):
    u = _user(db)
    c1 = _seed_conversation(db, u)
    c2 = _seed_conversation(db, u)
    # bump c1's updated_at above c2's
    c1.updated_at = c2.updated_at.replace(year=c2.updated_at.year + 1)
    db.commit()

    body = _client(db, u).get("/api/conversations").json()
    assert body["total"] == 2
    assert body["conversations"][0]["id"] == c1.id


def test_get_conversation_includes_message_count(db):
    u = _user(db)
    conv = _seed_conversation(db, u, n_messages=3)
    body = _client(db, u).get(f"/api/conversations/{conv.id}").json()
    assert body["message_count"] == 3


def test_get_missing_conversation_404(db):
    u = _user(db)
    r = _client(db, u).get("/api/conversations/does-not-exist")
    assert r.status_code == 404


def test_delete_conversation_removes_messages(db):
    u = _user(db)
    conv = _seed_conversation(db, u, n_messages=4)
    r = _client(db, u).delete(f"/api/conversations/{conv.id}")
    assert r.status_code == 204
    assert db.get(Conversation, conv.id) is None
    assert db.query(Message).filter(Message.conversation_id == conv.id).count() == 0


def test_list_messages_paginated_oldest_first(db):
    u = _user(db)
    conv = _seed_conversation(db, u, n_messages=5)
    body = _client(db, u).get(f"/api/conversations/{conv.id}/messages?limit=2&offset=0").json()
    assert body["total"] == 5
    assert len(body["messages"]) == 2
    assert body["messages"][0]["content"] == "msg 0"
    assert body["messages"][1]["content"] == "msg 1"


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------

def test_user_b_cannot_read_user_a_conversation(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    conv = _seed_conversation(db, a, n_messages=2)

    assert _client(db, b).get(f"/api/conversations/{conv.id}").status_code == 403
    assert _client(db, b).get(f"/api/conversations/{conv.id}/messages").status_code == 403


def test_user_b_cannot_delete_user_a_conversation(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    conv = _seed_conversation(db, a)

    assert _client(db, b).delete(f"/api/conversations/{conv.id}").status_code == 403
    assert db.get(Conversation, conv.id) is not None   # untouched


def test_user_b_list_never_shows_user_a_conversations(db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    _seed_conversation(db, a)
    _seed_conversation(db, a)

    body = _client(db, b).get("/api/conversations").json()
    assert body["total"] == 0
    assert body["conversations"] == []


def test_unauthenticated_401(db):
    main.app.dependency_overrides[get_db] = lambda: db
    assert TestClient(main.app).get("/api/conversations").status_code == 401
