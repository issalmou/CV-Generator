"""Service: DocumentService — stable references + versioning for generated
CVs and cover letters (v2.9 Phase 2b — LOT 8).

A *document* keeps ONE stable ``reference`` (``CV_A8F42K`` / ``LETTER_91BC72``)
for its whole life. Every edit adds a new row: same ``reference``, ``version``
incremented, the previous row untouched. ``structured_source`` (JSON) is the
source of truth the edit agent works on — the PDF is only a render of it.

Every read/write here is **strictly owner-scoped**: a row is only ever looked
up together with ``user_id == <current user>`` — a reference belonging to
another user is a 404, never a cross-tenant read.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import _mint_reference
from models import GeneratedCV, GeneratedLetter

logger = logging.getLogger(__name__)

_MODELS = {"cv": (GeneratedCV, "CV"), "letter": (GeneratedLetter, "LETTER")}


def mint_reference(kind: str) -> str:
    _model, prefix = _MODELS[kind]
    return _mint_reference(prefix)


# ---------------------------------------------------------------------------
# generic helpers (kind = "cv" | "letter")
# ---------------------------------------------------------------------------

def _model(kind: str):
    return _MODELS[kind][0]


def next_version(db: Session, user_id: str, reference: str, kind: str) -> int:
    m = _model(kind)
    current = db.scalar(
        select(func.max(m.version)).where(m.reference == reference, m.user_id == user_id)
    )
    return int(current or 0) + 1


def versions(db: Session, user_id: str, reference: str, kind: str) -> list:
    """All versions of one document, oldest first. Owner-scoped."""
    m = _model(kind)
    return list(db.scalars(
        select(m).where(m.reference == reference, m.user_id == user_id).order_by(m.version.asc())
    ))


def latest(db: Session, user_id: str, reference: str, kind: str):
    m = _model(kind)
    return db.scalars(
        select(m).where(m.reference == reference, m.user_id == user_id)
        .order_by(m.version.desc()).limit(1)
    ).first()


def get_version(db: Session, user_id: str, reference: str, version: int, kind: str):
    m = _model(kind)
    row = db.scalars(
        select(m).where(m.reference == reference, m.user_id == user_id, m.version == version)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"{kind.upper()} {reference} v{version} not found.")
    return row


def resolve(db: Session, user_id: str, key: str, kind: str):
    """Accept a reference (-> latest version) OR a raw row id. Owner-scoped:
    404 when nothing matches, 403 when a row exists but belongs to another
    user (matches the rest of the API — ownership errors stay visible, the
    body is the same generic 'access denied')."""
    m = _model(kind)
    if _looks_like_reference(key):
        row = db.scalars(select(m).where(m.reference == key)
                         .order_by(m.version.desc()).limit(1)).first()
    else:
        row = db.get(m, key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"{kind.upper()} {key!r} not found.")
    if row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return row


def _looks_like_reference(key: str) -> bool:
    return key.upper().startswith(("CV_", "LETTER_"))


def structured(row) -> dict[str, Any]:
    """The version's ``structured_source`` as a dict (``{}`` when absent/corrupt)."""
    raw = getattr(row, "structured_source", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("[DocumentService] corrupt structured_source on %s v%s",
                       getattr(row, "reference", "?"), getattr(row, "version", "?"))
        return {}


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------

def record_cv(
    db: Session, *,
    user_id: str,
    cv_id: str,
    filename: str,
    storage_key: str,
    minio_bucket: str,
    language: str,
    ats_score: float | None,
    structured_source: dict | None,
    job_hash: str | None = None,
    reference: str | None = None,
    conversation_id: str | None = None,
) -> GeneratedCV:
    """Insert one CV version. ``reference=None`` -> a new document (v1).
    Otherwise -> the next version of that (owner-scoped) reference."""
    if reference:
        version = next_version(db, user_id, reference, "cv")
        parent = latest(db, user_id, reference, "cv")
        parent_id = parent.id if parent else None
    else:
        reference = mint_reference("cv")
        version, parent_id = 1, None

    row = GeneratedCV(
        id=cv_id, user_id=user_id, filename=filename, storage_key=storage_key,
        minio_bucket=minio_bucket, language=language, ats_score=ats_score,
        reference=reference, version=version, parent_id=parent_id,
        structured_source=json.dumps(structured_source) if structured_source is not None else None,
        job_hash=job_hash, conversation_id=conversation_id,
    )
    db.add(row)
    return row


def record_letter(
    db: Session, *,
    user_id: str,
    letter_id: str,
    filename: str,
    storage_key: str,
    minio_bucket: str,
    language: str,
    structured_source: dict | None,
    job_hash: str | None = None,
    reference: str | None = None,
    conversation_id: str | None = None,
) -> GeneratedLetter:
    if reference:
        version = next_version(db, user_id, reference, "letter")
        parent = latest(db, user_id, reference, "letter")
        parent_id = parent.id if parent else None
    else:
        reference = mint_reference("letter")
        version, parent_id = 1, None

    row = GeneratedLetter(
        id=letter_id, user_id=user_id, filename=filename, storage_key=storage_key,
        minio_bucket=minio_bucket, language=language,
        reference=reference, version=version, parent_id=parent_id,
        structured_source=json.dumps(structured_source) if structured_source is not None else None,
        job_hash=job_hash, conversation_id=conversation_id,
    )
    db.add(row)
    return row
