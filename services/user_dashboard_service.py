"""
Service: UserDashboardService — one aggregated, **strictly owner-scoped**
dashboard for the authenticated user (``GET /api/dashboard``).

Every query filters on ``user.id``; no ``user_id`` is ever accepted from the
client. Reuses the same SQL-aggregate patterns as ``StatsService`` (the
superadmin one) — `COUNT` / `GROUP BY` / `COUNT(DISTINCT)` — never "load all
rows and count in Python" (the one exception is the per-user `missing_skills`
tally, bounded to this user's last N applications).

Figures come only from persisted data; a metric with no backing column is
absent, never invented. The auto-apply section never claims a submission — the
service prepares, it does not submit.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from models import (
    GeneratedCV, GeneratedLetter, JobApplication, JobOffer, SavedJob, UsageEvent, User,
)
from schemas.dashboard import UserDashboardResponse
from schemas.dashboard_jobs import (
    SelectedJobApplication, SelectedJobList, SelectedJobOut,
)
from services.cache_service import cache
from services.jobs.freshness import classify as _classify_freshness

_WINDOWS = {"today": 1, "week": 7, "month": 30, "year": 365}
_GOOD_MATCH = 0.6
_MISSING_SKILLS_SCAN = 500
_JOB_SUMMARY_MAX = 700


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_key(user_id: str) -> str:
    """Keyed **only** on ``user_id`` — never anything client-supplied."""
    return cache.key("dashboard", "user", user_id)


def invalidate(user_id: str) -> None:
    """Best-effort — call after any write that changes a user's own dashboard
    figures (a new application, a saved/unsaved job, a document created or
    deleted, …). Safe to call even when the cache backend is down."""
    try:
        cache.delete(_cache_key(user_id))
    except Exception:  # noqa: BLE001 — invalidation must never break a request
        pass


class UserDashboardService:
    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self._uid = user.id
        self._now = _now()

    # ------------------------------------------------------------------

    def build(self) -> dict:
        key = _cache_key(self._uid)
        cached = cache.get(key)
        if isinstance(cached, dict) and cached.get("user_id") == self._uid:
            return cached

        result = {
            "user_id": self._uid,
            "activity": self._activity(),
            "jobs": self._jobs(),
            "applications": self._applications(),
            "auto_apply": self._auto_apply(),
            "documents": self._documents(),
            "matching": self._matching(),
            "providers": self._providers(),
            "generated_at": self._now,
        }
        # Validate + dump to plain JSON-safe primitives before caching — the
        # Redis backend round-trips through JSON anyway (datetimes become
        # strings); doing it here means a cache HIT and a cache MISS return
        # the exact same shape, and FastAPI's response_model still coerces
        # the ISO-8601 strings back to real datetimes on the way out.
        safe = UserDashboardResponse.model_validate(result).model_dump(mode="json")
        cache.set(key, safe, ttl=settings.DASHBOARD_CACHE_TTL)
        return safe

    # ------------------------------------------------------------------

    def _event_count(self, kind: str, *, since: datetime | None = None) -> int:
        stmt = select(func.count()).select_from(UsageEvent).where(
            UsageEvent.user_id == self._uid, UsageEvent.kind == kind
        )
        if since is not None:
            stmt = stmt.where(UsageEvent.created_at >= since)
        return self.db.scalar(stmt) or 0

    def _app_windows(self, extra=None) -> dict[str, int]:
        out: dict[str, int] = {}
        for label, days in _WINDOWS.items():
            stmt = select(func.count()).select_from(JobApplication).where(
                JobApplication.user_id == self._uid,
                JobApplication.created_at >= self._now - timedelta(days=days),
            )
            if extra is not None:
                stmt = stmt.where(extra)
            out[label] = self.db.scalar(stmt) or 0
        return out

    # ------------------------------------------------------------------

    def _activity(self) -> dict:
        return {
            "member_since": self.user.created_at,
            "last_active_at": self.user.last_active_at,
            "sessions": {
                label: self._event_count("login", since=self._now - timedelta(days=d))
                for label, d in _WINDOWS.items()
            },
        }

    def _jobs(self) -> dict:
        last_search = self.db.scalar(
            select(func.max(UsageEvent.created_at)).where(
                UsageEvent.user_id == self._uid, UsageEvent.kind == "search"
            )
        )
        providers_used = self.db.scalar(
            select(func.count(func.distinct(JobApplication.source)))
            .where(JobApplication.user_id == self._uid, JobApplication.source.isnot(None))
        ) or 0
        return {
            "searches": self._event_count("search"),
            "offers_viewed": self._event_count("job_view"),
            "offers_saved": self.db.scalar(
                select(func.count()).select_from(SavedJob)
                .where(SavedJob.user_id == self._uid)
            ) or 0,
            "providers_engaged": providers_used,
            "last_search_at": last_search,
        }

    def _applications(self) -> dict:
        by_status = dict(self.db.execute(
            select(JobApplication.status, func.count())
            .where(JobApplication.user_id == self._uid)
            .group_by(JobApplication.status)
        ).all())
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_window": self._app_windows(),
            "duplicates_avoided": by_status.get("duplicate", 0),
            "failed": by_status.get("failed", 0),
        }

    def _auto_apply(self) -> dict:
        JA = JobApplication
        prepared = self.db.scalar(
            select(func.count()).select_from(JA)
            .where(JA.user_id == self._uid, JA.status == "prepared")
        ) or 0
        with_cv = self.db.scalar(
            select(func.count()).select_from(JA)
            .where(JA.user_id == self._uid, JA.cv_id.isnot(None))
        ) or 0
        with_letter = self.db.scalar(
            select(func.count()).select_from(JA)
            .where(JA.user_id == self._uid, JA.letter_id.isnot(None))
        ) or 0
        avg_match = self.db.scalar(
            select(func.avg(JA.match_score))
            .where(JA.user_id == self._uid, JA.match_score.isnot(None))
        )
        return {
            "prepared_count": prepared,
            "cv_attached_count": with_cv,
            "letter_generated_count": with_letter,
            "duplicates_avoided": self.db.scalar(
                select(func.count()).select_from(JA)
                .where(JA.user_id == self._uid, JA.status == "duplicate")
            ) or 0,
            "avg_match_score": round(float(avg_match), 4) if avg_match is not None else None,
            "note": "The service prepares applications — it never submits on an "
                    "external site (no 'submitted' status).",
        }

    def _documents(self) -> dict:
        cv_total = self.db.scalar(
            select(func.count()).select_from(GeneratedCV)
            .where(GeneratedCV.user_id == self._uid)
        ) or 0
        letter_total = self.db.scalar(
            select(func.count()).select_from(GeneratedLetter)
            .where(GeneratedLetter.user_id == self._uid)
        ) or 0
        cv_used = self.db.scalar(
            select(func.count(func.distinct(JobApplication.cv_id)))
            .where(JobApplication.user_id == self._uid, JobApplication.cv_id.isnot(None))
        ) or 0
        letter_used = self.db.scalar(
            select(func.count(func.distinct(JobApplication.letter_id)))
            .where(JobApplication.user_id == self._uid, JobApplication.letter_id.isnot(None))
        ) or 0
        created = {"cv": {}, "cover_letter": {}}
        for label, days in _WINDOWS.items():
            since = self._now - timedelta(days=days)
            created["cv"][label] = self.db.scalar(
                select(func.count()).select_from(GeneratedCV)
                .where(GeneratedCV.user_id == self._uid, GeneratedCV.created_at >= since)
            ) or 0
            created["cover_letter"][label] = self.db.scalar(
                select(func.count()).select_from(GeneratedLetter)
                .where(GeneratedLetter.user_id == self._uid,
                       GeneratedLetter.created_at >= since)
            ) or 0
        return {
            "cv_total": cv_total,
            "cover_letter_total": letter_total,
            "cv_used_in_application": cv_used,
            "cover_letter_used_in_application": letter_used,
            "created_by_window": created,
        }

    def _matching(self) -> dict:
        JA = JobApplication
        row = self.db.execute(
            select(func.avg(JA.match_score), func.max(JA.match_score),
                   func.count())
            .where(JA.user_id == self._uid, JA.match_score.isnot(None))
        ).one()
        avg, best, scored = row
        good = self.db.scalar(
            select(func.count()).select_from(JA)
            .where(JA.user_id == self._uid, JA.match_score >= _GOOD_MATCH)
        ) or 0

        # per-user missing-skills tally — bounded to this user's recent apps
        counter: collections.Counter = collections.Counter()
        for (skills,) in self.db.execute(
            select(JA.missing_skills)
            .where(JA.user_id == self._uid, JA.missing_skills.isnot(None))
            .order_by(JA.created_at.desc()).limit(_MISSING_SKILLS_SCAN)
        ):
            for s in (skills or []):
                counter[str(s)] += 1

        return {
            "scored_applications": int(scored or 0),
            "avg_score": round(float(avg), 4) if avg is not None else None,
            "best_score": round(float(best), 4) if best is not None else None,
            "good_matches": good,
            "top_missing_skills": [
                {"skill": s, "count": n} for s, n in counter.most_common(8)
            ],
        }

    def _providers(self) -> dict:
        rows = self.db.execute(
            select(JobApplication.source, func.count())
            .where(JobApplication.user_id == self._uid, JobApplication.source.isnot(None))
            .group_by(JobApplication.source)
            .order_by(func.count().desc())
        ).all()
        return {
            "by_provider": [{"provider": p, "application_count": n} for p, n in rows],
            "most_applied_provider": rows[0][0] if rows else None,
        }

    # ------------------------------------------------------------------
    # Phase 6 — the actual jobs the agent retained (+ per-job apply status)
    # ------------------------------------------------------------------

    def selected_jobs(self, *, limit: int = 20, offset: int = 0) -> SelectedJobList:
        """The user's job selection (agent search results + manually pinned),
        newest first, each joined to its most recent application. Owner-scoped.
        This is what the dashboard renders with an **Apply** button per job."""
        total = self.db.scalar(
            select(func.count()).select_from(SavedJob)
            .where(SavedJob.user_id == self._uid)
        ) or 0

        sel_rows = list(self.db.scalars(
            select(SavedJob)
            .where(SavedJob.user_id == self._uid)
            .order_by(SavedJob.created_at.desc())
            .limit(limit).offset(offset)
        ))
        if not sel_rows:
            return SelectedJobList(total=total, limit=limit, offset=offset, jobs=[])

        offer_ids = [s.job_offer_id for s in sel_rows]
        offers = {
            o.id: o for o in self.db.scalars(
                select(JobOffer).where(JobOffer.id.in_(offer_ids))
            )
        }
        # most recent application per (user, offer) in this page
        apps: dict[str, JobApplication] = {}
        for app in self.db.scalars(
            select(JobApplication)
            .where(JobApplication.user_id == self._uid,
                   JobApplication.job_offer_id.in_(offer_ids))
            .order_by(JobApplication.created_at.desc())
        ):
            apps.setdefault(app.job_offer_id, app)

        jobs: list[SelectedJobOut] = []
        for s in sel_rows:
            offer = offers.get(s.job_offer_id)
            if offer is None:
                continue   # offer row was pruned — skip, never fabricate
            app = apps.get(s.job_offer_id)
            jobs.append(SelectedJobOut(
                job_offer_id=offer.id,
                title=offer.title,
                company=offer.company,
                location=offer.location,
                city=offer.city,
                country=offer.country,
                source=offer.source,
                source_url=offer.source_url,
                summary=(offer.description or "")[:_JOB_SUMMARY_MAX] or None,
                skills=offer.skills or [],
                remote_type=offer.remote_type,
                salary_min=offer.salary_min,
                salary_max=offer.salary_max,
                salary_currency=offer.salary_currency,
                posted_at=offer.posted_at,
                freshness=_classify_freshness(offer),
                match_score=s.match_score,
                origin=s.origin or "agent",
                selected_at=s.created_at,
                conversation_id=s.conversation_id,
                application=(
                    SelectedJobApplication(
                        id=app.id, status=app.status, cv_id=app.cv_id,
                        letter_id=app.letter_id, application_url=app.application_url,
                        match_score=app.match_score,
                        created_at=app.created_at, updated_at=app.updated_at,
                    ) if app is not None else None
                ),
            ))
        return SelectedJobList(total=total, limit=limit, offset=offset, jobs=jobs)
