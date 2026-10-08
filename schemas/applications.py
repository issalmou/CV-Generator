"""
Schemas for job applications.

The service **never** submits an application automatically (that would need
authenticated / anti-bot-protected flows on LinkedIn, Indeed, etc.), so
there is deliberately **no ``submitted`` status**. An application record
captures intent + the user's context and points at the real external URL.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# Every id field in this module stays `str` (not `UUID`) on purpose: these
# objects are reused as live working data inside
# services/conversations/job_agent_service.py (built from — and compared
# against — plain SQLAlchemy string columns, then packed into a JSON
# conversation-state / pending_action column), well beyond being a terminal
# API response. A `UUID` instance there fails to bind into a `String(36)`
# column or serialize into `JSON`. The UUID *format* is guaranteed at the DB
# layer where these ids are generated and asserted in
# tests/test_business_ids_are_uuid.py.


class ApplicationStatus(str, Enum):
    prepared = "prepared"                         # fully prepared — go submit it on the external page
    manual_required = "manual_required"           # go finish it on the external page
    requires_user_action = "requires_user_action"  # login / extra step needed there
    unavailable = "unavailable"                   # the offer is expired / gone
    failed = "failed"                             # internal error preparing the record
    duplicate = "duplicate"                       # an application to this posting already exists
    # NOTE: there is deliberately still no ``submitted`` — the service never
    # completes an application on an external site (that needs the anti-bot /
    # auth / CAPTCHA flows we do not automate).


class ApplyRequest(BaseModel):
    cv_id: str | None = None
    letter_id: str | None = None
    context: str | None = Field(default=None, max_length=4000)   # user's own words
    answers: dict[str, str] | None = None
    # Phase 46 — opt in to full preparation: profile-aware match, best-CV
    # selection, and (optionally) a generated cover letter -> status "prepared".
    prepare: bool = False
    generate_letter: bool = False
    # required only when generate_letter is True and no letter_id is given —
    # the structured CV content the letter is written from (same shape as
    # /api/generate-letter). Never persisted as-is; used once to render a PDF.
    cv_profile: dict | None = None
    letter_language: str | None = None            # "fr" / "en"; defaults to the offer language


class ApplicationResponse(BaseModel):
    status: str = "success"                       # "success" | "error"
    application_status: ApplicationStatus
    job_id: str
    application_id: str | None = None
    application_url: str | None = None
    message: str
    cv_id: str | None = None                      # the CV used (auto-selected if not given)
    letter_id: str | None = None                  # the cover letter used / generated
    match_score: float | None = None              # deterministic offer<->request fit, 0..1
    match_reasons: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    is_duplicate: bool = False                    # True -> application_id points at the existing record


class ApplicationOut(BaseModel):
    id: str
    job_offer_id: str
    job_title: str | None = None
    company: str | None = None
    cv_id: str | None = None
    letter_id: str | None = None
    status: ApplicationStatus
    application_url: str | None = None
    context: dict | None = None
    answers: dict | None = None
    source: str | None = None
    match_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    attempt_count: int = 1
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime


class SavedJobOut(BaseModel):
    id: str
    job_offer_id: str
    created_at: datetime
