"""
Service: JobApplicationService

Prepares and records an application to a stored ``JobOffer``. It is
deliberately **separate from search** and it **never submits** anything:
applying to LinkedIn / Indeed / a company ATS requires authenticated,
anti-bot-protected flows that we will not automate. The service:

- verifies the offer exists and is still live (freshness),
- verifies the selected CV / cover letter belong to the caller (403 else),
- resolves the real external application URL via the offer's provider,
- persists a ``JobApplication`` capturing the user's context / answers,
- returns a status that is one of ``manual_required`` /
  ``requires_user_action`` / ``unavailable`` / ``failed`` — never ``submitted``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    GeneratedCV, GeneratedLetter, JobApplication, JobOffer, SavedJob, User,
)
from schemas.applications import ApplicationResponse, ApplicationStatus, ApplyRequest
from schemas.jobs import FreshnessStatus, JobSearchContext
from services.id_utils import require_uuid_or_404
from services.jobs.freshness import classify
from services.jobs.match_service import MatchResult, evaluate as _match_evaluate
from services.providers.base import ApplicationMethod
from services.providers.registry import registry
from services.user_dashboard_service import invalidate as _invalidate_dashboard
from services.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

_MAX_ANSWER_LEN = 4000
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str | None) -> str:
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-")


def normalized_job_key(offer: JobOffer) -> str:
    """``slug(company)|slug(title)|slug(city|country|location)`` — a stable
    identity for *the real posting*, so a user cannot apply to the same job
    twice even if it exists under two ``JobOffer`` rows."""
    where = offer.city or offer.country or offer.location
    return f"{_slug(offer.company)}|{_slug(offer.title)}|{_slug(where)}"


class JobApplicationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, user: User, job_id: str, req: ApplyRequest) -> ApplicationResponse:
        started = time.monotonic()
        require_uuid_or_404(job_id, detail="Job offer not found.")
        offer = self.db.get(JobOffer, job_id)
        if offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job offer not found.")

        self._check_ownership(user, req)

        job_key = normalized_job_key(offer)
        ctx = self._build_context(user, req)
        match = _match_evaluate(offer, ctx)

        # --- duplicate guard: never record the same posting twice for one user
        existing = self._existing_application(user, offer, job_key)
        if existing is not None:
            existing.attempt_count = (existing.attempt_count or 1) + 1
            existing.last_attempt_at = _now()
            self.db.commit()
            _invalidate_dashboard(user.id)
            return ApplicationResponse(
                application_status=ApplicationStatus.duplicate,
                job_id=offer.id, application_id=existing.id,
                application_url=existing.application_url,
                cv_id=existing.cv_id, letter_id=existing.letter_id,
                match_score=existing.match_score,
                match_reasons=existing.match_reasons or [],
                missing_skills=existing.missing_skills or [],
                is_duplicate=True,
                message="You already have an application on file for this posting.",
            )

        cv_id = req.cv_id or self._select_best_cv(user, offer)

        freshness = classify(offer)
        if not offer.is_active or freshness == FreshnessStatus.expired:
            record = self._persist(
                user, offer, req, ApplicationStatus.unavailable,
                application_url=offer.source_url, cv_id=cv_id, job_key=job_key,
                match=match, started=started,
                error_detail="listing is no longer active",
            )
            return self._response(record, ApplicationStatus.unavailable, offer.source_url, match,
                                  "This listing is no longer active. It cannot be applied to.")

        try:
            method = self._resolve_method(offer)
        except Exception as exc:  # noqa: BLE001 — never 500 on a provider quirk
            logger.warning("[applications] method resolution failed for %s: %s", offer.id, exc)
            method = ApplicationMethod("external_url", offer.source_url)
        apply_url = method.url or offer.source_url

        # --- optional cover-letter preparation (best-effort, never fatal)
        letter_id, letter_note = req.letter_id, ""
        if req.prepare and req.generate_letter and not letter_id:
            letter_id, letter_note = self._prepare_letter(user, offer, req)

        if method.kind != "external_url":
            app_status = ApplicationStatus.requires_user_action
            message = ("This source requires you to sign in on its own site to apply. "
                       "Open the URL to continue there.")
        elif req.prepare:
            app_status = ApplicationStatus.prepared
            message = ("Application prepared. Open the URL to submit it on the company's page."
                       + letter_note)
        else:
            app_status = ApplicationStatus.manual_required
            message = "Open the application URL to finish applying on the company's page."

        record = self._persist(
            user, offer, req, app_status, application_url=apply_url,
            cv_id=cv_id, letter_id=letter_id, job_key=job_key, match=match, started=started,
        )
        return self._response(record, app_status, apply_url, match, message)

    def _response(self, record, app_status, url, match: "MatchResult", message: str) -> ApplicationResponse:
        return ApplicationResponse(
            application_status=app_status,
            job_id=record.job_offer_id, application_id=record.id,
            application_url=url, cv_id=record.cv_id, letter_id=record.letter_id,
            match_score=match.score, match_reasons=match.reasons,
            missing_skills=match.missing_skills, message=message,
        )

    # ------------------------------------------------------------------

    def _build_context(self, user: User, req: ApplyRequest) -> JobSearchContext:
        """Merge the persistent profile with anything the caller typed."""
        profile = UserProfileService(self.db).get(user)
        ctx = UserProfileService.to_search_context(profile)
        text = (req.context or "").strip()
        if text:
            typed = JobSearchContext(query=ctx.query or text, keywords=[text] if ctx.query else [])
            ctx = ctx.merge(typed) if ctx.query else typed.merge(ctx)
        return ctx

    def _prepare_letter(self, user: User, offer: JobOffer, req: ApplyRequest) -> tuple[str | None, str]:
        """Generate + store a cover letter for this application. Returns
        ``(letter_id, note)`` — ``(None, reason)`` on any failure (never raises)."""
        if not req.cv_profile:
            return None, " (no cover letter — send `cv_profile` with `generate_letter` to auto-write one)"
        try:
            import uuid as _uuid

            from cv_models import GenerateLetterRequest
            from services.generation_service import require_minio, run_letter_pipeline
            from services.minio_service import minio_service

            if require_minio() is not None:
                return None, " (cover letter skipped — object storage not configured)"

            lang = (req.letter_language or offer.language or "en").lower()[:2]
            if lang not in ("fr", "en"):
                lang = "en"
            payload = GenerateLetterRequest.model_validate({
                "cv_profile": req.cv_profile,
                "job_description": (offer.description or offer.title or "")[:8000],
                "language": lang,
            })
            letter_id = str(_uuid.uuid4())
            letter_result = run_letter_pipeline(payload, letter_id)
            key = minio_service.letter_key(letter_id, lang)
            minio_service.upload(key, letter_result.pdf_bytes)
            from services.cache_service import cache
            from services.documents import document_service
            document_service.record_letter(
                self.db, user_id=user.id, letter_id=letter_id,
                filename=f"cover_letter_{letter_id}_{lang}.pdf", storage_key=key,
                minio_bucket=minio_service.bucket, language=lang,
                structured_source=letter_result.structured,
                # Audit fix: this was omitted, so every letter generated
                # through the apply flow got job_hash=NULL — silently
                # defeating recommendation_engine._contradicts_existing_state's
                # "standalone letter already exists" check for the most common
                # real-world letter path (the app.letter_id check still caught
                # the "attached to an application" case, which is why this had
                # no visible symptom — but a letter whose application was later
                # deleted, or generated ahead of one, was never protected).
                # Same digest formula as the job_description passed above so a
                # later recompute from the JobOffer matches exactly.
                job_hash=cache.digest(payload.job_description),
            )
            # NOT committed here — the letter row commits ATOMICALLY with the
            # JobApplication in _persist(). A crash before _persist() rolls the
            # letter back too, so an application is never recorded pointing at a
            # letter that isn't there (constraint: no partial creation).
            return letter_id, " A tailored cover letter was generated."
        except Exception as exc:  # noqa: BLE001 — letter prep must never fail an apply
            logger.warning("[applications] letter generation failed for %s: %s", offer.id, exc)
            self.db.rollback()   # drop any half-added letter row
            return None, " (cover letter could not be generated)"

    # ------------------------------------------------------------------
    # apply internals (Phase 27)
    # ------------------------------------------------------------------

    def _existing_application(
        self, user: User, offer: JobOffer, job_key: str
    ) -> JobApplication | None:
        """An earlier application by this user to the same posting — matched
        either on the exact offer row or on the normalized job identity."""
        return self.db.scalar(
            select(JobApplication)
            .where(
                JobApplication.user_id == user.id,
                (JobApplication.job_offer_id == offer.id)
                | (JobApplication.normalized_job_key == job_key),
            )
            .order_by(JobApplication.created_at.desc())
        )

    def _select_best_cv(self, user: User, offer: JobOffer) -> str | None:
        """Auto-pick the most relevant stored CV for this offer, using only
        data already on the ``GeneratedCV`` row: prefer a language match with
        the offer, then the higher ATS score, then the most recent."""
        cvs = list(self.db.scalars(
            select(GeneratedCV).where(GeneratedCV.user_id == user.id)
        ))
        if not cvs:
            return None
        offer_lang = (offer.language or "").lower()[:2]

        def key(cv: GeneratedCV):
            lang_match = 1 if offer_lang and (cv.language or "").lower()[:2] == offer_lang else 0
            return (lang_match, cv.ats_score or 0.0, cv.created_at or datetime.min)

        return max(cvs, key=key).id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_applications(self, user: User) -> list[JobApplication]:
        return list(
            self.db.scalars(
                select(JobApplication)
                .where(JobApplication.user_id == user.id)
                .order_by(JobApplication.created_at.desc())
            )
        )

    def get_application(self, user: User, application_id: str) -> JobApplication:
        require_uuid_or_404(application_id, detail="Application not found.")
        record = self.db.get(JobApplication, application_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
        if record.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        return record

    # ------------------------------------------------------------------
    # Saved jobs
    # ------------------------------------------------------------------

    def save_job(self, user: User, job_id: str) -> None:
        require_uuid_or_404(job_id, detail="Job offer not found.")
        if self.db.get(JobOffer, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job offer not found.")
        existing = self.db.scalar(
            select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_offer_id == job_id)
        )
        if existing is None:
            self.db.add(SavedJob(user_id=user.id, job_offer_id=job_id))
            self.db.commit()
            _invalidate_dashboard(user.id)

    def unsave_job(self, user: User, job_id: str) -> None:
        existing = self.db.scalar(
            select(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_offer_id == job_id)
        )
        if existing is not None:
            self.db.delete(existing)
            self.db.commit()
            _invalidate_dashboard(user.id)

    def list_saved(self, user: User) -> list[JobOffer]:
        saved = list(self.db.scalars(
            select(SavedJob).where(SavedJob.user_id == user.id).order_by(SavedJob.created_at.desc())
        ))
        if not saved:
            return []
        by_id = {
            o.id: o for o in self.db.scalars(
                select(JobOffer).where(JobOffer.id.in_({s.job_offer_id for s in saved}))
            )
        }
        return [by_id[s.job_offer_id] for s in saved if s.job_offer_id in by_id]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _check_ownership(self, user: User, req: ApplyRequest) -> None:
        if req.cv_id:
            cv = self.db.get(GeneratedCV, req.cv_id)
            if cv is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV not found.")
            if cv.user_id != user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        if req.letter_id:
            letter = self.db.get(GeneratedLetter, req.letter_id)
            if letter is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover letter not found.")
            if letter.user_id != user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    @staticmethod
    def _resolve_method(offer: JobOffer) -> ApplicationMethod:
        provider = registry.get(offer.source)
        if provider is None:
            return ApplicationMethod("external_url", offer.source_url)
        return provider.application_method(offer)

    def _persist(
        self,
        user: User,
        offer: JobOffer,
        req: ApplyRequest,
        app_status: ApplicationStatus,
        *,
        application_url: str | None,
        cv_id: str | None = None,
        letter_id: str | None = None,
        job_key: str | None = None,
        match: "MatchResult | None" = None,
        started: float | None = None,
        error_detail: str | None = None,
    ) -> JobApplication:
        context: dict = {}
        if req.context:
            context["notes"] = str(req.context)[:_MAX_ANSWER_LEN]
        answers = None
        if req.answers:
            answers = {
                str(k)[:200]: str(v)[:_MAX_ANSWER_LEN]
                for k, v in list(req.answers.items())[:30]
            }
        now = _now()
        record = JobApplication(
            user_id=user.id,
            job_offer_id=offer.id,
            cv_id=cv_id if cv_id is not None else req.cv_id,
            letter_id=letter_id if letter_id is not None else req.letter_id,
            status=app_status.value,
            application_url=application_url,
            context=context or None,
            answers=answers,
            source=offer.source,
            normalized_job_key=job_key or normalized_job_key(offer),
            match_score=match.score if match else None,
            match_reasons=(match.reasons if match else None) or None,
            missing_skills=(match.missing_skills if match else None) or None,
            error_detail=(error_detail or "")[:_MAX_ANSWER_LEN] or None,
            attempt_count=1,
            last_attempt_at=now,
            duration_ms=(int((time.monotonic() - started) * 1000)
                         if started is not None else None),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        _invalidate_dashboard(user.id)
        return record
