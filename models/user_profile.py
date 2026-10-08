"""``UserProfile`` — one persistent, structured job-search profile per user.

1:1 with ``users`` (``user_id`` is the primary key). Strictly owner-scoped —
a profile is only ever read or written by its owner (or a superadmin's
read-only stats aggregates, which never expose the fields). Feeds the
auto-apply matching engine and is merged with what the preference extractor
pulls from a conversation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )

    # list[str] fields
    target_titles: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    employment_types: Mapped[list] = mapped_column(JSON, default=list)  # job / internship / contract / ...
    sectors: Mapped[list] = mapped_column(JSON, default=list)
    excluded_keywords: Mapped[list] = mapped_column(JSON, default=list)
    excluded_companies: Mapped[list] = mapped_column(JSON, default=list)
    preferred_companies: Mapped[list] = mapped_column(JSON, default=list)

    # scalars
    remote_preference: Mapped[str | None] = mapped_column(String(16), nullable=True)  # onsite/hybrid/remote/any
    experience_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # free-form extras the schema does not model explicitly
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
