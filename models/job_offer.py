"""``JobOffer`` — a normalised job/internship listing aggregated from a provider.

This table is a **cache / dedup / freshness index**, never proof a job is
still live. Every read path recomputes freshness from the timestamps and
respects ``is_active``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobOffer(Base):
    __tablename__ = "job_offers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # --- provenance ---
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_job_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    also_seen_on: Mapped[list] = mapped_column(JSON, default=list)

    # --- listing content (description is PLAIN TEXT only, capped) ---
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_type: Mapped[str] = mapped_column(String(16), default="job", nullable=False)
    remote_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    internship_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    internship_start_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- freshness bookkeeping ---
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_verification_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    freshness: Mapped[str] = mapped_column(String(16), default="fresh", nullable=False)

    __table_args__ = (
        Index("ix_job_offers_source_sourcejobid", "source", "source_job_id"),
    )
