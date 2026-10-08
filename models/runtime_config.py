"""``RuntimeConfig`` — small key/JSON store for admin-changeable settings.

Used for the LLM configuration a superadmin can change at runtime
(``PATCH /api/admin/llm``). **Never** stores an API key — keys stay
environment secrets. Loaded on startup and re-applied onto the process
``settings`` object after each change.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
