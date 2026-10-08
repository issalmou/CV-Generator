"""``GeneratedLetter`` — metadata for one cover-letter PDF stored in MinIO."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedLetter(Base):
    __tablename__ = "generated_letters"
    __table_args__ = (
        UniqueConstraint("reference", "version", name="uq_generated_letters_reference_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    minio_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    # --- versioning (LOT 8) ---
    reference: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: JSON {"body": str, "company": {...}, "candidate": {...}} — source of truth
    structured_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
