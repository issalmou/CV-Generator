"""Schemas for the superadmin API (``/api/admin/*``).

None of the *request* models carry ``is_superadmin`` as something a normal
caller could set on themselves — the only mutation path is
``AdminUserUpdate`` on a route guarded by ``require_superadmin``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AdminUserRow(BaseModel):
    id: UUID
    email: str
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    last_active_at: datetime | None = None
    cv_count: int = 0
    cover_letter_count: int = 0
    application_count: int = 0


class AdminUserList(BaseModel):
    total: int
    limit: int
    offset: int
    users: list[AdminUserRow]


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_superadmin: bool | None = None


# --- ATS boards -------------------------------------------------------------

_SLUG_PROVIDERS = {"greenhouse", "lever", "ashby", "smartrecruiters", "rippling"}
_URL_PROVIDERS = {"workday", "oracle_hcm", "talentbrew"}
ATS_MANAGED_PROVIDERS = sorted(_SLUG_PROVIDERS | _URL_PROVIDERS)


class AtsBoardRow(BaseModel):
    id: UUID
    provider: str
    token: str
    enabled: bool
    label: str | None = None
    note: str | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_offer_count: int | None = None
    last_test_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AtsBoardCreate(BaseModel):
    provider: str = Field(min_length=2, max_length=32)
    token: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    label: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class AtsBoardUpdate(BaseModel):
    token: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class AtsBoardList(BaseModel):
    providers: list[str]
    boards: list[AtsBoardRow]


class AtsBoardTestResult(BaseModel):
    provider: str
    token: str
    reachable: bool
    offers_found: int | None = None
    detail: str | None = None
    board_id: UUID | None = None


# --- provider stats --------------------------------------------------------

class UsageOverview(BaseModel):
    total_users: int
    active_users: int
    dau: int
    wau: int
    mau: int
    yau: int
    active_users_by_window: dict[str, int]
    new_users_by_window: dict[str, int]
    activity_source: str = "last_active_at"
    generated_at: datetime


class UserStatRow(BaseModel):
    id: UUID
    email: str
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    last_active_at: datetime | None = None
    cv_count: int = 0
    cover_letter_count: int = 0
    saved_job_count: int = 0
    application_count: int = 0
    applications_by_status: dict[str, int] = Field(default_factory=dict)
    session_count: int = 0
    search_count: int = 0
    job_view_count: int = 0


class UserStatList(BaseModel):
    total: int
    limit: int
    offset: int
    users: list[UserStatRow]


class ApplicationStats(BaseModel):
    total: int
    by_status: dict[str, int]
    by_window: dict[str, int]
    by_provider: dict[str, int]
    avg_prepare_ms: int | None = None
    avg_match_score: float | None = None
    duplicate_count: int = 0
    failed_count: int = 0
    prepared_count: int = 0
    cv_used_count: int = 0
    cover_letter_used_count: int = 0
    note: str
    generated_at: datetime


class ApplicationsPerUserRow(BaseModel):
    id: UUID
    email: str
    application_count: int
    applications_by_status: dict[str, int] = Field(default_factory=dict)


class ApplicationsPerUserList(BaseModel):
    total: int
    limit: int
    offset: int
    users: list[ApplicationsPerUserRow]


class DocumentStats(BaseModel):
    cv_total: int
    cover_letter_total: int
    document_total: int
    cv_used_in_application: int
    cover_letter_used_in_application: int
    created_by_window: dict[str, dict[str, int]]
    generated_at: datetime


class JobsActivityStats(BaseModel):
    searches_by_window: dict[str, int] = Field(default_factory=dict)
    searching_users_by_window: dict[str, int] = Field(default_factory=dict)
    offer_views_by_window: dict[str, int] = Field(default_factory=dict)
    offers_saved_by_window: dict[str, int] = Field(default_factory=dict)
    total_saved_offers: int = 0
    generated_at: datetime


class ProviderStatRow(BaseModel):
    name: str
    enabled: bool
    state: str | None = None
    circuit_state: str
    runs: int = 0
    success: int = 0
    failure: int = 0
    success_rate: float | None = None
    error_rate: float | None = None
    consecutive_failures: int = 0
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_duration_ms: int | None = None
    avg_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    last_offer_count: int | None = None
    max_offers: int = 0
    total_offers_collected: int = 0
    offers_per_run: float | None = None
    empty_result_rate: float | None = None
    last_error: str | None = None
    health_note: str | None = None


class LlmConfigView(BaseModel):
    provider: str | None = None
    base_url: str
    model: str | None = None
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float
    max_output_tokens: int
    request_timeout: float
    max_calls_per_minute: int
    available_providers: list[str] = Field(default_factory=list)
    configured: bool
    # centralised routing (LOT 3) — request_type -> ["provider/model", ...]
    routing_enabled: bool = False
    routing: dict[str, list[str]] = Field(default_factory=dict)
    # circuit-breaker health (LOT 4) — {"breakers": {...}, "last_good": {...}}
    circuit: dict = Field(default_factory=dict)
    # deliberately NO api_key field


class LlmConfigUpdate(BaseModel):
    """Runtime LLM change — **no api_key field** (keys are environment secrets)."""
    provider: str | None = Field(default=None, max_length=32)   # -> LLM_PROVIDER
    model: str | None = Field(default=None, max_length=200)     # -> LLM_MODEL
    models_fallback: str | None = Field(default=None, max_length=1000)
    base_url: str | None = Field(default=None, max_length=500)  # -> LLM_BASE_URL
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)
    request_timeout: float | None = Field(default=None, ge=1, le=600)
    max_calls_per_minute: int | None = Field(default=None, ge=1, le=100000)

    def to_settings_patch(self) -> dict:
        m = {
            "LLM_PROVIDER": self.provider,
            "LLM_MODEL": self.model,
            "LLM_MODELS_FALLBACK": self.models_fallback,
            "LLM_BASE_URL": self.base_url,
            "LLM_TEMPERATURE": self.temperature,
            "LLM_MAX_OUTPUT_TOKENS": self.max_output_tokens,
            "LLM_REQUEST_TIMEOUT": self.request_timeout,
            "LLM_MAX_CALLS_PER_MINUTE": self.max_calls_per_minute,
        }
        return {k: v for k, v in m.items() if v is not None}


class LlmTestResult(BaseModel):
    provider: str
    model: str | None = None
    duration_ms: int
    ok: bool
    error: str | None = None


class BoardsOverviewRow(BaseModel):
    provider: str
    total: int
    enabled: int
    disabled: int
    last_test_ok: int


class DashboardResponse(BaseModel):
    usage: UsageOverview
    jobs: JobsActivityStats | None = None
    applications: ApplicationStats
    documents: DocumentStats
    providers: list[ProviderStatRow]
    boards: list[BoardsOverviewRow] = Field(default_factory=list)
    llm: LlmConfigView
    generated_at: datetime
