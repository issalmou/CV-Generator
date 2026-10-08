"""
CV Assistant — centralised application configuration.

One ``Settings`` object, loaded once from the environment (and an optional
``.env`` file). Every new subsystem — the database, JWT auth, the password
reset flow, e-mail delivery, MinIO object storage and the application
cache — reads its configuration from here instead of scattering
``os.getenv`` calls across the codebase.

Import-safe by design: no field raises while the module is imported, even
when a mandatory secret is missing. ``DATABASE_URL`` and
``JWT_SECRET_KEY`` default to ``""`` and are validated at application
startup (``main.lifespan``) so the test suite and tooling can import the
package without a full production environment.

``services/gemini_client.py`` keeps its own ``os.getenv("NVIDIA_API_KEY")``
lookup on purpose (documented exception — it must stay decoupled from this
module); the mirror field below exists only so the value shows up in one
place for documentation and ``/api/health``.
"""

from __future__ import annotations

import functools
import logging
import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_BUNDLED_BOARDS_DIR = _ROOT / "data" / "ats_boards"
_TOKEN_LINE = re.compile(r"^[^\s#]\S*$")   # a board token: no whitespace, not a comment

# Built-in LLM model fallback chain (used when neither LLM_MODEL nor
# LLM_MODELS_FALLBACK is set). NVIDIA NIM retires hosted models aggressively
# (HTTP 410 overnight) — hence a list. Verified live 2026-09-02.
DEFAULT_LLM_MODELS: tuple[str, ...] = (
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "openai/gpt-oss-20b",
)

# per-provider config — "OpenAI-compatible" is a transport, every backend is
# the same implementation with a different (base_url, api_key, model).
# tuple: (api_key_attr, model_attr, base_url_attr, default_base_url, default_model)
_LLM_BLOCKS: dict[str, tuple[str, str, str, str, str]] = {
    "nvidia": ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL",
               "https://integrate.api.nvidia.com/v1", ""),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
               "https://api.openai.com/v1", "gpt-4o-mini"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL",
               "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "mistral": ("MISTRAL_API_KEY", "MISTRAL_MODEL", "MISTRAL_BASE_URL",
                "https://api.mistral.ai/v1", "mistral-small-latest"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_BASE_URL",
             "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openai_compatible": ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_MODEL",
                          "OPENAI_COMPATIBLE_BASE_URL", "", ""),
    "custom": ("CUSTOM_LLM_API_KEY", "CUSTOM_LLM_MODEL", "CUSTOM_LLM_BASE_URL", "", ""),
}
LLM_PROVIDER_NAMES: tuple[str, ...] = tuple(_LLM_BLOCKS)

# ---------------------------------------------------------------------------
# Centralised LLM routing (v2.9 Phase 2b — LOT 3)
#
# request_type -> ordered [provider, model] chain. The first entry is the
# primary; the rest are fallbacks tried in order (LOT 4 adds the circuit
# breaker on top). ``temperature`` null -> the generic ``LLM_TEMPERATURE``.
#
# This is the ONLY place the task->model mapping lives. The 11 consumer
# modules never change — they call ``call_gemini(prompt, request_type=...)``
# and the routing happens inside ``gemini_client``.
#
# Provider reality this table is tuned for (re-pinged 2026-09-08 with the
# current .env keys):
#   groq/openai/gpt-oss-120b  -> OK, ~0.5s, strict json_schema  [PRIMARY everywhere]
#   gemini-3.5-flash-lite     -> OK, ~0.6s, strict json_schema  [1st fallback, structured]
#   gemini-flash-latest       -> OK, ~1.3s                       [1st fallback, prose]
#   groq/openai/gpt-oss-20b   -> OK (bake-off)                   [2nd fallback, structured]
#   mistral-*                 -> 429 rate-limited on the key     -> NOT in any chain
#   nvidia/nemotron-*         -> unstable baseline (long timeouts)-> NEVER an auto fallback
#                               (constraint #5) — available for manual/admin use only.
# ---------------------------------------------------------------------------

