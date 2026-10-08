"""``SavedJob`` — a user's *selection* of a ``JobOffer``. Save is idempotent.

Phase 6: this is the single "jobs the user is considering" table. Rows are
written both by the agent (``origin="agent"`` — from a conversation search) and
by the user (``origin="manual"`` — ``POST /api/jobs/{id}/save``). The dashboard
(`GET /api/dashboard/jobs`) joins this to the most recent ``JobApplication`` to
show the per-job Apply status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    job_offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_offers.id"), nullable=False
    )
    # who put this job in the selection — "agent" (conversation search) or
    # "manual" (POST /api/jobs/{id}/save). server_default so the additive ALTER
    # works on an existing table.
    origin: Mapped[str] = mapped_column(
        String(16), default="agent", server_default="agent", nullable=False
    )
    # deterministic offer<->profile fit at selection time (0..1), when known.
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # the conversation whose search produced this selection (agent origin).
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("user_id", "job_offer_id", name="uq_saved_jobs_user_offer"),
    )
