"""
API router: CV / cover-letter generation and management — ``/api/*``.

Every route requires a valid ``Authorization: Bearer <jwt>``.

Generation routes no longer stream a PDF: the bytes are rendered in
memory, pushed to MinIO, recorded in the database against the current
user, and the client gets JSON with a short-lived presigned download URL.

Ownership: a stored CV / letter is only ever visible to the user who
created it — a mismatched ``user_id`` is a 403, never a 404 leak-through.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from cv_models import GenerateCVRequest, GenerateLetterRequest
from database import get_db
from dependencies.auth import get_current_user
from models import GeneratedCV, GeneratedLetter, User
from schemas.cv import (
    CVListItem,
    DocumentVersionItem,
    DownloadUrlResponse,
    GenerateCVJsonResponse,
    GenerateLetterJsonResponse,
    LetterListItem,
)
from services import profiling
from services.documents import document_service
from services.cache_service import cache
from services.generation_service import (
    llm_error_response,
    require_llm,
    require_minio,
    run_cv_pipeline_async,
    run_letter_pipeline_async,
)
from services.minio_service import minio_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["CV"])


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def _generate_cv(payload: GenerateCVRequest, current_user: User, db: Session):
    cv_id = str(uuid.uuid4())
    logger.info("[API] POST /api/generate-cv | cv_id=%s | user_id=%s", cv_id, current_user.id)

    guard = require_llm() or require_minio()
    if guard is not None:
        return guard

    _prof = profiling.profile_request("cv_generation", request_id=cv_id)
    _prof.__enter__()
    try:
        try:
            result = await run_cv_pipeline_async(payload, cv_id)
            pdf_bytes, ats_score = result.pdf_bytes, result.ats_score
        except Exception as exc:  # noqa: BLE001
            mapped = llm_error_response(exc, trace_id=cv_id)
            if mapped is not None:
                return mapped
            logger.exception("[API] Unexpected error | cv_id=%s | %s", cv_id, exc)
            return JSONResponse(
                status_code=500, content={"status": "error", "message": "CV generation failed."}
            )

        key = minio_service.cv_key(cv_id, payload.language)
        filename = f"cv_{cv_id}_{payload.language}.pdf"
        with profiling.span("minio_upload"):
            minio_service.upload(key, pdf_bytes)

        row = document_service.record_cv(
            db,
            user_id=current_user.id,
            cv_id=cv_id,
            filename=filename,
            storage_key=key,
            minio_bucket=minio_service.bucket,
            language=payload.language,
            ats_score=ats_score,
            structured_source=result.cv_data,
            job_hash=cache.digest(payload.job_description) if payload.job_description else None,
            reference=payload.reference,
        )
        with profiling.span("db_operations"):
            db.commit()
            db.refresh(row)
        _usage(db, current_user.id, "document_created", {"kind": "cv"})
        _invalidate_dashboard(current_user.id)
    finally:
        _prof.__exit__(None, None, None)

    recommended_actions = _augment_cv_recommendations(
        db, current_user.id, result, payload.job_description
    )

    return GenerateCVJsonResponse(
        generated_cv_id=cv_id,
        reference=row.reference,
        version=row.version,
        ats_score=ats_score,
        language=payload.language,
        filename=filename,
        download_url=minio_service.presigned_get_url(key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
        additional_skills=result.additional_skills,
        ats_optimization=result.ats_status,
        layout=result.layout,
        skill_analysis=result.skill_analysis,
        recommended_actions=recommended_actions,
    )


@router.post("/generate-cv", response_model=GenerateCVJsonResponse)
async def generate_cv(
    payload: GenerateCVRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a CV PDF. Pass ``reference`` to save a new version of an
    existing document (LOT 8). This is the single CV-generation endpoint —
    the former ``/api/optimize-existing-cv`` alias was removed in Phase 2b
    (identical behaviour)."""
    return await _generate_cv(payload, current_user, db)