_GROQ_120 = ["groq", "openai/gpt-oss-120b"]
_GROQ_20 = ["groq", "openai/gpt-oss-20b"]
_GEM_LITE = ["gemini", "gemini-3.5-flash-lite"]   # most reliable Gemini tier (re-pinged 2026-09-08)
_GEM_FLASH = ["gemini", "gemini-3.5-flash"]        # `gemini-flash-latest` 503'd under load — use the pinned flash

_STRUCTURED_CHAIN = [_GROQ_120, _GEM_LITE, _GROQ_20]
_PROSE_CHAIN = [_GROQ_120, _GEM_LITE, _GEM_FLASH]

DEFAULT_LLM_ROUTING: dict[str, dict] = {
    # --- résumé extraction (strict JSON, deterministic) ---
    "resume_parse_experience":  {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "resume_parse_education":    {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "resume_parse_projects":     {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "resume_parse_skills":       {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "resume_parse_fallback":     {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    # --- ATS ---
    "ats_keyword_extraction":    {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "ats_content_optimization":  {"chain": [_GROQ_120, _GEM_LITE, _GROQ_20], "temperature": 0.2},
    "skill_categorisation":      {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    # --- cover letter ---
    "letter_job_company":        {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    "cover_letter":              {"chain": _PROSE_CHAIN, "temperature": 0.3},
    # --- job search / preferences ---
    "job_preference_extraction": {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    # --- profile analysis (Gemini kept as fallback, not primary — free-tier 429s) ---
    "profile_analysis":          {"chain": [_GROQ_120, _GEM_LITE, _GROQ_20], "temperature": 0.1},
    # --- conversational / edit agent (fast but faithful) ---
    "conversation_agent":        {"chain": [_GROQ_120, _GEM_FLASH, _GROQ_20], "temperature": 0.3},
    "agent_edit":                {"chain": _STRUCTURED_CHAIN, "temperature": 0.0},
    # --- default ---
    "generic":                   {"chain": _PROSE_CHAIN, "temperature": None},
}


@functools.lru_cache(maxsize=32)
def _read_token_file(path: str) -> tuple[str, ...]:
    """Read a board-list file — one token per line, ``#`` comments and blank
    lines ignored. Missing / unreadable file -> empty (never raises)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ()
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # allow "token  # trailing comment"
        token = line.split("#", 1)[0].strip()
        if token and _TOKEN_LINE.match(token):
            out.append(token)
    return tuple(out)


class Settings(BaseSettings):
    """Process-wide configuration, populated from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # LLM backend (OpenAI-compatible: NVIDIA NIM by default) + CORS
    #
    # ``services/gemini_client.py`` reads these through the ``llm_*`` helpers
    # below so the model / endpoint / limits can change via configuration
    # without touching business code. ``NVIDIA_API_KEY`` / ``NVIDIA_MODEL`` are
    # kept as aliases for the generic ``LLM_*`` vars (an existing deployment
    # keeps working unchanged).
    # ------------------------------------------------------------------
    # Which backend is active. One of: nvidia | openai | gemini | mistral |
    # groq | openai_compatible | custom. All speak the OpenAI Chat Completions
    # API — "provider" is just a (base_url, api_key, model) preset, never a
    # separate code path.
    LLM_PROVIDER: str = "nvidia"

    # Generic knobs — apply to every provider, and are the fall-back for the
    # per-provider key / URL / model below.
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""     # generic override; empty -> the active provider's own default
    LLM_MODEL: str = ""                       # "" -> built-in default chain
    LLM_MODELS_FALLBACK: str = ""             # CSV of extra model ids tried in order
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_OUTPUT_TOKENS: int = 4096
    LLM_REQUEST_TIMEOUT: float = 45.0
    LLM_MAX_CALLS_PER_MINUTE: int = 30

    # --- centralised routing (LOT 3) ---
    # When true, call_gemini routes each request_type through DEFAULT_LLM_ROUTING
    # (+ LLM_ROUTING_OVERRIDES). When false, every call uses the single
    # LLM_PROVIDER (legacy behaviour — kept for tests and emergency rollback).
    LLM_ROUTING_ENABLED: bool = True
    # JSON object merged over DEFAULT_LLM_ROUTING, e.g.
    #   {"cover_letter": {"chain": [["gemini","gemini-flash-latest"],["groq","openai/gpt-oss-120b"]]}}
    LLM_ROUTING_OVERRIDES: str = ""
    # CSV of provider names never used automatically (still buildable on demand).
    LLM_DISABLED_PROVIDERS: str = "mistral"
    # Per-request-type timeout (seconds). null entries -> LLM_REQUEST_TIMEOUT.
    LLM_ROUTING_TIMEOUT_OVERRIDES: str = ""

    # --- circuit breaker (LOT 4) — per (provider, model), in-process ---
    LLM_CIRCUIT_FAIL_THRESHOLD: int = 3       # consecutive failures -> breaker opens
    LLM_CIRCUIT_COOLDOWN_SECONDS: float = 60  # breaker stays open this long, then half-open
    LLM_CIRCUIT_ENABLED: bool = True

    # per-provider blocks (each falls back to the LLM_* generics above)
    NVIDIA_API_KEY: str = ""
    NVIDIA_MODEL: str = ""
    NVIDIA_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""
    OPENAI_BASE_URL: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = ""
    GEMINI_BASE_URL: str = ""
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = ""
    MISTRAL_BASE_URL: str = ""
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = ""
    GROQ_BASE_URL: str = ""
    OPENAI_COMPATIBLE_API_KEY: str = ""
    OPENAI_COMPATIBLE_MODEL: str = ""
    OPENAI_COMPATIBLE_BASE_URL: str = ""
    CUSTOM_LLM_API_KEY: str = ""
    CUSTOM_LLM_MODEL: str = ""
    CUSTOM_LLM_BASE_URL: str = ""

    ALLOWED_ORIGINS: str = "*"

    # ------------------------------------------------------------------
    # Database — PostgreSQL in production. No default: the app refuses to
    # start without it (checked in main.lifespan). The test suite sets a
    # SQLite URL via the environment before importing anything.
    # ------------------------------------------------------------------
    DATABASE_URL: str = ""
    # PostgreSQL only — kill a single statement after this many ms (0 = no cap).
    # Guards against a pathological query pinning a worker.
    DB_STATEMENT_TIMEOUT_MS: int = 15000

    # ------------------------------------------------------------------
    # JWT authentication
    # ------------------------------------------------------------------
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # CSV of e-mails promoted to superadmin on startup (existing accounts only;
    # never auto-demotes). The only way to mint the first admin.
    SUPERADMIN_EMAILS: str = ""

    # Privacy-safe usage-event log (login / search / job_view / … — counters +
    # ids only, never content). Powers the superadmin usage analytics.
    USAGE_EVENTS_ENABLED: bool = True
    USAGE_EVENT_RETENTION_DAYS: int = 400

    # ------------------------------------------------------------------
    # Dashboard cache (v2.8) — GET /api/dashboard and GET /api/admin/dashboard
    # share this one TTL, both through services/cache_service.py. Redis when
    # REDIS_URL is set, else in-memory; either way, best-effort (see
    # CacheService — a broken backend degrades to "no cache", never an error).
    # ------------------------------------------------------------------
    DASHBOARD_CACHE_TTL: int = 60

    # ------------------------------------------------------------------
    # Conversational memory (v2.8) — persistent conversations/messages.
    # SQL is the source of truth; Redis (via cache_service) only ever caches
    # the most recent CONVERSATION_HISTORY_LIMIT messages of one conversation.
    # ------------------------------------------------------------------
    CONVERSATION_HISTORY_LIMIT: int = 20          # messages sent to the LLM as context
    CONVERSATION_HISTORY_CACHE_TTL: int = 60      # seconds the recent-history cache lives

    # ------------------------------------------------------------------
    # Agent-orchestrated jobs (Phase 6). The conversation agent runs the
    # internal JobSearchService / JobApplicationService directly (no HTTP).
    # ------------------------------------------------------------------
    AGENT_JOB_SELECTION_LIMIT: int = 25          # top offers the agent keeps + can browse per search
    AGENT_JOB_PAGE_SIZE: int = 5                 # jobs shown per conversational "list" turn (Phase 6 finalisation)
    AGENT_PENDING_ACTION_TTL_MIN: int = 30       # a frozen apply proposal expires after this

    # ------------------------------------------------------------------
    # Latency + LLM-call profiling (v2.9 Phase 1) — services/profiling.py.
    # Records durations, token counts, model names and boolean flags only;
    # NEVER a prompt, a response, or any document content. ~microseconds of
    # overhead per span. When disabled, every span()/record call is a no-op.
    # ------------------------------------------------------------------
    PROFILING_ENABLED: bool = True
    PROFILING_RING_SIZE: int = 200   # last-N request profiles kept in memory for the debug endpoint

    # ------------------------------------------------------------------
    # CV PDF layout (v2.9 Phase 2b — LOT 6). The CV targets ONE page via
    # *adaptive* layout (margins -> spacing -> leading -> font size), never by
    # silently dropping experiences/bullets/projects. When the content genuinely
    # cannot fit above the readability floor, it flows to a 2nd page and the
    # response says so (fit_one_page=False) — no silent loss.
    # ------------------------------------------------------------------
    CV_MIN_BODY_FONT_PT: float = 8.0        # body text never smaller than this
    CV_MIN_AUX_FONT_PT: float = 6.8         # contact line / tech labels floor
    CV_MIN_MARGIN_MM: float = 9.0           # margins may shrink to here to gain space
    CV_SUMMARY_HARD_CAP: int = 900          # LLM summary sanity cap (logged if hit)
    CV_PROJECT_DESC_HARD_CAP: int = 400     # LLM project-desc sanity cap (logged if hit)

    # ------------------------------------------------------------------
    # Cover-letter cache (LOT 7). Key = hash(cv_profile + job_description +
    # language + LETTER_PROMPT_VERSION). Bump the version to invalidate every
    # cached letter at once when the letter prompt / layout changes.
    # ------------------------------------------------------------------
    LETTER_PROMPT_VERSION: int = 2          # v2 = LOT 1 anti-fabrication rewrite
    LETTER_CACHE_TTL: int = 86400           # seconds a rendered letter stays cached

    @property
    def superadmin_emails_list(self) -> list[str]:
        return [e.strip().lower() for e in self.SUPERADMIN_EMAILS.split(",") if e.strip()]

    # ------------------------------------------------------------------
    # Password reset (verification-code flow)
    # ------------------------------------------------------------------
    PASSWORD_RESET_CODE_EXPIRE_MINUTES: int = 15
    PASSWORD_RESET_MAX_ATTEMPTS: int = 5
    # When true, POST /api/auth/forgot-password echoes the generated code
    # in its response body (local dev + tests only — never in production).
    PASSWORD_RESET_DEV_MODE: bool = False

    # ------------------------------------------------------------------
    # Account reactivation key (deactivated-account recovery)
    # ------------------------------------------------------------------
    # 8-digit one-time key, SHA-256 at rest, single-use, attempt-limited.
    REACTIVATION_CODE_EXPIRE_MINUTES: int = 30
    REACTIVATION_MAX_ATTEMPTS: int = 5
    # When true, POST /api/auth/request-reactivation echoes the key in its
    # response body (local dev + tests only — never in production).
    REACTIVATION_DEV_MODE: bool = False

    # ------------------------------------------------------------------
    # E-mail delivery — Resend HTTP API → SMTP → dev-mode (log only).
    # ------------------------------------------------------------------
    RESEND_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "no-reply@cv-generator.local"

    # ------------------------------------------------------------------
    # MinIO object storage — every generated PDF lives here, never on disk.
    # ------------------------------------------------------------------
    MINIO_ENDPOINT: str = ""
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "cv-files"
    MINIO_SECURE: bool = False
    MINIO_URL_EXPIRE_SECONDS: int = 3600
    # The host a BROWSER can reach the object store at (e.g. "cdn.example.com"
    # or "minio.example.com"). ``MINIO_ENDPOINT`` above is the host the API
    # uploads/downloads through — inside Docker that is "minio:9000", which a
    # browser cannot resolve. When this is set, presigned download URLs are
    # generated against it. Empty -> presign against MINIO_ENDPOINT (dev).
    MINIO_PUBLIC_ENDPOINT: str = ""
    MINIO_PUBLIC_SECURE: bool = True
    # Bucket region — set so the URL-signing client never does a live
    # get_bucket_location lookup (it can't reach the public host from inside
    # the network). MinIO's default is "us-east-1".
    MINIO_REGION: str = "us-east-1"

    # ------------------------------------------------------------------
    # Application cache — Redis when REDIS_URL is set, else in-memory.
    # Sits ABOVE the gemini_client prompt cache (which is untouched).
    # ------------------------------------------------------------------
    REDIS_URL: str = ""
    CACHE_TTL: int = 3600
    # Bump to invalidate every cached entry at once (key namespace prefix).
    CACHE_VERSION: int = 1

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB

    # ------------------------------------------------------------------
    # Job & internship search — multi-provider aggregation.
    # Every provider lives in services/providers/; the search service holds
    # no platform-specific logic. Adzuna self-disables without credentials.
    # ------------------------------------------------------------------
    JOBS_ENABLED_PROVIDERS: str = (
        "linkedin,indeed,arbeitnow,weworkremotely,hackernews,"
        "remotive,jobicy,remoteok,himalayas,adzuna,"
        "greenhouse,lever,career_pages,ashby,"
        "workday,oracle_hcm,smartrecruiters,rippling,phenom,talentbrew"
    )
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""

    JOB_SEARCH_CACHE_TTL: int = 900          # seconds a search response is cached
    JOB_FRESH_TTL_HOURS: int = 24            # verified within this -> "fresh"
    JOB_STALE_TTL_DAYS: int = 14             # older & unverifiable -> "expired"
    JOB_DEFAULT_EXPIRY_DAYS: int = 30        # when the source gives no validThrough
    JOB_SEARCH_MAX_RESULTS: int = 60         # hard cap on the aggregated result set
    JOB_DEDUP_TITLE_RATIO: float = 0.90      # title similarity to merge same-company offers

    JOB_PROVIDER_TIMEOUT: float = 10.0       # per HTTP request
    JOB_PROVIDER_MAX_PAGES: int = 3
    JOB_PROVIDER_MAX_RESULTS: int = 50       # per provider

    # Fan-out control (services/jobs/search_service). Providers run concurrently
    # in a SHARED, process-wide thread pool; the whole fan-out is bounded by ONE
    # wall-clock deadline so a hung provider costs the deadline once, not once
    # per hung provider.
    #   * JOB_SEARCH_GLOBAL_CONCURRENCY caps provider calls across ALL
    #     simultaneous searches (multi-user safety — no thread/connection blow-up).
    #   * JOB_SEARCH_MAX_CONCURRENCY caps provider calls within ONE search.
    JOB_SEARCH_GLOBAL_CONCURRENCY: int = 32  # shared worker pool size (all searches)
    JOB_SEARCH_MAX_CONCURRENCY: int = 20     # concurrent provider calls per search
    JOB_SEARCH_DEADLINE: float = 45.0        # seconds for the whole provider fan-out
    JOB_PROVIDER_REQUEST_DELAY: float = 1.0  # min seconds between requests to one host
    JOB_PROVIDER_USER_AGENT: str = (
        "CVGeneratorJobBot/1.0 (+https://github.com/cv-generator; contact: admin@cv-generator.local)"
    )
    JOB_HTTP_MAX_BYTES: int = 3_000_000
    JOB_HTTP_MAX_REDIRECTS: int = 3
    # A public ATS board's full-listing JSON legitimately runs larger than an
    # HTML page; this per-call cap applies only to the ATS board-list fetch
    # (still streamed + enforced). Keeps the strict 3 MB default for everything else.
    ATS_HTTP_MAX_BYTES: int = 12_000_000

    JOB_CIRCUIT_FAIL_THRESHOLD: int = 3      # consecutive failures -> breaker opens
    JOB_CIRCUIT_COOLDOWN_SECONDS: int = 600  # breaker stays open this long

    # --- Asynchronous job search (Phase 4) ---
    # POST /api/jobs/search with `wait: false` returns 202 + a search_id and
    # runs the ~20-provider fan-out in a background worker; the frontend polls
    # GET /api/jobs/search/{search_id}. The SearchJob state lives in the app
    # cache (Redis when configured, else in-memory) — no new infrastructure.
    JOB_SEARCH_ASYNC_WORKERS: int = 4        # concurrent background searches
    JOB_SEARCH_JOB_TTL: int = 3600           # seconds a SearchJob record is kept
    JOB_SEARCH_ASYNC_STALE_SECONDS: int = 120  # running + no heartbeat this long -> failed

    # "provider looks broken" heuristic (GET /api/jobs/sources -> health_note):
    # a provider that has returned >= MIN_OFFERS at least once but then N runs
    # in a row come back `available` with **zero** offers is probably hit by an
    # upstream API / markup change — flagged even though nothing "errors".
    PROVIDER_STALE_MIN_OFFERS: int = 5
    PROVIDER_STALE_EMPTY_RUNS: int = 6

    # LinkedIn *public* provider (guest endpoints only — no login/cookies/browser).
    LINKEDIN_MAX_PAGES: int = 5              # guest search pages (25 cards each)
    LINKEDIN_FRESHNESS_DAYS: int = 30        # f_TPR — only list postings from the last N days (0 = no filter)
    LINKEDIN_ENRICH_MAX: int = 12            # per-offer guest enrichment fetches per search

    # ------------------------------------------------------------------
    # Browser automation infrastructure (BrowserSessionManager)
    #
    # Generic headless-Chromium config for browser-based providers. Off by
    # default; a browser provider self-disables when this is False or when
    # Selenium/Chromium is not installed. It renders PUBLIC pages only —
    # no login, no credentials, no LinkedIn.
    # ------------------------------------------------------------------
    BROWSER_ENABLED: bool = False
    BROWSER_HEADLESS: bool = True
    BROWSER_BINARY: str = ""                 # Chromium/Chrome binary override
    BROWSER_DRIVER_PATH: str = ""            # chromedriver override
    BROWSER_USER_DATA_DIR: str = "/tmp/cv-generator-browser"  # cache dir (not a login profile)
    BROWSER_PAGE_LOAD_TIMEOUT: float = 30.0
    BROWSER_SCRIPT_TIMEOUT: float = 30.0
    BROWSER_IMPLICIT_WAIT: float = 0.0
    BROWSER_MAX_PAGES_PER_SESSION: int = 40  # recycle the driver after N renders
    BROWSER_IDLE_SHUTDOWN_SECONDS: int = 300  # quit the driver after this idle time

    # ------------------------------------------------------------------
    # ATS job boards (public JSON APIs — no browser, no key).
    #
    # For provider X the effective list is (deduped, capped at ATS_MAX_BOARDS):
    #   1. the bundled data/ats_boards/<x>.txt   (unless ATS_USE_BUNDLED_BOARDS=false)
    #   2. the file at X_BOARDS_FILE              (operator's own list, optional)
    #   3. the X_BOARDS env var                   (CSV — operator additions)
    # Empty list -> the provider self-disables. A 404 board is silently skipped;
    # only *every* board failing marks the provider unavailable.
    # ------------------------------------------------------------------
    ATS_USE_BUNDLED_BOARDS: bool = True
    ATS_MAX_BOARDS: int = 60          # per provider per search — bounds the fan-out
    ATS_BOARD_OVERLAY_TTL: int = 60  # seconds the superadmin DB board overlay is cached

    GREENHOUSE_BOARDS: str = ""
    GREENHOUSE_BOARDS_FILE: str = ""
    LEVER_BOARDS: str = ""
    LEVER_BOARDS_FILE: str = ""
    ASHBY_BOARDS: str = ""
    ASHBY_BOARDS_FILE: str = ""
    SMARTRECRUITERS_BOARDS: str = ""          # slugs — CASE-SENSITIVE
    SMARTRECRUITERS_BOARDS_FILE: str = ""
    RIPPLING_BOARDS: str = ""
    RIPPLING_BOARDS_FILE: str = ""
    # Tenant-specific — a full board URL / payload per company. No bundled default.
    WORKDAY_TENANTS: str = ""                 # full board URLs
    WORKDAY_TENANTS_FILE: str = ""
    ORACLE_HCM_SITES: str = ""                # full CandidateExperience URLs (*.oraclecloud.com)
    ORACLE_HCM_SITES_FILE: str = ""
    TALENTBREW_BOARDS: str = ""               # full ".../search-jobs/results?..." URLs
    TALENTBREW_BOARDS_FILE: str = ""
    # Phenom has no shared API: a JSON array of
    #   {"company","endpoint":"https://.../widgets","payload":{...}}
    PHENOM_BOARDS: str = ""
    PHENOM_BOARDS_FILE: str = ""              # a file whose whole content is that JSON array
    # Public company career-page URLs the browser provider renders (JS pages
    # with embedded schema.org JobPosting). CSV; empty -> career_pages disabled.
    CAREER_PAGE_URLS: str = ""

    # Bounded per-job description enrichment for the ATS whose *board list*
    # does not include the description (workday / oracle_hcm / smartrecruiters).
    # Best-effort: a failing detail endpoint never fails the search.
    ATS_ENRICH_DESCRIPTIONS: bool = True
    ATS_ENRICH_MAX: int = 20                 # detail fetches per provider per search

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def allowed_origins_list(self) -> list[str]:
        """``ALLOWED_ORIGINS`` split into a list for the CORS middleware."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()] or ["*"]

    @property
    def enabled_providers_list(self) -> list[str]:
        """``JOBS_ENABLED_PROVIDERS`` as a clean lower-case list."""
        return [p.strip().lower() for p in self.JOBS_ENABLED_PROVIDERS.split(",") if p.strip()]

    @staticmethod
    def _csv(value: str) -> list[str]:
        """A comma / whitespace / newline separated string -> clean list."""
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]

    def _board_list(self, bundled_name: str, env_csv: str, env_file: str,
                    *, lower: bool = True) -> list[str]:
        """Merge bundled file + operator file + env CSV for one ATS.

        Order-preserving, de-duplicated **case-insensitively**, capped at
        ``ATS_MAX_BOARDS``. Never raises — a missing file contributes nothing."""
        parts: list[str] = []
        if self.ATS_USE_BUNDLED_BOARDS:
            parts.extend(_read_token_file(str(_BUNDLED_BOARDS_DIR / f"{bundled_name}.txt")))
        if env_file.strip():
            parts.extend(_read_token_file(env_file.strip()))
        parts.extend(self._csv(env_csv))

        out: list[str] = []
        seen: set[str] = set()
        for token in parts:
            t = token.strip()
            if not t:
                continue
            norm = t.lower() if lower else t
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(norm)
            if len(out) >= self.ATS_MAX_BOARDS:
                break
        return out

    @property
    def greenhouse_boards_list(self) -> list[str]:
        return self._board_list("greenhouse", self.GREENHOUSE_BOARDS, self.GREENHOUSE_BOARDS_FILE)

    @property
    def lever_boards_list(self) -> list[str]:
        return self._board_list("lever", self.LEVER_BOARDS, self.LEVER_BOARDS_FILE)

    @property
    def ashby_boards_list(self) -> list[str]:
        return self._board_list("ashby", self.ASHBY_BOARDS, self.ASHBY_BOARDS_FILE)

    @property
    def smartrecruiters_boards_list(self) -> list[str]:
        """Company slugs — **case-sensitive** (SmartRecruiters slugs like ``Visa1``)."""
        return self._board_list("smartrecruiters", self.SMARTRECRUITERS_BOARDS,
                                self.SMARTRECRUITERS_BOARDS_FILE, lower=False)

    @property
    def rippling_boards_list(self) -> list[str]:
        return self._board_list("rippling", self.RIPPLING_BOARDS, self.RIPPLING_BOARDS_FILE)

    @property
    def workday_tenants_list(self) -> list[str]:
        """Full board URLs (case preserved). No bundled default — tenant-specific."""
        return self._board_list("__none__", self.WORKDAY_TENANTS,
                                self.WORKDAY_TENANTS_FILE, lower=False)

    @property
    def oracle_hcm_sites_list(self) -> list[str]:
        """Full CandidateExperience URLs (case preserved). No bundled default."""
        return self._board_list("__none__", self.ORACLE_HCM_SITES,
                                self.ORACLE_HCM_SITES_FILE, lower=False)

    @property
    def talentbrew_boards_list(self) -> list[str]:
        """Full '.../search-jobs/results?...' URLs (case preserved). No bundled default."""
        return self._board_list("__none__", self.TALENTBREW_BOARDS,
                                self.TALENTBREW_BOARDS_FILE, lower=False)

    @property
    def phenom_boards_list(self) -> list[dict]:
        """JSON array of {company, endpoint, payload} — from ``PHENOM_BOARDS`` or,
        if empty, the file at ``PHENOM_BOARDS_FILE``. A malformed value disables
        the provider rather than raising."""
        import json
        raw = (self.PHENOM_BOARDS or "").strip()
        if not raw and self.PHENOM_BOARDS_FILE.strip():
            try:
                raw = Path(self.PHENOM_BOARDS_FILE.strip()).read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                raw = ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [
            e for e in data
            if isinstance(e, dict) and str(e.get("endpoint") or "").strip()
        ][: self.ATS_MAX_BOARDS]

    @property
    def career_page_urls_list(self) -> list[str]:
        """``CAREER_PAGE_URLS``; empty -> the ``career_pages`` provider self-disables."""
        return self._csv(self.CAREER_PAGE_URLS)

    # ------------------------------------------------------------------
    # LLM helpers  (provider-aware — the active provider is ``LLM_PROVIDER``)
    # ------------------------------------------------------------------

    @property
    def llm_provider(self) -> str:
        p = (self.LLM_PROVIDER or "nvidia").strip().lower()
        return p if p in _LLM_BLOCKS else "nvidia"

    def _llm_block(self) -> tuple[str, str, str, str, str]:
        return _LLM_BLOCKS[self.llm_provider]

    @property
    def llm_api_key(self) -> str:
        """The active provider's API key. Never logged, never returned by any route."""
        key_attr = self._llm_block()[0]
        return getattr(self, key_attr, "") or self.LLM_API_KEY

    @property
    def llm_base_url(self) -> str:
        _k, _m, url_attr, default_url, _dm = self._llm_block()
        return getattr(self, url_attr, "") or self.LLM_BASE_URL or default_url

    @property
    def llm_models(self) -> list[str]:
        """Model fallback chain for the active provider."""
        _k, model_attr, _u, _du, default_model = self._llm_block()
        primary = (getattr(self, model_attr, "") or self.LLM_MODEL or default_model).strip()
        extra = self._csv(self.LLM_MODELS_FALLBACK)
        chosen = [m for m in [primary, *extra] if m]
        out: list[str] = []
        for m in chosen or DEFAULT_LLM_MODELS:
            if m not in out:
                out.append(m)
        return out

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def llm_disabled_providers_list(self) -> list[str]:
        return [p.strip().lower() for p in self.LLM_DISABLED_PROVIDERS.split(",") if p.strip()]

    def provider_has_key(self, name: str) -> bool:
        """True when provider ``name`` has an API key configured (per-block or
        the generic ``LLM_API_KEY``)."""
        key = (name or "").strip().lower()
        if key not in _LLM_BLOCKS:
            return False
        return bool(getattr(self, _LLM_BLOCKS[key][0], "") or self.LLM_API_KEY)

    def llm_public_config(self) -> dict:
        """Safe-to-expose LLM settings for the admin dashboard — **no key**."""
        models = self.llm_models
        return {
            "provider": self.llm_provider,
            "base_url": self.llm_base_url,
            "model": models[0] if models else None,
            "fallback_models": models[1:],
            "temperature": self.LLM_TEMPERATURE,
            "max_output_tokens": self.LLM_MAX_OUTPUT_TOKENS,
            "request_timeout": self.LLM_REQUEST_TIMEOUT,
            "max_calls_per_minute": self.LLM_MAX_CALLS_PER_MINUTE,
            "available_providers": list(_LLM_BLOCKS),
            "configured": self.llm_configured,
            "routing_enabled": self.LLM_ROUTING_ENABLED,
            "routing": self._routing_public(),
            "circuit": self._circuit_public(),
        }

    def _routing_public(self) -> dict:
        """request_type -> ["provider/model", ...] effective chain, no keys.
        Import is local so config stays import-safe."""
        try:
            from services.llm.routing import as_public_dict
            return as_public_dict()
        except Exception:  # noqa: BLE001 — never let this break /api/health
            return {}

    def _circuit_public(self) -> dict:
        try:
            from services.llm.circuit import snapshot
            return snapshot()
        except Exception:  # noqa: BLE001
            return {}

    @property
    def adzuna_configured(self) -> bool:
        return bool(self.ADZUNA_APP_ID and self.ADZUNA_APP_KEY)

    @property
    def database_is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


settings = Settings()
