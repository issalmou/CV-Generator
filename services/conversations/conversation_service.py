"""
Service: ConversationService — persistent conversational memory (v2.8).

Every read/write is **strictly owner-scoped**: a conversation is only ever
looked up by ``(id, user_id == current_user.id)``, never by id alone. SQL
(``conversations`` / ``conversation_messages``) is the single source of
truth; Redis (through the existing ``services/cache_service.py`` — best
effort, same as everywhere else) only ever caches the most recent
``CONVERSATION_HISTORY_LIMIT`` messages of one conversation, to spare a
repeated query while a conversation is being read. A cache miss, or a cache
backend that is down, always falls back to the same SQL query — nothing
about the conversation's behaviour depends on the cache being warm.

The agent itself is **not a new LLM provider** — it is one more
``call_gemini(prompt, request_type="conversation_agent")`` call, exactly like
every other consumer module. The v2.7 single ``OpenAICompatibleProvider`` is
untouched.

Ordering guarantee (the mission's explicit requirement): the user's message
is persisted *before* the LLM is called, and the assistant's reply is
persisted *after* — so a message is never lost if generation fails, and the
conversation table always reflects exactly what happened, in order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from config import settings
from models import Conversation, Message, User
from services import profiling
from services.cache_service import cache
from services.id_utils import require_uuid_or_404

logger = logging.getLogger(__name__)

REQUEST_TYPE = "conversation_agent"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SendResult:
    """One completed conversation turn."""
    user_message: "Message"
    assistant_message: "Message"
    document: dict | None = None                       # EditAgent new version
    recommended_actions: list[dict] = field(default_factory=list)
    # Phase 7 — intelligent, contextual, any-topic recommendations (additive).
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    jobs_found: list[dict[str, Any]] = field(default_factory=list)      # SelectedJobOut dumps
    applications: list[dict[str, Any]] = field(default_factory=list)     # ApplyOutcome dumps
    pending_confirmation: dict[str, Any] | None = None


def _history_cache_key(conversation_id: str) -> str:
    return cache.key("conversation", "history", conversation_id)


def _invalidate_history(conversation_id: str) -> None:
    try:
        cache.delete(_history_cache_key(conversation_id))
    except Exception:  # noqa: BLE001 — invalidation must never raise
        pass


class ConversationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # CRUD — conversations
    # ------------------------------------------------------------------

    def create(self, user: User, *, title: str | None = None) -> Conversation:
        conv = Conversation(user_id=user.id, title=(title or "").strip()[:200] or None)
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        return conv

    def list(self, user: User, *, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        total = self.db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user.id)
        ) or 0
        rows = list(self.db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit).offset(offset)
        ))
        if not rows:
            return [], total
        counts = dict(self.db.execute(
            select(Message.conversation_id, func.count())
            .where(Message.conversation_id.in_([c.id for c in rows]))
            .group_by(Message.conversation_id)
        ).all())
        return [self._to_out(c, counts.get(c.id, 0)) for c in rows], total

    def get_owned(self, user: User, conversation_id: str) -> Conversation:
        """The only way any route touches a conversation — 404 if it does not
        exist, 403 if it exists but belongs to someone else (never leaked as
        a 404, so ownership errors are visible in tests/logs, but the body is
        the same generic 'access denied' either way)."""
        require_uuid_or_404(conversation_id, detail="Conversation not found.")
        conv = self.db.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Conversation not found.")
        if conv.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Access denied.")
        return conv

    def get_out(self, user: User, conversation_id: str) -> dict:
        conv = self.get_owned(user, conversation_id)
        count = self.db.scalar(
            select(func.count()).select_from(Message)
            .where(Message.conversation_id == conv.id)
        ) or 0
        return self._to_out(conv, count)

    def delete(self, user: User, conversation_id: str) -> None:
        conv = self.get_owned(user, conversation_id)
        self.db.execute(delete(Message).where(Message.conversation_id == conv.id))
        self.db.delete(conv)
        self.db.commit()
        _invalidate_history(conv.id)

    # ------------------------------------------------------------------
    # Messages — read
    # ------------------------------------------------------------------

    def list_messages(
        self, user: User, conversation_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        conv = self.get_owned(user, conversation_id)
        total = self.db.scalar(
            select(func.count()).select_from(Message)
            .where(Message.conversation_id == conv.id)
        ) or 0
        rows = list(self.db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc())
            .limit(limit).offset(offset)
        ))
        return rows, total

    # ------------------------------------------------------------------
    # Messages — write (the contextualised agent)
    # ------------------------------------------------------------------

    async def send_message(self, user: User, conversation_id: str, content: str) -> SendResult:
        """Persist the user's message, produce the assistant's reply, persist it.

        Routing:
        - message names a ``CV_…`` / ``LETTER_…`` reference → the EDIT AGENT
          (deterministic document edit, may create a new version);
        - otherwise → the JOB-AWARE conversation agent (chat / job search / an
          apply *proposal* / a confirmation of a frozen apply proposal).

        The user's message is committed BEFORE any LLM call — nothing the user
        typed is lost if generation fails."""
        with profiling.profile_request("chat", request_id=conversation_id[:12]):
            conv = self.get_owned(user, conversation_id)

            with profiling.span("user_message_save"):
                user_msg = self._append(conv, "user", content)

            from services.conversations.agent_service import EditAgent, find_reference
            reference = find_reference(content)
            out = SendResult(user_message=user_msg, assistant_message=user_msg)  # asst filled below

            if reference:
                with profiling.span("edit_agent"):
                    result = EditAgent(self.db).handle(user, conv.id, reference, content)
                text = result.reply
                out.document = result.document
                out.recommended_actions = list(result.recommended_actions)
                doc_ref = (result.document or {}).get("reference") if result.applied else None
            else:
                from services.conversations.job_agent_service import JobConversationAgent
                with profiling.span("history_load"):
                    history = self._recent_history(conv.id)
                with profiling.span("job_agent"):
                    jr = await JobConversationAgent(self.db).handle(user, conv, content, history)
                text = jr.reply
                out.recommended_actions = list(jr.recommended_actions)
                out.recommendations = list(jr.recommendations)
                out.jobs_found = list(jr.jobs_found)
                out.applications = list(jr.applications)
                out.pending_confirmation = jr.pending_confirmation
                doc_ref = None

            with profiling.span("assistant_message_save"):
                assistant_msg = self._append(conv, "assistant",
                                             text.strip() or "(empty response)",
                                             document_reference=doc_ref)
            out.assistant_message = assistant_msg
            return out

    def _append(self, conv: Conversation, role: str, content: str,
                *, document_reference: str | None = None) -> Message:
        msg = Message(conversation_id=conv.id, user_id=conv.user_id, role=role, content=content,
                      document_reference=document_reference)
        self.db.add(msg)
        conv.updated_at = _now()
        self.db.commit()
        self.db.refresh(msg)
        _invalidate_history(conv.id)
        return msg

    def _recent_history(self, conversation_id: str) -> list[dict]:
        """The last ``CONVERSATION_HISTORY_LIMIT`` messages, oldest first —
        exactly what goes to the LLM. Redis-cached (best-effort); SQL is
        always the fallback and the ground truth."""
        limit = max(1, settings.CONVERSATION_HISTORY_LIMIT)
        key = _history_cache_key(conversation_id)
        cached = cache.get(key)
        if isinstance(cached, list) and cached:
            return cached[-limit:]

        rows = list(self.db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ))
        ordered = [{"role": m.role, "content": m.content} for m in reversed(rows)]
        cache.set(key, ordered, ttl=settings.CONVERSATION_HISTORY_CACHE_TTL)
        return ordered

    # ------------------------------------------------------------------

    @staticmethod
    def _to_out(conv: Conversation, message_count: int) -> dict:
        return {
            "id": conv.id,
            "title": conv.title,
            "message_count": message_count,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }
