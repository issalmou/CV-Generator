"""``JobApplication`` — a user's *prepared* application to a stored ``JobOffer``.

Records the selected CV / cover letter, the match analysis, and the outcome.
**Never** represents an automated submission on an external site — ``status``
is one of ``prepared`` / ``manual_required`` / ``requires_user_action`` /
``unavailable`` / ``failed`` / ``duplicate``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    job_offer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_offers.id"), index=True, nullable=False
    )
    cv_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generated_cvs.id"), nullable=True
    )
    letter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generated_letters.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    application_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- traceability (Phase 27) — all additive / nullable ---
    # provider the offer came from, copied at apply time so history survives an
    # offer row being pruned.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # slug(company)|slug(title)|slug(city) — catches "same real job, different
    # JobOffer row" so a user cannot apply to the same posting twice by mistake.
    normalized_job_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # deterministic, LLM-free offer<->request fit at apply time (0..1).
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # human-readable "why" strings + wanted skills absent from the posting.
    match_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        Index("ix_job_applications_user_normkey", "user_id", "normalized_job_key"),
        Index("ix_job_applications_user_offer", "user_id", "job_offer_id"),
    )
