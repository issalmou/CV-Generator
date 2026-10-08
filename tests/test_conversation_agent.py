"""v2.8 — the contextualised agent behind `POST /api/conversations/{id}/messages`.

Covers the mission's explicit requirements:
- the user's message is persisted BEFORE generation, the assistant's reply AFTER;
- the recent history is sent to the LLM in chronological order;
- history is bounded by a configurable limit (never "all of it");
- no new LLM provider is involved — this is one more `call_gemini` call;
- Redis (via cache_service) only ever caches the recent-history read, SQL is
  the source of truth and the always-correct fallback.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import Conversation, Message, User
from services.cache_service import cache
from services.conversations.conversation_service import ConversationService, _history_cache_key


def _now():
    return datetime.now(timezone.utc)


def _user(db, email="u@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _conversation(db, user):
    conv = Conversation(user_id=user.id)
    db.add(conv); db.commit(); db.refresh(conv)
    return conv


def _seed_messages(db, conv, n, *, prefix="old"):
    """n alternating user/assistant messages, strictly increasing created_at."""
    base = _now() - timedelta(hours=1)
    for i in range(n):
        db.add(Message(
            conversation_id=conv.id, user_id=conv.user_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"{prefix}-{i}",
            created_at=base + timedelta(seconds=i),
        ))
    db.commit()


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ordering: user saved before generation, assistant saved after
# ---------------------------------------------------------------------------

def test_user_message_saved_before_assistant_reply(db, mock_llm):
    u = _user(db)
    conv = _conversation(db, u)

    body = _client(db, u).post(f"/api/conversations/{conv.id}/messages",
                               json={"content": "Help me improve my CV"}).json()
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["content"] == "Help me improve my CV"
    assert body["assistant_message"]["role"] == "assistant"
    assert body["assistant_message"]["content"]

    rows = list(db.query(Message).filter(Message.conversation_id == conv.id)
               .order_by(Message.created_at.asc()))
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].created_at <= rows[1].created_at


# ---------------------------------------------------------------------------
# history order + limit sent to the LLM
# ---------------------------------------------------------------------------

def test_history_sent_in_chronological_order(db, mock_llm):
    u = _user(db)
    conv = _conversation(db, u)
    _seed_messages(db, conv, 4)   # old-0..old-3, user/assistant/user/assistant

    _client(db, u).post(f"/api/conversations/{conv.id}/messages",
                        json={"content": "new question"})

    request_type, prompt = mock_llm.prompts[-1]
    assert request_type == "conversation_agent"
    # every prior message appears, in the same relative order, ending with the
    # brand-new user message last.
    positions = [prompt.index(f"old-{i}") for i in range(4)]
    assert positions == sorted(positions)
    assert prompt.index("new question") > positions[-1]


def test_history_respects_configurable_limit(db, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "CONVERSATION_HISTORY_LIMIT", 3)
    u = _user(db)
    conv = _conversation(db, u)
    _seed_messages(db, conv, 10)   # old-0 .. old-9

    _client(db, u).post(f"/api/conversations/{conv.id}/messages",
                        json={"content": "final question"})

    _, prompt = mock_llm.prompts[-1]
    # limit=3 total messages sent to the LLM: the 2 most recent old ones +
    # the brand-new user message — never "all of it".
    assert "old-9" in prompt and "old-8" in prompt
    assert "old-7" not in prompt and "old-0" not in prompt
    assert "final question" in prompt


def test_no_new_llm_provider_is_used(db, mock_llm):
    """The agent is one more call_gemini() call — not a second LLM client."""
    u = _user(db)
    conv = _conversation(db, u)
    _client(db, u).post(f"/api/conversations/{conv.id}/messages", json={"content": "hi"})
    assert mock_llm.calls[-1] == "conversation_agent"

    import services.llm.providers as p
    from services.llm.base import BaseLLMProvider
    from services.llm.providers import OpenAICompatibleProvider
    impls = [v for v in vars(p).values()
             if isinstance(v, type) and issubclass(v, BaseLLMProvider) and v is not BaseLLMProvider]
    assert impls == [OpenAICompatibleProvider]


# ---------------------------------------------------------------------------
# failure handling — the user's message is never lost
# ---------------------------------------------------------------------------

def test_llm_failure_keeps_user_message_but_saves_no_assistant_reply(db, mock_llm):
    def _boom(prompt, *, request_type="generic", use_cache=True):
        raise RuntimeError("No LLM model available.")
    mock_llm.set(_boom)

    u = _user(db)
    conv = _conversation(db, u)
    r = _client(db, u).post(f"/api/conversations/{conv.id}/messages",
                            json={"content": "will this fail?"})
    assert r.status_code == 429

    rows = list(db.query(Message).filter(Message.conversation_id == conv.id))
    assert len(rows) == 1
    assert rows[0].role == "user"
    assert rows[0].content == "will this fail?"


def test_llm_not_configured_persists_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    u = _user(db)
    conv = _conversation(db, u)
    r = _client(db, u).post(f"/api/conversations/{conv.id}/messages",
                            json={"content": "anyone there?"})
    assert r.status_code == 503
    assert db.query(Message).filter(Message.conversation_id == conv.id).count() == 0


def test_cannot_send_a_message_to_someone_elses_conversation(db, mock_llm):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    conv = _conversation(db, a)
    r = _client(db, b).post(f"/api/conversations/{conv.id}/messages", json={"content": "hijack"})
    assert r.status_code == 403
    assert db.query(Message).filter(Message.conversation_id == conv.id).count() == 0


# ---------------------------------------------------------------------------
# Redis (cache_service) history cache — best-effort, SQL always the fallback
# ---------------------------------------------------------------------------

def test_recent_history_is_cached_then_invalidated_on_new_message(db):
    u = _user(db)
    conv = _conversation(db, u)
    _seed_messages(db, conv, 2)

    svc = ConversationService(db)
    key = _history_cache_key(conv.id)
    assert cache.get(key) is None            # MISS before any read

    history1 = svc._recent_history(conv.id)
    assert cache.get(key) == history1        # now cached (HIT on direct read)

    svc._append(conv, "user", "brand new")
    assert cache.get(key) is None            # invalidated by the write

    history2 = svc._recent_history(conv.id)
    assert history2[-1] == {"role": "user", "content": "brand new"}


def test_conversation_never_leaks_the_llm_key(db, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "SECRET-LLM-KEY-DO-NOT-LEAK-777")
    u = _user(db)
    conv = _conversation(db, u)
    r = _client(db, u).post(f"/api/conversations/{conv.id}/messages", json={"content": "hi"})
    assert "SECRET-LLM-KEY-DO-NOT-LEAK-777" not in r.text

    rows = db.query(Message).filter(Message.conversation_id == conv.id).all()
    assert all("SECRET-LLM-KEY-DO-NOT-LEAK-777" not in m.content for m in rows)


def test_history_cache_backend_down_falls_back_to_sql(db, monkeypatch):
    """Redis (or whatever backend) raising on every call must never break
    the conversation — the recent history still comes back correctly."""
    u = _user(db)
    conv = _conversation(db, u)
    _seed_messages(db, conv, 3)

    class _BrokenBackend:
        def get(self, key):
            raise ConnectionError("redis down")
        def set(self, key, value, ttl=None):
            raise ConnectionError("redis down")
        def delete(self, key):
            raise ConnectionError("redis down")
        def clear(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "_backend", _BrokenBackend())

    svc = ConversationService(db)
    history = svc._recent_history(conv.id)   # must not raise
    assert len(history) == 3
