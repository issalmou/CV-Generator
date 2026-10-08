"""
Service: UserProfileService — the persistent structured job-search profile.

Strictly owner-scoped: every method takes the ``User`` and only ever touches
``UserProfile.user_id == user.id``. ``to_search_context`` turns the profile
into a :class:`schemas.jobs.JobSearchContext` the matching engine and search
already understand.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import UserProfile, User
from schemas.jobs import JobSearchContext
from schemas.profile import UserProfileIn, UserProfileOut

_LIST_ATTRS = (
    "target_titles", "skills", "languages", "locations", "employment_types",
    "sectors", "excluded_keywords", "excluded_companies", "preferred_companies",
)
_SCALAR_ATTRS = (
    "remote_preference", "experience_level", "salary_min", "salary_max",
    "salary_currency",
)


class UserProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user: User) -> UserProfile | None:
        return self.db.get(UserProfile, user.id)

    def get_or_empty(self, user: User) -> UserProfileOut:
        row = self.get(user)
        if row is None:
            return UserProfileOut(user_id=user.id)
        return self._to_out(row)

    def upsert(self, user: User, payload: UserProfileIn) -> UserProfileOut:
        row = self.get(user)
        if row is None:
            row = UserProfile(user_id=user.id)
            self.db.add(row)
        data = payload.model_dump()
        for attr in _LIST_ATTRS:
            setattr(row, attr, list(data.get(attr) or []))
        for attr in _SCALAR_ATTRS:
            val = data.get(attr)
            setattr(row, attr, val.value if hasattr(val, "value") else val)
        row.extra = dict(data.get("extra") or {})
        self.db.commit()
        self.db.refresh(row)
        return self._to_out(row)

    def delete(self, user: User) -> None:
        row = self.get(user)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    # ------------------------------------------------------------------

    @staticmethod
    def _to_out(row: UserProfile) -> UserProfileOut:
        return UserProfileOut(
            user_id=row.user_id,
            updated_at=row.updated_at,
            target_titles=row.target_titles or [],
            skills=row.skills or [],
            languages=row.languages or [],
            locations=row.locations or [],
            employment_types=row.employment_types or [],
            sectors=row.sectors or [],
            excluded_keywords=row.excluded_keywords or [],
            excluded_companies=row.excluded_companies or [],
            preferred_companies=row.preferred_companies or [],
            remote_preference=row.remote_preference,
            experience_level=row.experience_level,
            salary_min=row.salary_min,
            salary_max=row.salary_max,
            salary_currency=row.salary_currency,
            extra=row.extra or {},
        )

    @staticmethod
    def to_search_context(profile: UserProfile | UserProfileOut | None) -> JobSearchContext:
        """Best-effort mapping of the profile onto a ``JobSearchContext``.
        Only *set* fields are carried; nothing is invented."""
        if profile is None:
            return JobSearchContext()
        get = (lambda a: getattr(profile, a, None))
        titles = list(get("target_titles") or [])
        ctx = JobSearchContext(
            query=titles[0] if titles else None,
            keywords=titles[1:] if len(titles) > 1 else [],
            skills=list(get("skills") or []),
            technologies=list(get("skills") or []),
            excluded_keywords=list(get("excluded_keywords") or []),
            excluded_companies=list(get("excluded_companies") or []),
            preferred_companies=list(get("preferred_companies") or []),
            sectors=list(get("sectors") or []),
            location=(list(get("locations") or []) or [None])[0],
            remote_type=_enum(get("remote_preference")),
            experience_level=_enum(get("experience_level")),
            salary_min=get("salary_min"),
            salary_max=get("salary_max"),
            salary_currency=get("salary_currency"),
            language=(list(get("languages") or []) or [None])[0],
        )
        return ctx


def _enum(v):
    return v.value if hasattr(v, "value") else v