@router.post("/generate-letter", response_model=GenerateLetterJsonResponse)
async def generate_letter(
    payload: GenerateLetterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    letter_id = str(uuid.uuid4())
    logger.info("[API] POST /api/generate-letter | letter_id=%s | user_id=%s", letter_id, current_user.id)

    guard = require_llm() or require_minio()
    if guard is not None:
        return guard

    _prof = profiling.profile_request("letter", request_id=letter_id)
    _prof.__enter__()
    try:
        try:
            letter_result = await run_letter_pipeline_async(payload, letter_id)
            pdf_bytes = letter_result.pdf_bytes
        except Exception as exc:  # noqa: BLE001
            mapped = llm_error_response(exc, trace_id=letter_id)
            if mapped is not None:
                return mapped
            logger.exception("[API] Unexpected error | letter_id=%s | %s", letter_id, exc)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": "Cover letter generation failed."},
            )

        key = minio_service.letter_key(letter_id, payload.language)
        filename = f"cover_letter_{letter_id}_{payload.language}.pdf"
        with profiling.span("minio_upload"):
            minio_service.upload(key, pdf_bytes)

        row = document_service.record_letter(
            db,
            user_id=current_user.id,
            letter_id=letter_id,
            filename=filename,
            storage_key=key,
            minio_bucket=minio_service.bucket,
            language=payload.language,
            structured_source=letter_result.structured,
            job_hash=cache.digest(payload.job_description),
            reference=payload.reference,
        )
        with profiling.span("db_operations"):
            db.commit()
            db.refresh(row)
        _usage(db, current_user.id, "document_created", {"kind": "cover_letter"})
        _invalidate_dashboard(current_user.id)
    finally:
        _prof.__exit__(None, None, None)

    return GenerateLetterJsonResponse(
        generated_letter_id=letter_id,
        reference=row.reference,
        version=row.version,
        language=payload.language,
        filename=filename,
        download_url=minio_service.presigned_get_url(key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
    )


# ---------------------------------------------------------------------------
# Management — CVs
# ---------------------------------------------------------------------------


def _cv_item(r) -> CVListItem:
    return CVListItem(
        id=r.id, reference=r.reference, version=r.version, filename=r.filename,
        language=r.language, ats_score=r.ats_score, created_at=r.created_at,
    )


@router.get("/cvs", response_model=list[CVListItem])
def list_cvs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(GeneratedCV)
        .where(GeneratedCV.user_id == current_user.id)
        .order_by(GeneratedCV.created_at.desc())
    ).all()
    return [_cv_item(r) for r in rows]


