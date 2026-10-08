"""
Schemas for the job & internship search system.

- ``JobSearchContext`` is the structured preference bag the agent fills
  progressively from the conversation (every field optional, mergeable).
- ``NormalizedOffer`` is what every provider must return — one shape,
  regardless of the source's native format.
- ``JobOfferOut`` / ``JobSearchResponse`` / ``JobDetailResponse`` are the
  frontend-facing shapes.

Scraped free text (``description``) is always stored/returned as plain
text — never HTML — and is treated as untrusted data everywhere.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse as _urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobType(str, Enum):
    job = "job"
    internship = "internship"
    any = "any"


class RemoteType(str, Enum):
    onsite = "onsite"
    hybrid = "hybrid"
    remote = "remote"
    any = "any"


class ExperienceLevel(str, Enum):
    student = "student"
    entry = "entry"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    any = "any"


class FreshnessStatus(str, Enum):
    fresh = "fresh"
    stale = "stale"
    expired = "expired"
    unknown = "unknown"


class ProviderStatus(str, Enum):
    """Coarse, frontend-facing status (kept stable — used in ``sources``)."""
    success = "success"
    partial = "partial"
    unavailable = "unavailable"
    disabled = "disabled"


class ProviderState(str, Enum):
    """Fine-grained provider state — *why* a source did/didn't deliver.
    Exposed in ``ProviderInfo.state`` and ``JobSearchResponse.provider_states``.
    Coarsens to ``ProviderStatus`` via :meth:`to_status` so ``sources`` and the
    existing assertions stay unchanged."""
    available = "available"                          # responded (even with 0 offers)
    degraded = "degraded"                            # responded but incomplete / partial data
    auth_required = "auth_required"                  # login / session / challenge wall hit
    temporarily_unavailable = "temporarily_unavailable"  # timeout / 429 / transient network
    blocked = "blocked"                              # persistent 403 / 451 / 999 / access denied
    error = "error"                                  # unexpected internal failure (parse, crash)
    disabled = "disabled"                            # not enabled / not configured

    def to_status(self) -> "ProviderStatus":
        if self is ProviderState.available:
            return ProviderStatus.success
        if self is ProviderState.degraded:
            return ProviderStatus.partial
        if self is ProviderState.disabled:
            return ProviderStatus.disabled
        return ProviderStatus.unavailable


class SortOrder(str, Enum):
    relevance = "relevance"
    date = "date"


# Ordering used by the ``min_freshness`` filter (index = "acceptability").
_FRESHNESS_RANK = {
    FreshnessStatus.expired: 0,
    FreshnessStatus.unknown: 1,
    FreshnessStatus.stale: 2,
    FreshnessStatus.fresh: 3,
}


def freshness_at_least(value: FreshnessStatus, minimum: FreshnessStatus) -> bool:
    return _FRESHNESS_RANK[value] >= _FRESHNESS_RANK[minimum]


# ---------------------------------------------------------------------------
# Search context (progressive, agent-filled)
# ---------------------------------------------------------------------------

_LIST_FIELDS = {
    "skills", "technologies", "preferred_companies", "excluded_companies", "keywords",
    "excluded_keywords", "sectors",
}


class JobSearchContext(BaseModel):
    """Structured user preferences. Every field optional; agent completes it
    over multiple conversation turns via :func:`merge`."""

    query: str | None = None
    job_type: JobType | None = None
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    experience_level: ExperienceLevel | None = None
    education: str | None = None
    location: str | None = None
    city: str | None = None
    country: str | None = None
    remote_type: RemoteType | None = None
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str | None = None
    salary_period: str | None = None
    contract_type: str | None = None
    internship_duration_months: int | None = Field(default=None, ge=0, le=36)
    internship_start_date: str | None = None
    availability: str | None = None
    preferred_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    language: str | None = None
    keywords: list[str] = Field(default_factory=list)
    extra_preferences: str | None = None

    @field_validator(
        "skills", "technologies", "preferred_companies", "excluded_companies", "keywords",
        "excluded_keywords", "sectors",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [p.strip() for p in re.split(r"[,;]", v) if p.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(p).strip() for p in v if str(p).strip()]
        return []

    def merge(self, other: "JobSearchContext | dict | None") -> "JobSearchContext":
        """Return a new context: ``other`` (newer) wins on scalars; list
        fields are unioned (order-preserving, de-duped case-insensitively).
        A later user message can therefore override earlier preferences."""
        if other is None:
            return self.model_copy(deep=True)
        other_ctx = other if isinstance(other, JobSearchContext) else JobSearchContext.model_validate(other)
        data = self.model_dump()
        for key, new_val in other_ctx.model_dump().items():
            if key in _LIST_FIELDS:
                merged: list[str] = list(data.get(key) or [])
                seen = {x.lower() for x in merged}
                for item in new_val or []:
                    if item.lower() not in seen:
                        merged.append(item)
                        seen.add(item.lower())
                data[key] = merged
            elif new_val not in (None, ""):
                data[key] = new_val
        return JobSearchContext.model_validate(data)


# ---------------------------------------------------------------------------
# Request — Phase 6: internal only. The conversation agent builds this and calls
# JobSearchService directly; there is no `POST /api/jobs/search` endpoint.
# ---------------------------------------------------------------------------

class JobSearchRequest(BaseModel):
    context: JobSearchContext = Field(default_factory=JobSearchContext)
    sources: list[str] | None = None
    page: int = Field(default=1, ge=1, le=100)
    page_size: int = Field(default=20, ge=1, le=50)
    sort: SortOrder = SortOrder.relevance
    max_age_days: int | None = Field(default=None, ge=1, le=365)
    min_freshness: FreshnessStatus = FreshnessStatus.stale

    @field_validator("sources", mode="before")
    @classmethod
    def _clean_sources(cls, v: Any) -> list[str] | None:
        if not v:
            return None
        return [str(s).strip().lower() for s in v if str(s).strip()]


# ---------------------------------------------------------------------------
# Provider output
# ---------------------------------------------------------------------------

class NormalizedOffer(BaseModel):
    """One provider result, already mapped onto the common shape."""

    source: str
    source_job_id: str
    source_url: str
    title: str
    company: str | None = None
    company_url: str | None = None
    location: str | None = None
    city: str | None = None
    country: str | None = None
    description: str | None = None            # plain text only
    employment_type: str | None = None
    job_type: JobType = JobType.job
    remote_type: RemoteType | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    experience_level: ExperienceLevel | None = None
    skills: list[str] = Field(default_factory=list)
    language: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    internship_duration_months: int | None = None
    internship_start_date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    also_seen_on: list[str] = Field(default_factory=list)
    # populated by the ranking step; not part of the identity / hash
    match_score: float = 0.0

    @field_validator("source_url", "company_url", mode="before")
    @classmethod
    def _clean_url(cls, v: Any) -> Any:
        """Trim links and neutralise anything unsafe to *display* — a link
        carrying credentials (``https://user:pass@host``), control characters or
        a non-``http(s)`` scheme is blanked here so the search service then
        drops the offer (``source_url`` is required, a URL is never fabricated)."""
        if not isinstance(v, str):
            return v
        v = v.strip()
        if not v:
            return v
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):  # CR/LF/tab/...
            return ""
        low = v.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            return ""
        try:
            parsed = _urlparse(v)
        except ValueError:
            return ""
        if parsed.username or parsed.password or "@" in (parsed.netloc.split("/", 1)[0]):
            return ""            # credentials in the URL — never surface it
        if not parsed.hostname:
            return ""
        return v

    def content_hash(self) -> str:
        basis = "|".join([
            (self.source or "").lower(),
            (self.source_job_id or "").lower(),
            _slug(self.company),
            _slug(self.title),
            _slug(self.city or self.country or self.location),
        ])
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _slug(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


# ---------------------------------------------------------------------------
# Frontend-facing shapes
# ---------------------------------------------------------------------------

class JobOfferOut(BaseModel):
    # Stays `str` (not `UUID`) on purpose: this object is reused internally as
    # working data (job_agent_service persists/compares `.id` against plain
    # SQLAlchemy string columns and a JSON conversation-state column) well
    # beyond the API response — a `UUID` instance there fails to bind into a
    # `String(36)` column / serialize into `JSON`. See tests/test_business_ids_are_uuid.py
    # for the format guarantee instead.
    id: str
    source: str
    source_job_id: str          # the PROVIDER's own native id — never a UUID of ours
    source_url: str
    title: str
    company: str | None = None
    company_url: str | None = None
    location: str | None = None
    city: str | None = None
    country: str | None = None
    description: str | None = None
    employment_type: str | None = None
    job_type: str
    remote_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    experience_level: str | None = None
    skills: list[str] = Field(default_factory=list)
    language: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    scraped_at: datetime | None = None
    last_verified_at: datetime | None = None
    internship_duration_months: int | None = None
    internship_start_date: str | None = None
    match_score: float = 0.0
    freshness: FreshnessStatus = FreshnessStatus.unknown
    is_active: bool = True
    also_seen_on: list[str] = Field(default_factory=list)


class ApplicationMethodOut(BaseModel):
    method: str                       # external_url | platform_login_required | unknown
    can_be_assisted: bool = False     # automated submission is never available in v1
    apply_url: str | None = None


class JobDetailResponse(BaseModel):
    status: str = "success"
    job: JobOfferOut
    application: ApplicationMethodOut
    freshness: FreshnessStatus


class JobSearchResponse(BaseModel):
    status: str = "success"
    query: JobSearchContext
    results: list[JobOfferOut]
    total: int
    page: int
    page_size: int
    sources: dict[str, ProviderStatus]                 # coarse (stable)
    provider_states: dict[str, ProviderState] = Field(default_factory=dict)  # fine-grained
    from_cache: bool = False


class ProviderInfo(BaseModel):
    name: str
    enabled: bool
    circuit_state: str                # closed | open | half_open
    last_status: ProviderStatus | None = None
    state: ProviderState | None = None   # fine-grained last state
    # rolling run metrics (cache-backed, 7-day TTL) — read-only diagnostics
    runs: int = 0
    error_rate: float | None = None      # fail / runs  (None until the first run)
    last_run_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_offer_count: int | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None        # short sanitised label — never a secret
    health_note: str | None = None       # set only when the provider looks structurally broken
