"""
API router: job detail + applications + selection — ``/api/jobs/*``.

Every route requires a valid ``Authorization: Bearer <jwt>``.

Phase 6 — there is **no manual job-search API**. The conversation agent runs
``JobSearchService`` internally (``services/conversations/job_agent_service.py``)
and the retained jobs surface via ``GET /api/dashboard/jobs``. This router is the
narrow public façade the dashboard needs:

- ``GET  /api/jobs/{id}``     — one retained job's full detail (scoped to the
  caller's selection / applications); revalidates a stale row.
- ``POST /api/jobs/{id}/apply`` — the **Apply** button. Records intent, returns
  the external URL, never auto-submits. Delegates to ``JobApplicationService``.
- ``GET /api/jobs/applications`` / ``…/{id}`` — application list + status.
- ``POST``/``DELETE /api/jobs/{id}/save`` + ``GET /api/jobs/saved`` — manage the
  selection (pin / unpin a job). All owner-scoped.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import JobApplication, JobOffer, SavedJob, User
from schemas.applications import (
    ApplicationOut, ApplicationResponse, ApplyRequest,
)
from schemas.jobs import ApplicationMethodOut, JobDetailResponse, JobOfferOut
from services.id_utils import require_uuid_or_404
from services.jobs.application_service import JobApplicationService
from services.jobs.freshness import classify, revalidate
from services.providers.base import ApplicationMethod
from services.providers.registry import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


def _record_event(db, user_id, kind, meta=None):
    from services.usage_event_service import record
    record(db, user_id, kind, meta)


# ---------------------------------------------------------------------------
# Applications + saved jobs (static paths — declared before /{job_id})
# ---------------------------------------------------------------------------

@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select as _select

    records = JobApplicationService(db).list_applications(current_user)
    offer_ids = {r.job_offer_id for r in records}
    offers = (
        {o.id: o for o in db.scalars(_select(JobOffer).where(JobOffer.id.in_(offer_ids)))}
        if offer_ids else {}
    )
    return [_application_out(records=record, offer=offers.get(record.job_offer_id))
            for record in records]


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = JobApplicationService(db).get_application(current_user, application_id)
    return _application_out(records=record, offer=db.get(JobOffer, record.job_offer_id))


@router.get("/saved", response_model=list[JobOfferOut])
def list_saved(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return [_offer_out(o) for o in JobApplicationService(db).list_saved(current_user)]


# ---------------------------------------------------------------------------
# Single offer
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    offer = _offer_in_scope_or_404(db, current_user.id, job_id)

    freshness = classify(offer)
    if freshness.value == "stale":
        provider = registry.get(offer.source)
        if provider is not None:
            try:
                freshness = revalidate(db, offer, provider)
            except Exception as exc:  # noqa: BLE001 — a detail read must never 500
                logger.warning("[jobs] revalidate %s failed: %s", offer.id, exc)

    _record_event(db, current_user.id, "job_view", {"source": offer.source})
    return JobDetailResponse(
        job=_offer_out(offer),
        application=_application_method_out(offer),
        freshness=freshness,
    )


@router.post("/{job_id}/apply", response_model=ApplicationResponse)
def apply_to_job(
    job_id: str,
    payload: ApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    resp = JobApplicationService(db).apply(current_user, job_id, payload)
    _record_event(db, current_user.id, "application_prepared",
                  {"status": resp.application_status.value})
    return resp


@router.post("/{job_id}/save", status_code=status.HTTP_204_NO_CONTENT)
def save_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    JobApplicationService(db).save_job(current_user, job_id)
    _record_event(db, current_user.id, "job_saved", None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{job_id}/save", status_code=status.HTTP_204_NO_CONTENT)
def unsave_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    JobApplicationService(db).unsave_job(current_user, job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _offer_in_scope_or_404(db: Session, user_id: str, job_id: str) -> JobOffer:
    """Phase 6 — the frontend only ever sees jobs the agent retained for THIS
    user (or that the user applied to). Any other offer id is a 404, exactly as
    if it did not exist — no cross-user browsing of the offer table."""
    require_uuid_or_404(job_id, detail="Job offer not found.")
    offer = db.get(JobOffer, job_id)
    if offer is not None:
        in_selection = db.scalar(
            select(SavedJob.id).where(
                SavedJob.user_id == user_id, SavedJob.job_offer_id == job_id
            ).limit(1)
        )
        applied = db.scalar(
            select(JobApplication.id).where(
                JobApplication.user_id == user_id, JobApplication.job_offer_id == job_id
            ).limit(1)
        )
        if in_selection or applied:
            return offer
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job offer not found.")


def _application_method_out(offer: JobOffer) -> ApplicationMethodOut:
    provider = registry.get(offer.source)
    method = (
        provider.application_method(offer) if provider is not None
        else ApplicationMethod("external_url", offer.source_url)
    )
    return ApplicationMethodOut(
        method=method.kind,
        can_be_assisted=False,   # automated submission is never available in v1
        apply_url=method.url or offer.source_url,
    )


def _offer_out(offer: JobOffer) -> JobOfferOut:
    return JobOfferOut(
        id=offer.id,
        source=offer.source,
        source_job_id=offer.source_job_id,
        source_url=offer.source_url,
        title=offer.title,
        company=offer.company,
        company_url=offer.company_url,
        location=offer.location,
        city=offer.city,
        country=offer.country,
        description=offer.description,
        employment_type=offer.employment_type,
        job_type=offer.job_type,
        remote_type=offer.remote_type,
        salary_min=offer.salary_min,
        salary_max=offer.salary_max,
        salary_currency=offer.salary_currency,
        salary_period=offer.salary_period,
        experience_level=offer.experience_level,
        skills=offer.skills or [],
        language=offer.language,
        posted_at=offer.posted_at,
        expires_at=offer.expires_at,
        scraped_at=offer.scraped_at,
        last_verified_at=offer.last_verified_at,
        internship_duration_months=offer.internship_duration_months,
        internship_start_date=offer.internship_start_date,
        match_score=getattr(offer, "match_score", 0.0),
        freshness=classify(offer),
        is_active=offer.is_active,
        also_seen_on=offer.also_seen_on or [],
    )


def _application_out(*, records, offer) -> ApplicationOut:
    record = records
    return ApplicationOut(
        id=record.id,
        job_offer_id=record.job_offer_id,
        job_title=offer.title if offer else None,
        company=offer.company if offer else None,
        cv_id=record.cv_id,
        letter_id=record.letter_id,
        status=record.status,
        application_url=record.application_url,
        context=record.context,
        answers=record.answers,
        source=getattr(record, "source", None),
        match_score=getattr(record, "match_score", None),
        match_reasons=getattr(record, "match_reasons", None) or [],
        missing_skills=getattr(record, "missing_skills", None) or [],
        attempt_count=getattr(record, "attempt_count", 1) or 1,
        error_detail=getattr(record, "error_detail", None),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
