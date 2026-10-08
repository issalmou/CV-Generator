"""``AtsBoard`` — a superadmin-managed ATS board entry.

A **DB overlay on top of** the bundled ``data/ats_boards/*.txt`` files and the
``<PROVIDER>_BOARDS`` env vars — never a replacement. The effective token list
for a provider is ``file/env tokens ∪ {enabled DB rows} \ {disabled DB rows}``
(see ``services/ats_board_registry``). With zero rows the system behaves exactly
as before this table existed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import expression
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AtsBoard(Base):
    __tablename__ = "ats_boards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # provider name as in the registry: greenhouse / lever / ashby / workday / ...
    provider: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    # slug, full URL or JSON blob — whatever that provider's token list expects.
    token: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=expression.true(), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # --- last live test (Phase 53) — all additive / nullable ---
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_offer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("provider", "token", name="uq_ats_boards_provider_token"),
    )
