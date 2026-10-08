"""``GeneratedCV`` — metadata for one CV PDF version stored in MinIO.

Versioning (v2.9 Phase 2b — LOT 8): a *document* has a stable ``reference``
(``CV_A8F42K``) that never changes; each edit adds a new row with an
incremented ``version`` and keeps the old ones. ``structured_source`` (the
assembled ``cv_data`` JSON) is the source of truth the edit agent works on —
the PDF is a render of it, never edited directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedCV(Base):
    __tablename__ = "generated_cvs"
    __table_args__ = (
        UniqueConstraint("reference", "version", name="uq_generated_cvs_reference_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Object key inside the bucket — the PDF bytes never touch local disk.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    minio_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    # --- versioning (LOT 8) — all nullable/defaulted so _ensure_additive_columns migrates ---
    #: stable id for the whole document, unchanged across versions (e.g. "CV_A8F42K")
    reference: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    #: 1, 2, 3 … within one ``reference``
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    #: the previous version's row id (None for v1)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: assembled cv_data JSON — the SOURCE OF TRUTH the edit agent works on
    structured_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: digest of the job description this version targeted (None = generic)
    job_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: the conversation that produced / last edited this version (nullable)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
