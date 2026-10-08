"""Schemas for the per-user structured job-search profile (``/api/profile``).

Owner-scoped: only ever the caller's own profile. Superadmin stats never expose
these fields (they aggregate counts only).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from schemas.jobs import ExperienceLevel, RemoteType

_MAX_ITEMS = 40
_MAX_ITEM_LEN = 120
_EMPLOYMENT_TYPES = {"job", "full_time", "part_time", "contract", "internship",
                     "temporary", "freelance", "apprenticeship"}


def _clean_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[,;]", v)
    if not isinstance(v, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in v:
        s = str(item).strip()[:_MAX_ITEM_LEN]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= _MAX_ITEMS:
            break
    return out


class UserProfileIn(BaseModel):
    target_titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    remote_preference: RemoteType | None = None
    experience_level: ExperienceLevel | None = None
    salary_min: float | None = Field(default=None, ge=0, le=100_000_000)
    salary_max: float | None = Field(default=None, ge=0, le=100_000_000)
    salary_currency: str | None = Field(default=None, max_length=8)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "target_titles", "skills", "languages", "locations", "employment_types",
        "sectors", "excluded_keywords", "excluded_companies", "preferred_companies",
        mode="before",
    )
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _clean_list(v)

    @field_validator("employment_types")
    @classmethod
    def _emp(cls, v: list[str]) -> list[str]:
        return [t for t in v if t.lower() in _EMPLOYMENT_TYPES]

    @field_validator("salary_currency", mode="before")
    @classmethod
    def _cur(cls, v: Any) -> Any:
        return v.strip().upper()[:8] if isinstance(v, str) and v.strip() else None

    @field_validator("extra")
    @classmethod
    def _extra(cls, v: dict) -> dict:
        # keep it small + primitive; never trust it as instructions anywhere
        out: dict[str, Any] = {}
        for k, val in list(v.items())[:20]:
            if isinstance(val, (str, int, float, bool)) or val is None:
                out[str(k)[:60]] = val if not isinstance(val, str) else val[:500]
        return out


class UserProfileOut(UserProfileIn):
    user_id: UUID
    updated_at: datetime | None = None
