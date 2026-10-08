"""``UsageEvent`` — a lightweight, privacy-safe activity record.

One row per meaningful action (login / search / job_view / job_saved /
application_prepared / document_created / document_downloaded). It stores
**counters and ids only** — never the content of a search, CV or message.
Powers the superadmin usage analytics (sessions, searches, views per user;
DAU/WAU/MAU/YAU from distinct active users). Best-effort: a write failure is
swallowed and never blocks a request. Retention: rows older than
``USAGE_EVENT_RETENTION_DAYS`` are pruned on startup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# the closed vocabulary — anything else is rejected before insert
EVENT_KINDS = frozenset({
    "login",
    "search",
    "job_view",
    "job_saved",
    "application_prepared",
    "document_created",
    "document_downloaded",
})


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # tiny non-sensitive context only (e.g. {"provider_count": 12, "results": 30})
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    __table_args__ = (
        Index("ix_usage_events_user_kind_time", "user_id", "kind", "created_at"),
        Index("ix_usage_events_kind_time", "kind", "created_at"),
        # covering index for "distinct active users in a window"
        Index("ix_usage_events_time_user", "created_at", "user_id"),
    )