@router.get("/cvs/{cv_id}/versions", response_model=list[DocumentVersionItem])
def list_cv_versions(cv_id: str, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Every version of a CV document, oldest first. ``cv_id`` may be the
    stable reference (``CV_A8F42K``) or any version's row id."""
    row = document_service.resolve(db, current_user.id, cv_id, "cv")
    rows = document_service.versions(db, current_user.id, row.reference, "cv")
    latest_v = max((r.version for r in rows), default=1)
    return [
        DocumentVersionItem(
            id=r.id, reference=r.reference, version=r.version, language=r.language,
            filename=r.filename, ats_score=r.ats_score, created_at=r.created_at,
            is_latest=(r.version == latest_v),
        )
        for r in rows
    ]


@router.get("/cvs/{cv_id}/versions/{version}/download", response_model=DownloadUrlResponse)
def download_cv_version(cv_id: str, version: int,
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    ref_row = document_service.resolve(db, current_user.id, cv_id, "cv")
    row = document_service.get_version(db, current_user.id, ref_row.reference, version, "cv")
    _usage(db, current_user.id, "document_downloaded", {"kind": "cv"})
    return DownloadUrlResponse(
        download_url=minio_service.presigned_get_url(row.storage_key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
    )


@router.get("/cvs/{cv_id}", response_model=CVListItem)
def get_cv(cv_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = document_service.resolve(db, current_user.id, cv_id, "cv")
    return _cv_item(row)


@router.get("/cvs/{cv_id}/download", response_model=DownloadUrlResponse)
def download_cv(cv_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = document_service.resolve(db, current_user.id, cv_id, "cv")
    _usage(db, current_user.id, "document_downloaded", {"kind": "cv"})
    return DownloadUrlResponse(
        download_url=minio_service.presigned_get_url(row.storage_key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
    )


@router.delete("/cvs/{cv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cv(cv_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a CV. A reference deletes the WHOLE document (every version); a
    raw row id deletes just that version."""
    anchor = document_service.resolve(db, current_user.id, cv_id, "cv")
    if cv_id.upper().startswith("CV_"):
        rows = document_service.versions(db, current_user.id, anchor.reference, "cv")
    else:
        rows = [anchor]
    for row in rows:
        try:
            minio_service.delete(row.storage_key)
        except Exception as exc:  # noqa: BLE001 - the DB row is what matters
            logger.warning("[API] MinIO delete failed for %s: %s", row.storage_key, exc)
        db.delete(row)
    db.commit()
    _invalidate_dashboard(current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Management — cover letters
# ---------------------------------------------------------------------------


def _letter_item(r) -> LetterListItem:
    return LetterListItem(id=r.id, reference=r.reference, version=r.version,
                          filename=r.filename, language=r.language, created_at=r.created_at)


@router.get("/letters", response_model=list[LetterListItem])
def list_letters(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(GeneratedLetter)
        .where(GeneratedLetter.user_id == current_user.id)
        .order_by(GeneratedLetter.created_at.desc())
    ).all()
    return [_letter_item(r) for r in rows]


@router.get("/letters/{letter_id}/versions", response_model=list[DocumentVersionItem])
def list_letter_versions(letter_id: str, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    row = document_service.resolve(db, current_user.id, letter_id, "letter")
    rows = document_service.versions(db, current_user.id, row.reference, "letter")
    latest_v = max((r.version for r in rows), default=1)
    return [
        DocumentVersionItem(
            id=r.id, reference=r.reference, version=r.version, language=r.language,
            filename=r.filename, created_at=r.created_at, is_latest=(r.version == latest_v),
        )
        for r in rows
    ]


@router.get("/letters/{letter_id}/versions/{version}/download", response_model=DownloadUrlResponse)
def download_letter_version(letter_id: str, version: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    ref_row = document_service.resolve(db, current_user.id, letter_id, "letter")
    row = document_service.get_version(db, current_user.id, ref_row.reference, version, "letter")
    _usage(db, current_user.id, "document_downloaded", {"kind": "cover_letter"})
    return DownloadUrlResponse(
        download_url=minio_service.presigned_get_url(row.storage_key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
    )


@router.get("/letters/{letter_id}", response_model=LetterListItem)
def get_letter(letter_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = document_service.resolve(db, current_user.id, letter_id, "letter")
    return _letter_item(row)


@router.get("/letters/{letter_id}/download", response_model=DownloadUrlResponse)
def download_letter(letter_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = document_service.resolve(db, current_user.id, letter_id, "letter")
    _usage(db, current_user.id, "document_downloaded", {"kind": "cover_letter"})
    return DownloadUrlResponse(
        download_url=minio_service.presigned_get_url(row.storage_key),
        expires_in=settings.MINIO_URL_EXPIRE_SECONDS,
    )


def _augment_cv_recommendations(db, user_id: str, result, job_description: str | None):
    """The pipeline computed the JD-only contextual recommendations
    (services/recommendations.py). Here — where we have the request's session —
    we add the one that needs a DB lookup: suggest a cover letter when this job
    has no letter yet. Never changes the pipeline's suggestions, only appends."""
    actions = list(result.recommended_actions)
    jd = (job_description or "").strip()
    if not jd:
        return actions
    job_hash = cache.digest(jd)
    has_letter = db.scalar(
        select(GeneratedLetter.id)
        .where(GeneratedLetter.user_id == user_id, GeneratedLetter.job_hash == job_hash)
        .limit(1)
    ) is not None
    if not has_letter and not any(a.get("type") == "generate_cover_letter" for a in actions):
        from services.recommendations import RecommendationContext, build as _build_reco
        actions += [a.model_dump() for a in _build_reco(RecommendationContext(
            has_job_description=True, has_matching_letter=False))]
    return actions


def _usage(db, user_id, kind, meta=None):
    try:
        from services.usage_event_service import record
        record(db, user_id, kind, meta)
    except Exception:  # noqa: BLE001
        pass


def _invalidate_dashboard(user_id: str) -> None:
    from services.user_dashboard_service import invalidate
    invalidate(user_id)


@router.delete("/letters/{letter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_letter(letter_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    anchor = document_service.resolve(db, current_user.id, letter_id, "letter")
    if letter_id.upper().startswith("LETTER_"):
        rows = document_service.versions(db, current_user.id, anchor.reference, "letter")
    else:
        rows = [anchor]
    for row in rows:
        try:
            minio_service.delete(row.storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[API] MinIO delete failed for %s: %s", row.storage_key, exc)
        db.delete(row)
    db.commit()
    _invalidate_dashboard(current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
