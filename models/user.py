"""``User`` — an authenticated account."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # bcrypt hash — the plaintext password is never stored or logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: bumped every time the account is deactivated OR reactivated. Every access
    #: token carries a ``tv`` claim; a token whose ``tv`` != this is rejected
    #: (dependencies.auth) — so deactivation invalidates every existing session
    #: and reactivation issues a fresh one that supersedes any lingering token.
    token_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Superadmin — grants access to /api/admin/*. NEVER settable through any
    # request schema; only via the SUPERADMIN_EMAILS bootstrap or another
    # superadmin's explicit PATCH. server_default so the additive-migration
    # ALTER works on an existing table.
    is_superadmin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=expression.false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    # Last time this account made an authenticated request (throttled write —
    # see dependencies.auth). Powers the DAU/WAU/MAU stats.
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<User {self.email}>"
