"""Schemas for the agent-retained jobs shown on the dashboard (Phase 6).

`GET /api/dashboard/jobs` — the jobs the conversation agent selected for the
user (plus any the user pinned manually), each with its current application
status so the frontend can render a per-job **Apply** button. Strictly
owner-scoped.

`ApplyOutcome` is the per-job result the agent returns after a confirmed
"apply to the selected jobs" instruction.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from schemas.jobs import FreshnessStatus

# id / job_offer_id / cv_id / letter_id / conversation_id stay `str` (not
# `UUID`) throughout this module on purpose: `SelectedJobOut` /
# `SelectedJobApplication` are reused as live working data inside
# services/conversations/job_agent_service.py (dict-keyed lookups compared
# against plain SQLAlchemy string columns, then persisted into a JSON
# conversation-state column) well beyond being an API response leaf — a
# `UUID` instance there fails to bind into a `String(36)` column / serialize
# into `JSON`. The UUID *format* is still guaranteed and asserted in
# tests/test_business_ids_are_uuid.py, at the DB layer where these ids
# actually originate.


class SelectedJobApplication(BaseModel):
    """The most recent application this user has for a selected job (or null)."""
    id: str
    status: str
    cv_id: str | None = None
    letter_id: str | None = None
    application_url: str | None = None
    match_score: float | None = None
    created_at: datetime
    updated_at: datetime


class SelectedJobOut(BaseModel):
    job_offer_id: str
    title: str
    company: str | None = None
    location: str | None = None
    city: str | None = None
    country: str | None = None
    source: str
    source_url: str
    summary: str | None = None            # description, truncated for a card
    skills: list[str] = Field(default_factory=list)
    remote_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    posted_at: datetime | None = None
    freshness: FreshnessStatus = FreshnessStatus.unknown
    match_score: float | None = None      # fit at selection time (0..1)
    origin: str = "agent"                 # "agent" | "manual"
    selected_at: datetime
    conversation_id: str | None = None
    application: SelectedJobApplication | None = None


class SelectedJobList(BaseModel):
    status: str = "success"
    total: int
    limit: int
    offset: int
    jobs: list[SelectedJobOut]


class ApplyOutcome(BaseModel):
    """One job's result after the agent runs `JobApplicationService.apply`.
    ``job_offer_id`` stays a plain string (not ``UUID``) on purpose: a stale or
    corrupted frozen proposal can carry an id that no longer resolves to a
    real offer, and this schema must still be able to report that outcome as
    ``application_status="failed"`` instead of crashing on serialization."""
    job_offer_id: str
    title: str | None = None
    application_status: str               # prepared | duplicate | unavailable | failed | manual_required | ...
    application_id: str | None = None
    application_url: str | None = None
    message: str = ""
