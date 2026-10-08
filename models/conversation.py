"""``Conversation`` and ``Message`` — persistent memory for the contextualised
assistant (v2.8).

A conversation belongs to exactly one user; a message belongs to exactly one
conversation. ``Message.user_id`` is denormalised from the parent
conversation at insert time purely for query speed and defence-in-depth — the
service layer never trusts it alone for authorization, it always re-checks
the parent ``Conversation.user_id`` first.

SQL is the only source of truth here. Any Redis caching of "recent messages"
(``services/conversation_service.py``) is a read-through optimisation on top
of these tables, never a replacement for them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# the closed vocabulary for Message.role
MESSAGE_ROLES = frozenset({"user", "assistant"})


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    # short, user-editable label; auto-derived from the first message when unset.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Phase 6 — a structured action the agent PROPOSED and is waiting for the
    # user to confirm (currently only ``{"kind": "apply", "job_ids": [...],
    # "proposed_at": "<iso>"}``). Frozen at proposal time so the confirmation
    # turn is 100% deterministic: a recognised "yes" on the very next turn
    # executes exactly this set, nothing the LLM re-derives. Cleared on execute,
    # on any non-confirmation message, and ignored once stale.
    pending_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Phase 6 finalisation — the ordered result list of the last search/listing
    # turn, plus how much of it has already been shown to the user:
    # {"ordered_job_ids": [...], "offset": int, "page_size": 5}. The backend
    # slices this deterministically on a "show me more" turn — it never
    # re-ranks and never re-runs JobSearchService just to paginate.
    job_browse_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, index=True
    )

    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )


class Message(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True, nullable=False
    )
    # denormalised from the parent conversation — see module docstring.
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)   # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    # LOT 9/10 — when the edit agent produced/edited a document in this turn,
    # the assistant message links to it (e.g. "CV_A8F42K"). Nullable.
    document_reference: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)

    __table_args__ = (
        Index("ix_conv_messages_conv_created", "conversation_id", "created_at"),
    )
