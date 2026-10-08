"""
Service: StatsService — superadmin analytics.

Every figure is a live SQL aggregate (``COUNT`` / ``GROUP BY`` /
``COUNT(DISTINCT ...)``) over the tables that already exist — no event
pipeline, no derived counters, nothing fabricated. A metric the schema cannot
support (e.g. "job cards viewed") is simply absent, never guessed.

Activity windows use ``User.last_active_at`` (a single timestamp, updated at
most once / 10 min by ``dependencies.auth``):
    DAU  = active in the last 1 day     WAU = last 7 days
    MAU  = last 30 days                 YAU = last 365 days
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from models import (
    GeneratedCV, GeneratedLetter, JobApplication, SavedJob, UsageEvent, User,
)

_WINDOWS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StatsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # users / activity  (Phase 34)
    # ------------------------------------------------------------------

    def usage_overview(self) -> dict:
        now = _now()
        total = self.db.scalar(select(func.count()).select_from(User)) or 0
        active_total = self.db.scalar(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        ) or 0

        # any usage events at all -> use the event log (finer than last_active_at)
        have_events = bool(self.db.scalar(select(func.count()).select_from(UsageEvent)))
        cut = {label: now - timedelta(days=d) for label, d in _WINDOWS.items()}

        active: dict[str, int] = {}
        if have_events:
            # one range scan over the year window, count-distinct per sub-window
            row = self.db.execute(
                select(*[
                    func.count(func.distinct(
                        case((UsageEvent.created_at >= cut[label], UsageEvent.user_id))
                    )).label(label)
                    for label in _WINDOWS
                ]).where(UsageEvent.created_at >= cut["year"])
            ).one()
            active = {label: int(getattr(row, label) or 0) for label in _WINDOWS}
        else:
            for label in _WINDOWS:
                active[label] = self.db.scalar(
                    select(func.count()).select_from(User)
                    .where(User.last_active_at >= cut[label])
                ) or 0

        signups: dict[str, int] = {}
        for label in _WINDOWS:
            signups[label] = self.db.scalar(
                select(func.count()).select_from(User).where(User.created_at >= cut[label])
            ) or 0

        return {
            "total_users": total,
            "active_users": active_total,
            "dau": active["day"], "wau": active["week"],
            "mau": active["month"], "yau": active["year"],
            "active_users_by_window": active,
            "new_users_by_window": signups,
            "activity_source": "events" if have_events else "last_active_at",
            "generated_at": now,
        }

    def per_user(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        total = self.db.scalar(select(func.count()).select_from(User)) or 0
        users = list(self.db.scalars(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        ))
        if not users:
            return [], total
        ids = [u.id for u in users]

        cvs = self._count_by_user(GeneratedCV, ids)
        letters = self._count_by_user(GeneratedLetter, ids)
        saved = self._count_by_user(SavedJob, ids)
        apps = self._app_counts_by_user(ids)
        events = self._event_counts_by_user(ids)

        rows = []
        for u in users:
            a = apps.get(u.id, {})
            ev = events.get(u.id, {})
            rows.append({
                "id": u.id, "email": u.email,
                "is_active": u.is_active, "is_superadmin": u.is_superadmin,
                "created_at": u.created_at, "last_active_at": u.last_active_at,
                "cv_count": cvs.get(u.id, 0),
                "cover_letter_count": letters.get(u.id, 0),
                "saved_job_count": saved.get(u.id, 0),
                "application_count": sum(a.values()),
                "applications_by_status": a,
                "session_count": ev.get("login", 0),
                "search_count": ev.get("search", 0),
                "job_view_count": ev.get("job_view", 0),
            })
        return rows, total

    def _event_counts_by_user(self, ids: list[str]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for uid, kind, n in self.db.execute(
            select(UsageEvent.user_id, UsageEvent.kind, func.count())
            .where(UsageEvent.user_id.in_(ids))
            .group_by(UsageEvent.user_id, UsageEvent.kind)
        ).all():
            out.setdefault(uid, {})[kind] = n
        return out

    # ------------------------------------------------------------------
    # job-search activity  (Phase 70) — from the UsageEvent log only
    # ------------------------------------------------------------------

    def jobs_activity(self) -> dict:
        """Searches / offer views / saves per time window, plus the distinct
        users doing each. Everything is a GROUP BY over ``usage_events`` — a
        window with no recorded events is simply zero, never estimated."""
        now = _now()
        cut = {label: now - timedelta(days=d) for label, d in _WINDOWS.items()}
        kinds = ("search", "job_view", "job_saved")

        zero = {label: 0 for label in _WINDOWS}
        events: dict[str, dict[str, int]] = {k: dict(zero) for k in kinds}
        actors: dict[str, dict[str, int]] = {k: dict(zero) for k in kinds}
        row = self.db.execute(
            select(
                UsageEvent.kind,
                *[
                    func.count(
                        case((UsageEvent.created_at >= cut[label], 1))
                    ).label(f"n_{label}")
                    for label in _WINDOWS
                ],
                *[
                    func.count(func.distinct(
                        case((UsageEvent.created_at >= cut[label], UsageEvent.user_id))
                    )).label(f"u_{label}")
                    for label in _WINDOWS
                ],
            )
            .where(UsageEvent.kind.in_(kinds), UsageEvent.created_at >= cut["year"])
            .group_by(UsageEvent.kind)
        ).all()
        for r in row:
            events[r.kind] = {label: int(getattr(r, f"n_{label}") or 0) for label in _WINDOWS}
            actors[r.kind] = {label: int(getattr(r, f"u_{label}") or 0) for label in _WINDOWS}

        return {
            "searches_by_window": events["search"],
            "searching_users_by_window": actors["search"],
            "offer_views_by_window": events["job_view"],
            "offers_saved_by_window": events["job_saved"],
            "total_saved_offers": self.db.scalar(
                select(func.count()).select_from(SavedJob)
            ) or 0,
            "generated_at": now,
        }

    # ------------------------------------------------------------------
    # applications  (Phase 35)
    # ------------------------------------------------------------------

    def application_stats(self) -> dict:
        now = _now()
        by_status = dict(self.db.execute(
            select(JobApplication.status, func.count()).group_by(JobApplication.status)
        ).all())
        total = sum(by_status.values())

        by_window = {}
        for label, days in _WINDOWS.items():
            since = now - timedelta(days=days)
            by_window[label] = self.db.scalar(
                select(func.count()).select_from(JobApplication)
                .where(JobApplication.created_at >= since)
            ) or 0

        by_provider = dict(self.db.execute(
            select(JobApplication.source, func.count())
            .group_by(JobApplication.source)
        ).all())

        avg_ms = self.db.scalar(
            select(func.avg(JobApplication.duration_ms))
            .where(JobApplication.duration_ms.isnot(None))
        )
        avg_match = self.db.scalar(
            select(func.avg(JobApplication.match_score))
            .where(JobApplication.match_score.isnot(None))
        )
        cv_used = self.db.scalar(
            select(func.count(func.distinct(JobApplication.cv_id)))
            .where(JobApplication.cv_id.isnot(None))
        ) or 0
        letter_used = self.db.scalar(
            select(func.count(func.distinct(JobApplication.letter_id)))
            .where(JobApplication.letter_id.isnot(None))
        ) or 0

        return {
            "total": total,
            "by_status": by_status,
            "by_window": by_window,
            "by_provider": {k or "unknown": v for k, v in by_provider.items()},
            "avg_prepare_ms": int(avg_ms) if avg_ms is not None else None,
            "avg_match_score": round(float(avg_match), 4) if avg_match is not None else None,
            "duplicate_count": by_status.get("duplicate", 0),
            "failed_count": by_status.get("failed", 0),
            "prepared_count": by_status.get("prepared", 0),
            "cv_used_count": cv_used,
            "cover_letter_used_count": letter_used,
            "note": "Every application is user-initiated intent; the service "
                    "never submits on an external site (no 'submitted' status).",
            "generated_at": now,
        }

    def applications_per_user(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        totals = select(
            JobApplication.user_id.label("uid"), func.count().label("n")
        ).group_by(JobApplication.user_id).subquery()
        total_users = self.db.scalar(select(func.count()).select_from(totals)) or 0

        rows = self.db.execute(
            select(User.id, User.email, totals.c.n)
            .join(totals, totals.c.uid == User.id)
            .order_by(totals.c.n.desc())
            .limit(limit).offset(offset)
        ).all()
        ids = [r[0] for r in rows]
        per_status = self._app_counts_by_user(ids) if ids else {}
        return [
            {"id": uid, "email": email, "application_count": n,
             "applications_by_status": per_status.get(uid, {})}
            for uid, email, n in rows
        ], total_users

    # ------------------------------------------------------------------
    # documents  (Phase 36)
    # ------------------------------------------------------------------

    def document_stats(self) -> dict:
        now = _now()
        cv_total = self.db.scalar(select(func.count()).select_from(GeneratedCV)) or 0
        letter_total = self.db.scalar(select(func.count()).select_from(GeneratedLetter)) or 0
        used_cv = self.db.scalar(
            select(func.count(func.distinct(JobApplication.cv_id)))
            .where(JobApplication.cv_id.isnot(None))
        ) or 0
        used_letter = self.db.scalar(
            select(func.count(func.distinct(JobApplication.letter_id)))
            .where(JobApplication.letter_id.isnot(None))
        ) or 0

        created = {"cv": {}, "cover_letter": {}}
        for label, days in _WINDOWS.items():
            since = now - timedelta(days=days)
            created["cv"][label] = self.db.scalar(
                select(func.count()).select_from(GeneratedCV).where(GeneratedCV.created_at >= since)
            ) or 0
            created["cover_letter"][label] = self.db.scalar(
                select(func.count()).select_from(GeneratedLetter)
                .where(GeneratedLetter.created_at >= since)
            ) or 0

        return {
            "cv_total": cv_total,
            "cover_letter_total": letter_total,
            "document_total": cv_total + letter_total,
            "cv_used_in_application": used_cv,
            "cover_letter_used_in_application": used_letter,
            "created_by_window": created,
            "generated_at": now,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _count_by_user(self, model, ids: list[str]) -> dict[str, int]:
        return dict(self.db.execute(
            select(model.user_id, func.count())
            .where(model.user_id.in_(ids)).group_by(model.user_id)
        ).all())

    def _app_counts_by_user(self, ids: list[str]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for uid, st, n in self.db.execute(
            select(JobApplication.user_id, JobApplication.status, func.count())
            .where(JobApplication.user_id.in_(ids))
            .group_by(JobApplication.user_id, JobApplication.status)
        ).all():
            out.setdefault(uid, {})[st] = n
        return out
