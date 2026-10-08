"""Schema for the per-user dashboard (`GET /api/dashboard`).

One aggregated response — activity / jobs / applications / auto-apply /
documents / matching / providers. Strictly owner-scoped (the service only ever
queries the authenticated user's rows).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DashActivity(BaseModel):
    member_since: datetime | None = None
    last_active_at: datetime | None = None
    sessions: dict[str, int] = Field(default_factory=dict)   # today/week/month/year


class DashJobs(BaseModel):
    searches: int = 0
    offers_viewed: int = 0
    offers_saved: int = 0
    providers_engaged: int = 0
    last_search_at: datetime | None = None


class DashApplications(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_window: dict[str, int] = Field(default_factory=dict)
    duplicates_avoided: int = 0
    failed: int = 0


class DashAutoApply(BaseModel):
    prepared_count: int = 0
    cv_attached_count: int = 0
    letter_generated_count: int = 0
    duplicates_avoided: int = 0
    avg_match_score: float | None = None
    note: str


class DashDocuments(BaseModel):
    cv_total: int = 0
    cover_letter_total: int = 0
    cv_used_in_application: int = 0
    cover_letter_used_in_application: int = 0
    created_by_window: dict[str, dict[str, int]] = Field(default_factory=dict)


class DashMissingSkill(BaseModel):
    skill: str
    count: int


class DashMatching(BaseModel):
    scored_applications: int = 0
    avg_score: float | None = None
    best_score: float | None = None
    good_matches: int = 0
    top_missing_skills: list[DashMissingSkill] = Field(default_factory=list)


class DashProviderRow(BaseModel):
    provider: str
    application_count: int


class DashProviders(BaseModel):
    by_provider: list[DashProviderRow] = Field(default_factory=list)
    most_applied_provider: str | None = None


class UserDashboardResponse(BaseModel):
    user_id: UUID
    activity: DashActivity
    jobs: DashJobs
    applications: DashApplications
    auto_apply: DashAutoApply
    documents: DashDocuments
    matching: DashMatching
    providers: DashProviders
    generated_at: datetime
