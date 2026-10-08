[![CI Pipeline](https://github.com/issalmou/CV-Generator/actions/workflows/ci.yml/badge.svg)](https://github.com/issalmou/CV-Generator/actions/workflows/ci.yml)
# CV Assistant â€” CV Generation Service

Backend API that turns a candidate's raw rÃ©sumÃ© into an **ATS-optimised,
bilingual (FR / EN) PDF CV** â€” with a mandatory human-in-the-loop
validation step in between, so nothing extracted from a document is ever
shipped to a candidate without their review.

Built with **FastAPI**, **Pydantic v2**, **ReportLab** (PDF), **PyMuPDF**
+ **python-docx** (file parsing), and an OpenAI-compatible LLM backend
(**NVIDIA NIM**) centralised behind a single client module.

Since **v2.0** the service also ships:

- **User authentication** â€” signup / signin / `/me` with JWT bearer
  tokens, plus a two-step verification-code password reset. Auth is
  **mandatory on every CV endpoint**; only `/api/health` and
  `/api/auth/*` are public.
- **PostgreSQL** persistence (SQLAlchemy 2.0) â€” accounts and the metadata
  of every generated document. `DATABASE_URL` is **required**; the app
  refuses to start without it.
- **MinIO object storage** â€” every generated PDF (CV *and* cover letter)
  is streamed to MinIO, never to local disk. The generation endpoints
  return **JSON + a short-lived presigned download URL** instead of a PDF
  stream.
- An **application-level cache** (in-memory, or Redis when `REDIS_URL` is
  set) sitting *above* the `gemini_client` prompt cache: an identical
  extraction / generation request skips prompt building, ATS scoring and
  PDF rendering entirely.

---

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Running the API](#running-the-api)
- [API reference](#api-reference)
- [Workflow 1 â€” Extraction (`POST /api/extract-cv`)](#workflow-1--extraction-post-apiextract-cv)
- [Workflow 2 â€” Generation (`POST /api/generate-cv`)](#workflow-2--generation-post-apigenerate-cv)
- [Workflow 3 â€” Cover letter (`POST /api/generate-letter`)](#workflow-3--cover-letter-post-apigenerate-letter)
- [Workflow 4 â€” Job & internship search (`/api/jobs/*`)](#workflow-4--job--internship-search-apijobs)
- [Multilingual support (FR / EN)](#multilingual-support-fr--en)
- [ATS optimisation](#ats-optimisation)
- [Anti-hallucination contract](#anti-hallucination-contract)
- [Testing](#testing)
- [Known limitations](#known-limitations)
- [Project structure](#project-structure)

---

## Overview

The service has **three pipelines** that share one LLM client
(`services/gemini_client.py`) but are otherwise fully decoupled:

1. **Extraction** â€” `POST /api/extract-cv`
   Takes a rÃ©sumÃ© **file** (PDF or DOCX) and an optional `language`, runs
   it through a local-first pipeline (regex + heuristics for contact,
   languages, certifications; four **section-scoped** LLM calls â€”
   experience, education, projects, skills â€” run **concurrently**, never
   the whole rÃ©sumÃ© at once), and returns a structured,
   **never-fabricated** profile plus per-section confidence scores and
   validation findings. The frontend renders this as an editable review
   form.

2. **Generation** â€” `POST /api/generate-cv` (and `POST /api/optimize-existing-cv`)
   Takes a **validated** `cv_profile` plus an optional job description,
   runs ATS keyword matching and content optimisation, and **streams back
   a single-page PDF** â€” in French or English.

3. **Cover letter** â€” `POST /api/generate-letter`
   Takes a validated `cv_profile` + a job description and **streams back a
   one-page cover-letter PDF** â€” in French or English.

Nothing is ever written to the server's disk. The pipelines connect
**only through the frontend**:

```
upload rÃ©sumÃ© â†’ /api/extract-cv â†’ draft profile
             â†’ user reviews / edits in a form
             â†’ confirmed profile â†’ /api/generate-cv     â†’ CV PDF
                                 â†’ /api/generate-letter  â†’ letter PDF
```

The backend never auto-promotes extracted data straight into a generated
document.

---

## Architecture

```
        PDF / DOCX â”€â”€â–¶ â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                       â”‚  POST /api/extract-cv â”‚
     (+ language?)     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                   â”‚  ResumeParserPipeline
                                   â”‚  local regex  +  â‰¤ 4 section calls
                                   â”‚  (+ â‰¤ 1 whole-rÃ©sumÃ© fallback, rare)
                                   â–¼
                    ExtractCVResponse  â”€â”€ JSON â”€â”€â–¶  frontend review form
                                   â”‚
                          (human review / edit)
                                   â–¼
   cv_profile + job_description + language
         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
         â”‚  POST /api/generate-cv      â”‚  POST /api/generate-letter â”‚
         â”‚  POST /api/optimize-        â”‚                           â”‚
         â”‚       existing-cv          â”‚                           â”‚
         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                      â”‚ ProfileAnalyzerâ†’ATSOptimizerâ”‚ JobCompanyParser
                      â”‚ â†’CVGeneratorâ†’PDFGenerator    â”‚ â†’LetterGeneratorâ†’LetterPDFGenerator
                      â–¼                              â–¼
        PDF bytes â†’ MinIO (cv/â€¦ , letter/â€¦)  â€”  JSON + presigned URL to the client
                      â”‚                              â”‚
              GeneratedCV row                 GeneratedLetter row   (owner = caller)
```

All auth-protected routes sit behind `Depends(get_current_user)`; the
cache (`services/cache_service.py`) wraps the LLM-bound pipeline steps in
`services/generation_service.py`.

**Every LLM call â€” in all three pipelines â€” goes through
`services/gemini_client.py`.** It provides SHA-256-keyed response caching,
a 30 calls/minute rate limiter, an automatic model-fallback chain, a
45-second per-request timeout and call statistics. No other module is
allowed to instantiate an LLM client. (The module is named
`gemini_client` for historical reasons; it currently targets NVIDIA
NIM-hosted open models.)

### Extraction pipeline (local-first)

| Stage | Module | LLM calls |
|---|---|---|
| PDF â†’ raw text **+ link annotations** / DOCX | `services/resume_text_extractor.py`, `services/pdf_layout_reader.py` | 0 |
| Per-page layout detection (single- / two-column, full-width header) + reading-order reconstruction | `services/column_detector.py` | 0 |
| Classify URLs (annotation URLs win) â†’ linkedin / github / portfolio / email | `services/link_extractor.py` | 0 |
| Line cleanup (rejoin wrapped lines, split list blocks) | `services/text_cleaner.py` | 0 |
| Raw text â†’ named sections + language detection | `services/section_splitter.py` | 0 |
| Contact (name, email, phone incl. `+CC`, **URLs from the link annotation**, address â€” labelled *or* an unlabelled "City, Country" on the contact bar, nationality) | `services/contact_extractor.py` | 0 |
| `professional_summary` / `interests` / `personal_qualities` cleanup | `services/resume_parser_pipeline.py` | 0 |
| Certifications + languages (levels kept verbatim) | `services/local_section_extractor.py` | 0 |
| Experience (â†’ `period`, a duration) | `services/experience_parser.py` | â‰¤ 1 |
| Education (`degree`, `start_date` / `end_date` all kept **verbatim** â€” `M.Sc.` / `B.Sc. (Excellence)` never normalised or translated) | `services/education_parser.py` | â‰¤ 1 |
| Projects | `services/project_parser.py` | â‰¤ 1 |
| Skills â†’ **flat `list[str]`** (no categories; line-cut repair; local regex fallback) | `services/skills_parser.py` | â‰¤ 1 |
| **Source grounding** â€” every LLM value checked back against its own section text; unsupported items removed; a project's repo/demo URL attached from the projects text or a PDF annotation | `services/source_grounding.py` | 0 |
| Date resolution â€” education kept verbatim; experience `period` computed as a duration | `services/date_parser.py` | 0 |
| Whole-rÃ©sumÃ© fallback (Level 5, conditional) | `services/resume_structurer.py` | â‰¤ 1 |
| Validation + de-duplication + tech splitting | `services/resume_validator.py` | 0 |
| Confidence scoring (0â€“100 per section) | `services/confidence_scorer.py` | 0 |
| Orchestration | `services/resume_parser_pipeline.py` | â€” |

**At most 4 LLM calls** for a normal rÃ©sumÃ© (one per non-empty structured
section: experience / education / projects / skills), regardless of
length â€” plus at most **1** extra whole-rÃ©sumÃ© fallback call in the rare
case where a section header was detected but its body could not be
recovered (unusual layouts). **Every call receives one section's text
only**, so a date or a technology from one section can never bleed into
another.

The four section calls are **independent and run concurrently** (a
`ThreadPoolExecutor` in `ResumeParserPipeline`; `gemini_client` is
thread-safe), so extraction wall-time is ~one LLM round-trip instead of
four â€” roughly a 4Ã— speed-up of the LLM-bound phase.

### Generation pipeline (`/api/generate-cv`, `/api/optimize-existing-cv`)

| Stage | Module | LLM calls |
|---|---|---|
| Profile analysis (summary / seniority / key skills) | `services/profile_analyzer.py` | 1 |
| Keyword extraction from the job description | `services/ats_optimizer.py` | 1 (only if a JD is given) |
| Match scoring | `services/ats_optimizer.py` | 0 |
| Content optimisation (summary / bullets, language-aware) | `services/ats_optimizer.py` | 0 or 1 |
| Skill categorisation (only if profile has no categories) | `services/cv_generator.py` | 0 or 1 |
| PDF rendering straight to bytes | `services/pdf_generator.py` | 0 |

### Cover-letter pipeline (`/api/generate-letter`)

| Stage | Module | LLM calls |
|---|---|---|
| Parse company / position / recipient from the job description | `services/job_company_parser.py` | 1 (0 if the JD is empty) |
| Write the letter body, in the requested language | `services/letter_generator.py` | 1 |
| Render the letter PDF straight to bytes | `services/letter_pdf_generator.py` | 0 |

---

## Installation

Requires **Python 3.10+** (the codebase uses `str | None` unions and
built-in generics).

### With conda

```bash
conda create -n cv-api python=3.10
conda activate cv-api
pip install -r requirements.txt
pip install -r requirements-dev.txt   # for the test suite
```

### With venv

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Runtime dependencies (`requirements.txt`)

| Package | Purpose |
|---|---|
| `fastapi`, `uvicorn[standard]` | web framework + ASGI server |
| `pydantic` | request / response models & validation |
| `python-multipart` | multipart file upload parsing |
| `python-dotenv` | `.env` loading |
| `openai` | OpenAI-compatible client for NVIDIA NIM (the **only** LLM client) |
| `reportlab` | PDF rendering â€” CV **and** cover letter |
| `PyMuPDF` | layout-aware PDF text extraction |
| `python-docx` | DOCX text extraction (document-order paragraphs + tables) |

The cover-letter feature (ported from a separate service) adds **no new
dependency**: it drops that service's `google-genai` client and routes
every call through the existing `services/gemini_client.py`.

---

## Environment variables

Copy `.env.example` to `.env` and fill it in. The full list:

| Variable | Required | Description |
|---|---|---|
| `LLM_PROVIDER` | no | Active LLM backend: `nvidia` (default) / `openai` / `gemini` / `mistral` / `groq` / `openai_compatible` / `custom`. All 7 are the **same** OpenAI-compatible implementation with a different `(base_url, api_key, model)` preset â€” "provider" is never a separate code path. |
| `NVIDIA_API_KEY` | **yes*** | *At least one provider's key is required.* NVIDIA NIM key. <https://build.nvidia.com/>. |
| `<PROVIDER>_API_KEY` / `_MODEL` / `_BASE_URL` | no | Per-provider block for `NVIDIA` / `OPENAI` / `GEMINI` / `MISTRAL` / `GROQ` / `OPENAI_COMPATIBLE` / `CUSTOM_LLM`. The active provider's own vars win over the generic `LLM_*`; an unset `_BASE_URL` falls back to that provider's built-in default. |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_MODELS_FALLBACK` | no | Generic fallback for the per-provider blocks. `LLM_MODEL` empty â‡’ the built-in chain. `LLM_MODELS_FALLBACK` is a CSV tried in order. |
| `LLM_TEMPERATURE` / `LLM_MAX_OUTPUT_TOKENS` / `LLM_REQUEST_TIMEOUT` / `LLM_MAX_CALLS_PER_MINUTE` | no | `0.3` / `4096` / `45.0` / `30`. Also changeable at runtime via `PATCH /api/admin/llm` (persisted, health-checked, auto-rollback). The key is **never** logged or returned by any route. |
| `USAGE_EVENTS_ENABLED` / `USAGE_EVENT_RETENTION_DAYS` | no | Privacy-safe usage-event log (`true` / `400`). Counters + ids only, pruned on startup. |
| `DASHBOARD_CACHE_TTL` | no | Seconds `GET /api/dashboard` and `GET /api/admin/dashboard` cache for, via `services/cache_service.py` (Redis when `REDIS_URL` is set, else in-memory). Best-effort â€” a broken cache backend degrades to always-fresh SQL, never an error. Default `60`. |
| `CONVERSATION_HISTORY_LIMIT` / `CONVERSATION_HISTORY_CACHE_TTL` | no | How many of a conversation's most recent messages are sent to the LLM as context (default `20`), and how long that bounded history is cached (default `60`). SQL is always the source of truth. |
| `JOB_SEARCH_GLOBAL_CONCURRENCY` | no | Shared worker pool sizing the provider fan-out across **all** simultaneous searches. Default `32`. |
| `DATABASE_URL` | **yes** | PostgreSQL DSN, e.g. `postgresql://cv:cv@localhost:5432/cv_generator`. The app refuses to start without it. Tables are created on startup; new *additive* columns/indexes are applied automatically (`_ensure_additive_columns`), still no Alembic. |
| `JWT_SECRET_KEY` | **yes** | Long random string used to sign access tokens. `python -c "import secrets;print(secrets.token_urlsafe(48))"`. |
| `SUPERADMIN_EMAILS` | no | CSV of e-mails promoted to superadmin on startup (existing accounts only, never demotes). Grants `/api/admin/*`. |
| `ALLOWED_ORIGINS` | no | Comma-separated CORS origins. Defaults to `*`. |
| `JWT_ALGORITHM` / `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | no | `HS256` / `60` by default. |
| `PASSWORD_RESET_CODE_EXPIRE_MINUTES` | no | Reset-code lifetime. Default `15`. |
| `PASSWORD_RESET_MAX_ATTEMPTS` | no | Wrong-code tries before a code is burned. Default `5`. |
| `PASSWORD_RESET_DEV_MODE` | no | When `true`, `POST /api/auth/forgot-password` returns the code in its body (dev / tests only). Default `false`. |
| `RESEND_API_KEY` | no | If set, reset codes are e-mailed via the Resend HTTP API. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | no | SMTP fallback (STARTTLS) used when `RESEND_API_KEY` is unset. |
| `EMAIL_FROM` | no | From address for reset e-mails. Default `no-reply@cv-generator.local`. |
| `MINIO_ENDPOINT` | no* | `host:port` the **API** uploads/downloads through. *When unset, the generation routes return `503`. |
| `MINIO_PUBLIC_ENDPOINT` | no | Host a **browser** can reach the object store at â€” presigned download URLs are signed against this. In Docker `MINIO_ENDPOINT` is `minio:9000` (not browser-resolvable), so set this to `localhost:9000` (dev) or your public CDN/S3 domain (prod). Empty â‡’ signs against `MINIO_ENDPOINT`. |
| `MINIO_PUBLIC_SECURE` | no | `true` for `https://` presigned URLs. Default `true`. |
| `MINIO_REGION` | no | Bucket region for the URL signer (avoids a live `get_bucket_location` call). Default `us-east-1`. |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | no | MinIO credentials. |
| `MINIO_BUCKET` | no | Bucket for the PDFs. Default `cv-files` (created on startup). |
| `MINIO_SECURE` | no | `true` for HTTPS between API and store. Default `false`. |
| `MINIO_URL_EXPIRE_SECONDS` | no | Presigned-URL lifetime. Default `3600`. |
| `DB_STATEMENT_TIMEOUT_MS` | no | PostgreSQL only â€” kill a single statement after N ms. Default `15000` (`0` = off). |
| `LLM_ROUTING_ENABLED` | no | Route each `request_type` through the centralised table (`config.DEFAULT_LLM_ROUTING` + `LLM_ROUTING_OVERRIDES`). Default `true`; `false` = single `LLM_PROVIDER`. |
| `LLM_DISABLED_PROVIDERS` | no | CSV of providers never used automatically (still buildable on demand). Default `mistral` (free-tier key 429s). |
| `LLM_ROUTING_OVERRIDES` | no | JSON merged over the default routing table. |
| `LLM_CIRCUIT_FAIL_THRESHOLD` / `LLM_CIRCUIT_COOLDOWN_SECONDS` | no | Per-(provider,model) breaker (in-process). Default `3` / `60`. |
| `REDIS_URL` | no | When set (`redis://â€¦`), the app cache uses Redis; otherwise in-memory. |
| `CACHE_TTL` | no | App-cache entry lifetime in seconds. Default `3600`. |
| `CACHE_VERSION` | no | Bump to invalidate every cached entry at once. Default `1`. |
| `MAX_UPLOAD_SIZE` | no | RÃ©sumÃ© upload cap in bytes. Default `10485760` (10 MB). |
| `JOBS_ENABLED_PROVIDERS` | no | CSV of job providers to run. Default: all 20. Unknown names are ignored. |
| `ASHBY_BOARDS` | no | CSV of Ashby board tokens. Ships with a small curated list. |
| `WORKDAY_TENANTS` / `ORACLE_HCM_SITES` / `SMARTRECRUITERS_BOARDS` / `RIPPLING_BOARDS` / `TALENTBREW_BOARDS` | no | Tenant-specific ATS â€” empty by default (the provider self-disables). Full board URLs for Workday/Oracle/TalentBrew; slugs for SmartRecruiters (**case-sensitive**) / Rippling. |
| `PHENOM_BOARDS` | no | JSON array of `{company, endpoint, payload}` for the per-tenant Phenom `/widgets` API. Empty â‡’ disabled. |
| `ATS_ENRICH_DESCRIPTIONS` / `ATS_ENRICH_MAX` | no | Bounded, **relevance-ordered** per-job description enrichment for the ATS whose board list omits it (Workday/Oracle/SmartRecruiters). Default `true` / `20`. |
| `ATS_HTTP_MAX_BYTES` | no | Response-size cap for the ATS board-list fetch (they run larger than an HTML page). Default `12000000`; the strict `JOB_HTTP_MAX_BYTES` (3 MB) still applies everywhere else. |
| `ATS_USE_BUNDLED_BOARDS` / `ATS_MAX_BOARDS` | no | Use the shipped `data/ats_boards/*.txt` lists (default `true`); cap on boards per provider per search (default `60`). |
| `ATS_BOARD_OVERLAY_TTL` | no | Seconds the superadmin `ats_boards` DB overlay is cached. Default `60`. Zero rows â‡’ pure file/env behaviour. |
| `JOB_SEARCH_MAX_CONCURRENCY` / `JOB_SEARCH_DEADLINE` | no | Provider fan-out: worker threads (`20` â€” the full set starts at once) / one wall-clock deadline for the whole fan-out (`45.0` s). |
| `<X>_BOARDS_FILE` (`GREENHOUSE`, `LEVER`, `ASHBY`, `SMARTRECRUITERS`, `RIPPLING`, `WORKDAY_TENANTS`, `ORACLE_HCM_SITES`, `TALENTBREW`, `PHENOM`) | no | Path to your own list file â€” one token/URL per line (`#` comments ok), merged on top of the bundled + env lists. |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | no | Adzuna free-tier credentials. **Blank â‡’ the Adzuna provider is disabled**; everything else is unaffected. |
| `LINKEDIN_MAX_PAGES` / `LINKEDIN_FRESHNESS_DAYS` / `LINKEDIN_ENRICH_MAX` | no | Guest-search depth (`5`, 25 cards/page), `f_TPR` recency window in days (`30`, `0`=off), per-offer enrichment cap (`12`). |
| `GREENHOUSE_BOARDS` / `LEVER_BOARDS` / `ASHBY_BOARDS` | no | **Extra** slugs merged on top of the bundled `data/ats_boards/*.txt` list. Empty by default. Invalid slugs are dropped. |
| `PROVIDER_STALE_MIN_OFFERS` / `PROVIDER_STALE_EMPTY_RUNS` | no | "Provider looks broken" heuristic for `/api/admin/providers` â†’ `health_note` (defaults `5` / `6`). |
| `CAREER_PAGE_URLS` | no | CSV of **public** company career-page URLs for the `career_pages` browser provider. Empty â‡’ disabled. Never a LinkedIn URL. |
| `BROWSER_ENABLED` | no | Master switch for the headless-browser session. Default `false` â€” `career_pages` self-disables while off. |
| `BROWSER_HEADLESS` / `BROWSER_BINARY` / `BROWSER_DRIVER_PATH` / `BROWSER_USER_DATA_DIR` | no | Chromium wiring. In the Docker image `BROWSER_BINARY` / `BROWSER_DRIVER_PATH` are preset to the `chromium` packages. |
| `BROWSER_PAGE_LOAD_TIMEOUT` / `BROWSER_SCRIPT_TIMEOUT` / `BROWSER_IMPLICIT_WAIT` | no | Per-render Selenium timeouts (s). Defaults `30` / `30` / `0`. |
| `BROWSER_MAX_PAGES_PER_SESSION` / `BROWSER_IDLE_SHUTDOWN_SECONDS` | no | Recycle the driver after N renders (`40`) / quit it after N seconds idle (`300`). |
| `JOB_SEARCH_CACHE_TTL` | no | Seconds an agent job-search response is cached. Default `900`. |
| `JOB_FRESH_TTL_HOURS` / `JOB_STALE_TTL_DAYS` / `JOB_DEFAULT_EXPIRY_DAYS` | no | Freshness windows. Defaults `24` / `14` / `30`. |
| `JOB_SEARCH_MAX_RESULTS` / `JOB_DEDUP_TITLE_RATIO` | no | Aggregated result cap (`60`) and the same-company title-similarity merge threshold (`0.90`). |
| `JOB_PROVIDER_TIMEOUT` / `JOB_PROVIDER_MAX_PAGES` / `JOB_PROVIDER_MAX_RESULTS` | no | Per-provider HTTP budget. Defaults `10.0` / `3` / `50`. |
| `JOB_PROVIDER_REQUEST_DELAY` / `JOB_PROVIDER_USER_AGENT` | no | Politeness delay (s) between requests to one host, and the identifying UA. |
| `JOB_HTTP_MAX_BYTES` / `JOB_HTTP_MAX_REDIRECTS` | no | Response-size cap (`3000000`) and redirect cap (`3`) enforced by the SSRF-guarded fetcher. |
| `JOB_CIRCUIT_FAIL_THRESHOLD` / `JOB_CIRCUIT_COOLDOWN_SECONDS` | no | Circuit breaker â€” consecutive failures before a provider is skipped (`3`), and for how long (`600`). |

E-mail delivery chain for the reset code: **`RESEND_API_KEY` â†’ `SMTP_HOST`
â†’ dev-mode** (the code is only logged; returned in the response body when
`PASSWORD_RESET_DEV_MODE=true`). The app never sends real e-mail without
one of the first two configured, and a delivery failure never breaks the
`200` from `forgot-password`.

The app never crashes at import time if `NVIDIA_API_KEY` is missing â€”
`GET /api/health` reports `"gemini_configured": false`, and any endpoint
that needs the LLM returns a clear `503`. `DATABASE_URL` and
`JWT_SECRET_KEY`, however, are checked at **startup** and abort the boot
if absent.

> **Note on model availability.** NVIDIA NIM retires hosted models
> aggressively (a model id can start returning HTTP 410 "end of life" from
> one day to the next). `services/gemini_client.py` therefore tries a
> **list** of models in order:
> `openai/gpt-oss-20b` â†’ `nvidia/nemotron-3-super-120b-a12b` â†’
> `nvidia/nemotron-3-nano-30b-a3b`. If all fail, check
> `GET https://integrate.api.nvidia.com/v1/models` for currently-served
> ids and update `NVIDIA_MODELS_FALLBACK`.

---

## Running the API

### Locally

You need PostgreSQL, and (for the generation routes) MinIO â€” Redis is
optional. The quickest way is to bring up just the infra with compose and
run the API on the host:

```bash
docker compose up -d postgres redis minio

export DATABASE_URL=postgresql://cv:cv@localhost:5432/cv_generator
export JWT_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export MINIO_ENDPOINT=localhost:9000
export MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin
export REDIS_URL=redis://localhost:6379/0

uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Tables are created automatically on startup; the MinIO bucket too.

### With Docker (full stack)

`docker compose up --build` starts **postgres + redis + minio + api**,
wired together (the compose file injects `DATABASE_URL`, `REDIS_URL` and
`MINIO_*` for the `api` service). Needs a `.env` next to
`docker-compose.yml` with at least `NVIDIA_API_KEY` and `JWT_SECRET_KEY`.

```bash
docker compose up --build          # build + run (Ctrl-C to stop)
docker compose up -d --build       # ... detached
docker compose logs -f api         # follow the API logs
docker compose down                # stop & remove   (add -v to wipe volumes)
```

MinIO console: <http://localhost:9001> (`minioadmin` / `minioadmin`).

The image (`python:3.11-slim` base, ~430 MB) runs as a non-root user and
declares a `HEALTHCHECK` hitting `/api/health`. Without compose:

```bash
docker build -t cv-assistant:latest .
docker run --rm -p 8000:8000 --env-file .env cv-assistant:latest
```

### Verify

- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/health>

```bash
curl http://localhost:8000/api/health
# {"status":"ok","service":"cv-generator","version":"1.0.0","gemini_configured":true}
```

---

## API reference

| Method | Path | Auth | Purpose | Response |
|---|---|---|---|---|
| `GET`  | `/api/health` | â€” | Liveness + LLM configuration status | JSON |
| `POST` | `/api/auth/signup` | â€” | Create an account. E-mail is trimmed + lower-cased and UNIQUE at the DB level; a duplicate (including a concurrent race) is always `409`, never `500`. | JSON (`access_token` + `user`) |
| `POST` | `/api/auth/signin` | â€” | Log in | JSON (`access_token` + `user`) |
| `POST` | `/api/auth/forgot-password` | â€” | Request a 6-digit reset code by e-mail | JSON (always the same `200`) |
| `POST` | `/api/auth/reset-password` | â€” | Verify the code + set a new password | JSON (`200` / generic `400`) |
| `GET`  | `/api/auth/me` | **Bearer** | Current account | JSON (`id`, `email`) |
| `POST` | `/api/extract-cv` | **Bearer** | Extract structured data from an uploaded rÃ©sumÃ© | JSON (`ExtractCVResponse`) |
| `POST` | `/api/generate-cv` | **Bearer** | Generate a CV PDF â†’ MinIO (pass `reference` for a new version) | JSON + presigned URL |
| `POST` | `/api/generate-letter` | **Bearer** | Generate a cover-letter PDF â†’ MinIO | JSON + presigned URL |
| `GET`  | `/api/cvs` Â· `/api/letters` | **Bearer** | List the caller's documents (newest first) | JSON array |
| `GET`  | `/api/cvs/{id}` Â· `/api/letters/{id}` | **Bearer** | One document's metadata (`404` unknown, `403` not owner) | JSON |
| `GET`  | `/api/cvs/{id}/download` Â· `/api/letters/{id}/download` | **Bearer** | A fresh presigned download URL | JSON |
| `DELETE` | `/api/cvs/{id}` Â· `/api/letters/{id}` | **Bearer** | Delete from MinIO + DB | `204` |
| `POST` | `/api/conversations/{id}/messages` | **Bearer** | Talk to the agent â€” it runs the job search internally (no manual search API) | JSON (`MessageSendResponse`) |
| `GET`  | `/api/dashboard/jobs` | **Bearer** | The jobs the agent retained for the caller + per-job application status | JSON |
| `GET`  | `/api/jobs/{id}` | **Bearer** | Job detail (scoped to the caller's selection) + application method | JSON |
| `POST` | `/api/jobs/{id}/apply` | **Bearer** | Record an application, return the external URL | JSON (never `submitted`) |
| `GET`  | `/api/jobs/applications` Â· `/applications/{id}` | **Bearer** | The caller's application history (`403` not owner) | JSON |
| `POST`/`DELETE` | `/api/jobs/{id}/save` Â· `GET /api/jobs/saved` | **Bearer** | Bookmark jobs (idempotent, owner-scoped) | `204` / JSON |
| `GET`/`PUT`/`DELETE` | `/api/profile` | **Bearer** | The caller's persistent structured job-search profile (owner-scoped by PK) | JSON / `204` |
| `GET` | `/api/dashboard` | **Bearer** | The caller's **own** aggregated dashboard (activity / jobs / applications / auto-apply / documents / matching / providers). Strictly `where(user_id == current_user.id)`; no `user_id` accepted from the client â€” user A can never see B's figures. Cached `DASHBOARD_CACHE_TTL` seconds per user. | JSON |
| `POST`/`GET`/`DELETE` | `/api/conversations` (+ `/{id}`, `/{id}/messages`) | **Bearer** | Persistent conversations with the assistant, strictly owner-scoped. `POST /{id}/messages` saves the user's message, replies with the last `CONVERSATION_HISTORY_LIMIT` messages as context, then saves the reply. | JSON / `204` |
| `GET` | `/api/admin/users` Â· `GET/PATCH /api/admin/users/{id}` | **Superadmin** | List / inspect / update accounts (`is_active`, `is_superadmin`) | JSON |
| `GET`/`POST`/`PATCH`/`DELETE` | `/api/admin/ats-boards` (+ `/test`, `/{id}/test`) | **Superadmin** | Manage ATS boards â€” a DB overlay on the file/env lists; per-board live test | JSON / `204` |
| `GET` | `/api/admin/providers` | **Superadmin** | Per-provider run stats (runs / success rate / offers / p95 latency / health) | JSON array |
| `GET` | `/api/admin/stats/{usage,users,applications,applications/by-user,documents}` | **Superadmin** | Live SQL aggregates (DAU/WAU/MAU/YAU + event counts, applications, documents) | JSON |
| `GET`/`PATCH` | `/api/admin/llm` Â· `POST /api/admin/llm/test` | **Superadmin** | Read / change (no key) / probe the active LLM provider | JSON |
| `GET` | `/api/admin/dashboard` | **Superadmin** | One-shot dashboard (usage + apps + docs + providers + boards + LLM); 60 s cache | JSON |

`/api/admin/*` requires a **superadmin** account: a normal token is `403`, a
missing/expired token is `401`. `is_superadmin` is not in any request schema â€”
the first admin is minted by `SUPERADMIN_EMAILS` (startup, existing accounts,
never demotes), thereafter by another superadmin's `PATCH`.

Send the token as `Authorization: Bearer <access_token>`. Full schemas and
a try-it-out console are auto-generated at `/docs` and `/redoc`. See
[Workflow 4](#workflow-4--job--internship-search-apijobs) for the job
search architecture, provider list and security model.

### Authentication & password reset

```bash
# 1. Sign up (or sign in) â€” returns { access_token, token_type, user }
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"Str0ngPass1"}'

TOKEN=...   # access_token from the response

# 2. Use it on every CV endpoint
curl -X POST http://localhost:8000/api/generate-cv \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @payload.json
```

The password reset is **two steps** and leaks nothing about which e-mails
have accounts:

```bash
# Step 1 â€” always returns the same 200, whether or not the account exists.
#          A 6-digit code is e-mailed (and, with PASSWORD_RESET_DEV_MODE=true,
#          echoed as "reset_code" in the body).
curl -X POST http://localhost:8000/api/auth/forgot-password \
  -H "Content-Type: application/json" -d '{"email":"me@example.com"}'

# Step 2 â€” verify the code and set the new password.
curl -X POST http://localhost:8000/api/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","code":"123456","new_password":"N3wStrongPass"}'
```

The code is random, SHA-256-hashed at rest, single-use, expires after
`PASSWORD_RESET_CODE_EXPIRE_MINUTES`, and is burned after
`PASSWORD_RESET_MAX_ATTEMPTS` wrong guesses. Any failure in step 2 â€”
unknown e-mail, wrong / expired / used code, too many attempts â€” returns
the **same** generic `400 {"status":"error","message":"Invalid or expired
verification code."}`.

---

## Workflow 1 â€” Extraction (`POST /api/extract-cv`)

**Request** â€” `multipart/form-data`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | `.pdf` or `.docx`, max 10 MB |
| `language` | text | no | `"fr"` or `"en"`. When given, it drives the LLM section prompts and is echoed as `language`. When omitted, `language` mirrors the auto-detected value. Any other value â†’ `422`. |

```bash
curl -X POST http://localhost:8000/api/extract-cv \
  -F "file=@resume.pdf" \
  -F "language=en"
```

**Response** (`200`) â€” real output for the sample CV in
[`postman/sample_resume.txt`](./postman/sample_resume.txt):

```json
{
  "status": "success",
  "language": "en",
  "detected_language": "en",
  "cv_profile": {
    "name": "John Doe",
    "email": "john.doe@example.com",
    "phone": "+212 600 112233",
    "linkedin": "https://www.linkedin.com/in/johndoe",
    "github": "https://github.com/johndoe",
    "portfolio": null,
    "address": "12 Rue de la Paix, Rabat",
    "nationality": "Moroccan",
    "professional_summary": "Backend engineer focused on data platforms and API design.",
    "education": [
      {
        "institution": "INSEA",
        "degree": "Master",
        "field": "Data Science",
        "start_date": null,
        "end_date": "2024",
        "gpa": null,
        "location": null
      }
    ],
    "experience": [
      {
        "company": "Acme Corp",
        "position": "Software Engineer",
        "period": "3 years 7 months",
        "location": "Remote",
        "description": null,
        "achievements": [
          "Shipped the billing service handling 2M requests/day",
          "Cut pipeline latency by 40%"
        ],
        "technologies": []
      }
    ],
    "projects": [
      {
        "title": "Portfolio site",
        "description": "personal website built with Next.js",
        "technologies": ["Next.js"],
        "github": "github.com/johndoe/portfolio",
        "demo": null
      }
    ],
    "skills": ["Python", "SQL", "PostgreSQL", "MongoDB", "Docker", "Kubernetes", "AWS"],
    "languages": [
      { "language": "English", "level": "Fluent" },
      { "language": "French", "level": "Native" },
      { "language": "Arabic", "level": "Native" }
    ],
    "certifications": [
      { "name": "AWS Certified Solutions Architect", "issuer": "Amazon", "year": 2023 }
    ],
    "interests": ["Open-source", "Cycling"],
    "personal_qualities": ["Analytical thinking", "Team collaboration"]
  },
  "confidence_scores": {
    "contact": 95,
    "experience": 88,
    "education": 82,
    "projects": 90,
    "skills": 97
  },
  "validation_issues": [],
  "duplicates_removed": {},
  "sections_detected": ["summary", "experience", "education", "projects", "skills", "languages", "certifications"],
  "message": "Resume extracted successfully."
}
```

**Every field defaults to `null` / `[]` when absent from the source
document â€” never a fabricated placeholder.** The frontend renders
`cv_profile` as an editable form, using:

- `confidence_scores` (0â€“100 per section) to visually flag low-confidence
  sections that need attention,
- `validation_issues` (`{field, message, severity}`) to surface concrete
  problems (missing email, malformed URL, a duplicate that was
  auto-removed),
- `language` vs `detected_language` to warn if the caller-selected
  language disagrees with the document.

**Error responses:** `400` empty file Â· `413` over 10 MB Â· `415`
unsupported type Â· `422` bad `language` value / unreadable text Â· `500`
unexpected. All as `{"status": "error", "message": "..."}`.

---

## Workflow 2 â€” Generation (`POST /api/generate-cv`)

**Request** â€” `application/json`:

```jsonc
{
  "language": "fr",                       // "fr" | "en" (default "en")
  "job_description": "Nous recherchonsâ€¦",  // optional â€” enables ATS targeting
  "cv_profile": { /* validated CVProfile â€” see below */ }
}
```

`cv_profile` is the **completed** profile from the review form. Unlike the
extraction contract, `name`, `email` and `phone` are **required** here
(the data has been validated by a human by this point); every other field
is optional.

```bash
curl -X POST http://localhost:8000/api/generate-cv \
  -H "Content-Type: application/json" \
  -o cv.pdf \
  -d '{
        "language": "fr",
        "job_description": "IngÃ©nieur Machine Learning, Python, Kubernetesâ€¦",
        "cv_profile": {
          "name": "John Doe",
          "email": "john.doe@example.com",
          "phone": "+212 600 112233",
          "linkedin": "linkedin.com/in/johndoe",
          "professional_summary": "Backend engineer focused on data platforms.",
          "education": [
            { "institution": "INSEA", "degree": "Master", "field": "Data Science", "start_date": "2022", "end_date": "2024" }
          ],
          "experience": [
            {
              "company": "Acme Corp", "position": "Software Engineer",
              "period": "3 years 2 months",
              "achievements": ["Shipped the billing service"],
              "technologies": ["Python", "PostgreSQL"]
            }
          ],
          "projects": [],
          "skills": [{ "category": "Languages", "skills": ["Python", "SQL"] }],
          "languages": [{ "language": "English", "level": "Fluent" }],
          "certifications": []
        }
      }'
```

**Response** (`200`) â€” **JSON**, not a PDF stream (breaking change from
v1.x). The PDF is rendered in memory, pushed to MinIO under
`cv/<uuid>_<lang>.pdf`, recorded in the database against the caller, and
you get a short-lived presigned URL to fetch it:

```json
{
  "status": "success",
  "generated_cv_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "ats_score": 87.5,
  "language": "fr",
  "filename": "cv_3fa85f64-5717-4562-b3fc-2c963f66afa6_fr.pdf",
  "download_url": "https://minio.example.com/cv-files/cv/3fa8...fr.pdf?X-Amz-...",
  "expires_in": 3600
}
```

`GET` the `download_url` directly for the PDF bytes (or call
`GET /api/cvs/{id}/download` later for a fresh URL). On failure the
response is JSON with the right status code: `401` no / bad token Â· `422`
invalid payload Â· `429` rate limit hit Â· `503` `NVIDIA_API_KEY` **or**
MinIO not configured Â· `500` unexpected.

### Re-targeting an existing CV

`POST /api/optimize-existing-cv` was **removed in Phase 2b** â€” it was a
byte-for-byte alias of `/api/generate-cv`. To re-target an already-validated
profile to a different job posting, call `POST /api/generate-cv` with the new
`job_description`; add `reference` to store the result as a new **version** of
the existing document (LOT 8 versioning).

### Managing stored documents

`GET /api/cvs` and `GET /api/letters` list the caller's own documents
(newest first). `GET /api/cvs/{id}` returns one document's metadata â€”
`404` if it does not exist, `403` if it belongs to another user.
`GET /api/cvs/{id}/download` mints a fresh presigned URL.
`DELETE /api/cvs/{id}` removes it from both MinIO and the database
(`204`). The `/api/letters/*` routes behave identically.

---

## Workflow 3 â€” Cover letter (`POST /api/generate-letter`)

Turns a validated `cv_profile` + a job description into a one-page cover
letter PDF, in French or English. The letter body is written by the LLM
(strict rules: first person, at most four short paragraphs, no bracket
placeholders, **only facts already in the profile**); a small extra LLM
call pulls the hiring company / position / recipient out of the job
description for the letter's heading. Nothing is written to disk.

**Request** â€” `application/json`:

```jsonc
{
  "language": "fr",                       // "fr" | "en" (default "en")
  "job_description": "Nous recrutonsâ€¦",     // required
  "cv_profile": { /* validated CVProfile, same shape as /api/generate-cv */ },
  "recipient_name": "Mme Dupont",          // optional â€” overrides the parsed addressee
  "company_address": "12 rue â€¦\n75001 Paris" // optional â€” overrides the parsed address
}
```

```bash
curl -X POST http://localhost:8000/api/generate-letter \
  -H "Content-Type: application/json" \
  -o cover_letter.pdf \
  -d '{
        "language": "en",
        "job_description": "We are hiring a Full Stack Developer (React + Node.js) at TechNova, Casablanca.",
        "cv_profile": { "name": "Issalmou Adaaiche", "email": "issalmou@example.com", "phone": "+212 640 065 118", "experience": [ â€¦ ], "skills": [ â€¦ ] }
      }'
```

**Response** (`200`) â€” JSON, same shape as `/api/generate-cv` but with
`generated_letter_id` and no `ats_score`; the PDF is stored in MinIO under
`letter/<uuid>_<lang>.pdf`:

```json
{
  "status": "success",
  "generated_letter_id": "7c2f...",
  "language": "en",
  "filename": "cover_letter_7c2f..._en.pdf",
  "download_url": "https://minio.example.com/cv-files/letter/7c2f..._en.pdf?X-Amz-...",
  "expires_in": 3600
}
```

**Error responses:** `401` no / bad token Â· `422` missing/empty
`job_description` or bad `language` Â· `429` rate limit Â· `503`
`NVIDIA_API_KEY` or MinIO not configured Â· `500` unexpected.

---

## Workflow 4 â€” Job & internship search (`/api/jobs/*`)

Since **v2.1** the service aggregates job & internship listings from many
**free / public** sources, normalises + deduplicates + freshness-checks
them, ranks them (no LLM), and lets a user record an application for a
selected job. It is a **provider framework**, not a scraper: one platform
= one file in `services/providers/`, and one provider failing never
breaks the search.

### Endpoints (all require `Authorization: Bearer`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/conversations/{id}/messages` | The agent understands a job-search request, runs `JobSearchService` **internally and asynchronously** (`search_async`, no manual search endpoint), and retains the best offers on the dashboard. |
| `GET` | `/api/dashboard/jobs` | The retained offers + per-job application status (the frontend renders an **Apply** button per row). |
| `GET`  | `/api/jobs/{id}` | Full normalised detail + application method; **revalidates** a stale row against its provider |
| `POST` | `/api/jobs/{id}/apply` | Records intent, returns the **real external URL**. Never auto-submits. |
| `GET`  | `/api/jobs/applications` Â· `/applications/{id}` | The caller's application history (owner-scoped, `403` otherwise) |
| `POST`/`DELETE` | `/api/jobs/{id}/save` Â· `GET /api/jobs/saved` | Bookmark jobs (idempotent, owner-scoped) |

```bash
# 1. ask the agent to search â€” it runs JobSearchService internally
curl -X POST $BASE/api/conversations/$CONV_ID/messages -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json'   -d '{"content":"Je cherche un stage data science a Paris, ouvert au remote, Python/SQL"}'
# -> { "assistant_message": {...}, "jobs_found": [ {job_offer_id, title, ...} ], ... }

# 2. the retained offers + their apply status
curl $BASE/api/dashboard/jobs -H "Authorization: Bearer $TOKEN"

# 3. detail + apply (the "Apply" button)
curl $BASE/api/jobs/$JOB_ID -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/api/jobs/$JOB_ID/apply -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json' -d '{"cv_id":"...","letter_id":"...","prepare":true}'
# -> { "application_status": "prepared", "application_url": "https://...", ... }

# 3b. or let the agent apply â€” it PROPOSES (with an explicit oui/non ask,
#     in the language of your own message), then a "oui" confirms
curl -X POST $BASE/api/conversations/$CONV_ID/messages -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json' -d '{"content":"postule aux offres 1 et 2"}'
# -> { "pending_confirmation": {kind:"apply", job_ids:[<real UUIDs, never "1"/"2">]},
#      "assistant_message": {"content": "... Voulez-vous continuer ? RÃ©pondez uniquement par Â« oui Â» ou Â« non Â»."},
#      "applications": [] }
curl -X POST $BASE/api/conversations/$CONV_ID/messages -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json' -d '{"content":"oui"}'
# -> { "applications": [ {job_offer_id, application_status, ...} ] }

# 4. more than 5 offers were found? the reply always caps the conversational
#    listing at 5 â€” ask for the rest, the SAME already-ranked list, never a
#    new search, never re-ranked, never a duplicate:
curl -X POST $BASE/api/conversations/$CONV_ID/messages -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json' -d '{"content":"montre-moi les suivantes"}'
# -> { "assistant_message": {"content": "Here are roles 6-10 of 14: ..."}, "jobs_found": [...5 more...] }
```

### Conversational job listing â€” 5 at a time, deterministic pagination

`GET /api/jobs/*` never returns more than 5 offers per conversational turn,
even when the search behind it found many more. The FULL ranked order is
frozen on `Conversation.job_browse_state` (`{ordered_job_ids, offset,
page_size}`) the moment a search runs, and a follow-up "montre-moi
tous/les suivants" / "show me all / more" turn is recognised as
`intent="list_jobs"` and **slices that same frozen list** â€” it never
re-runs `JobSearchService`, never recomputes the ranking, and never
repeats or skips an offer. A numbered offer ("l'offre 1", "job 3") always
resolves against that same frozen order before it is ever written into
`pending_action.job_ids` â€” the number shown to the user is a display index
only, never persisted, never confused with the real UUID underneath it. A
later search overwrites `job_browse_state` with a fresh order, but never
changes what an *already-frozen* `pending_action` from an earlier proposal
means.

### Confirmation â€” explicit, deterministic, and in the user's own language

Every apply *proposal* ends with an explicit instruction, appended in code
(never left for the LLM to phrase, never a mix of the two languages):

- French user message â†’ `â€¦ Voulez-vous continuer ? RÃ©pondez uniquement par Â« oui Â» ou Â« non Â».`
- English user message â†’ `â€¦ Would you like to continue? Please reply only with "yes" or "no".`

The language is picked by a small local heuristic (`_detect_lang`, no LLM
call) on the user's own message. The decision to actually apply is 100%
deterministic Python (`_is_confirmation`) â€” a short "oui" / "yes" /
"confirme" / "confirm" (Â± a trailing filler word) applies exactly the
frozen `pending_action.job_ids`; anything carrying a caveat ("oui
maisâ€¦", "oui, attendsâ€¦", "yes butâ€¦", "yes, beforeâ€¦") is **not** a
confirmation and drops the stale proposal instead. The LLM is not even
called on a confirmation turn.

### Intelligent recommendations (Phase 7)

Every conversational turn (chat, search, list, apply-propose, or a question)
can surface **0-3 contextual next-step suggestions** â€” not just about the
CV/ATS match, but about the CV, a cover letter, the job search, a specific
selected offer, the user's profile preferences, an application, or the
conversation itself:

```
ContextBuilder (compact summary: CV/letter/application status â€” never the
  raw documents, never the whole DB)
        â†“
ONE call_gemini â€” the SAME ConversationDirective call that already decides
  intent/reply/search_patch/apply â€” now also returns 0-10 raw
  IntelligentRecommendation candidates (action is a free label, e.g.
  "adapt_cv_to_job", "clarify_user_preference", "review_prepared_application"
  â€” never a fixed 10-value enum)
        â†“
RecommendationValidator (services/conversations/recommendation_engine.py) â€”
  deterministic Python: drops a recommendation whose target doesn't belong
  to the caller, whose action names a destructive/system verb, or that
  contradicts state the backend already knows (a letter that already
  exists, an application already made); dedupes; ranks by priority then
  confidence; caps to 3
        â†“
MessageSendResponse.recommendations â€” the frontend renders these as
  suggestions; nothing here is ever executed automatically. Acting on one
  happens the same way any request does: the user sends a new message,
  which goes through the normal intent â†’ (confirmation, for anything
  sensitive) â†’ service pipeline.
```

Zero extra LLM calls â€” recommendations ride along in the turn's one
`call_gemini`. The older, closed-vocabulary `recommended_actions`
(`review_match` / `confirm_skill` / â€¦ from `services/recommendations.py`)
is unchanged and kept for backward compatibility â€” it's still what
`/api/generate-cv` attaches after a CV generation (a stateless endpoint with
no conversation to build a directive from, so it stays a cheap deterministic
rule engine on purpose) and what the apply-proposal/request-information
turns populate directly, exactly as in Phase 6. The two fields are additive,
not a replacement of one by the other.

**Phase 8 hardening**, on top of the same pipeline:

- **Language consistency** â€” a recommendation whose own `title`/`message`
  text is confidently detected in the wrong language for the conversation
  is dropped (`validate_and_rank(..., language=...)`); a short label with
  too little signal ("Adapt CV") is never penalised.
- **Intra-batch contradiction / doublon collapse** â€” two survivors that
  concern the *same document* (CV or cover letter) for the *same target*
  (e.g. the LLM returning both "generate_cover_letter" and
  "review_cover_letter" for one job in one turn) collapse to the single
  higher-ranked one, never shown as two conflicting suggestions.
- **A search-turn recommendation cannot know the real job until the search
  runs** (the LLM decides `intent`/`recommendations` in the same call that
  precedes the search) â€” `_backfill_top_job_target` resolves a generic
  "review the top match"-style suggestion to the actual top result right
  after the search, no second LLM call, no invented text. A suggestion
  about a *specific, non-top* result on that exact turn still has to wait
  one turn, once it's part of the context (see `benchmarks/PHASE_6.md` Â§O).
- **An explicit job number in the user's own message overrides the LLM's
  own guess** â€” "l'offre 2" / "job #3" / "la deuxiÃ¨me" resolve
  deterministically (`_extract_explicit_job_ordinal`, accent-insensitive)
  and replace whatever `target_id` the LLM attached. An *implicit* reference
  ("cette offre", with no number) is not resolved this way â€” documented
  residual limitation, not a silently-ignored bug.
- **A confident claim about an unconfirmed skill is downgraded, not
  trusted** â€” when a recommendation states (not asks) that the user has a
  skill absent from their stored profile, it is turned into a question
  instead (`_downgrade_unconfirmed_skill_claim`). Free-text `message`/
  `reason` prose itself still cannot be fact-checked deterministically â€”
  identical, pre-existing limitation to `reply` since Phase 4, not a new gap.
- **Real-LLM spot check** (not part of the automated suite, which stays
  deterministic/mocked): asked, in French, about an offer the user is
  unsure about because of a Kubernetes gap, the live model returned
  `ask_kubernetes_experience` / `adapt_cv_to_job` / `generate_cover_letter`
  â€” three genuinely different, correctly-targeted, non-enum suggestions.
  Asked again with a CV/letter already done and the user saying everything
  looks good, it correctly returned zero recommendations rather than
  padding to fill slots.

### Provider fan-out

Every enabled provider is queried **concurrently** on a **shared, process-wide
thread pool** (`JOB_SEARCH_GLOBAL_CONCURRENCY`, default `32`) â€” so N simultaneous
searches can never spawn N Ã— 20 threads / connections. Within one search a
semaphore caps concurrent provider calls at `JOB_SEARCH_MAX_CONCURRENCY`
(default `20` â€” the whole default set in one wave). The entire fan-out is bounded
by **one shared wall-clock deadline** (`JOB_SEARCH_DEADLINE`, default `45 s`):
results are harvested as they complete, and whatever is still running when the
deadline elapses is abandoned (circuit-breaker tripped, marked
`temporarily_unavailable`), the task finishing quietly on the shared pool bounded
by the provider's own per-request timeout. So one hung provider costs the
deadline **once**, never once per hung provider, and a fast provider is never
held up behind a slow one. `http.py` stays the sole outbound-HTTP chokepoint;
per-request timeouts, the SSRF guard, circuit breakers and `provider_states`
are unchanged.

**The conversation agent's search is genuinely `asyncio`** (Phase 6
finalisation): `JobSearchService.search_async()` / `_fan_out_async` dispatch
each provider call onto the **same** shared thread pool via
`loop.run_in_executor` and await them with `asyncio.wait(...,
return_when=FIRST_COMPLETED)` â€” the identical one-shared-deadline,
per-provider-fault-isolated semantics as the sync `_fan_out`, just awaited
instead of blocked on. A **full rewrite** of the 20 providers + `http.py`
onto an async HTTP client was evaluated and, again, deferred â€” the sync
`httpx`/SQLAlchemy stack + 20 providers + their tests make that high-risk for
a marginal further gain, since the thread-pool fan-out already parallelises
the one thing worth parallelising (independent provider I/O). `search()`
(sync) is unchanged and still what every other caller (and ~15 existing test
files) uses; `search_async()` is additive, used only by
`JobConversationAgent._search`. See `tests/test_job_search_async.py` for the
measured concurrency evidence.

### Job links

Every offer surfaced by the agent search and `/api/jobs/{id}` carries an
`http(s)` `source_url` back to the original posting. A provider that cannot
produce a real URL has its offer **dropped** â€” a link is never fabricated.
Deduplication keeps the primary source's URL and records the others in
`also_seen_on`.

### Providers

| Provider | Method | Notes |
|---|---|---|
| **linkedin** | multi-strategy public HTML/JSON-LD | guest search API â†’ guest search page (list); guest job fragment â†’ guest job-view JSON-LD (per-offer enrichment, bounded concurrency). Circuit breaker. |
| **indeed** | public SERP HTML + JSON-LD | Cloudflare-protected â†’ **usually `unavailable`**, by design |
| **arbeitnow** | public JSON API | reliable |
| **weworkremotely** | public RSS | remote-only |
| **hackernews** | Algolia API ("Who is hiring?") | monthly thread, best-effort line parsing |
| **remotive** | public JSON API | remote-only |
| **jobicy** | public JSON API v2 | remote-only |
| **remoteok** | public JSON | UA-gated â†’ often `unavailable` |
| **himalayas** | public JSON API | may block server IPs â†’ often `unavailable` |
| **adzuna** | JSON API (free tier) | **optional** â€” self-disables without `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
| **greenhouse** | public board JSON API (`boards-api.greenhouse.io`) | **no browser** â€” one token per company board (`GREENHOUSE_BOARDS`); a 404/blocked board is skipped, only *every* board failing marks the provider |
| **lever** | public postings JSON API (`api.lever.co`) | **no browser** â€” one token per company board (`LEVER_BOARDS`); same skip-one-board behaviour |
| **ashby** | public board JSON API (`api.ashbyhq.com/posting-api`) | **no browser** â€” `ASHBY_BOARDS`; the board list already carries description + compensation, so no per-job enrichment |
| **workday** | public CXS JSON API (`{tenant}.myworkdayjobs.com/wday/cxs`) | **no browser** â€” `WORKDAY_TENANTS` (full board URLs); `POST` list + offset pagination + **bounded per-job description enrichment**; tenant path case is preserved |
| **oracle_hcm** | public CandidateExperience REST (`*.oraclecloud.com/hcmRestApi`) | **no browser** â€” `ORACLE_HCM_SITES` (full board URLs); site number + path **case-sensitive**, passed through verbatim; bounded `ById` description enrichment |
| **smartrecruiters** | public postings JSON API (`api.smartrecruiters.com`) | **no browser** â€” `SMARTRECRUITERS_BOARDS` (**case-sensitive** slugs); limit/offset pagination + bounded `jobAd.sections` description enrichment |
| **rippling** | public board JSON API (`api.rippling.com/platform/api/ats`) | **no browser** â€” `RIPPLING_BOARDS`; flat array, multi-location rows deduped by `uuid` |
| **phenom** | per-company `/widgets` POST API | **no browser** â€” `PHENOM_BOARDS` is a JSON array of `{company, endpoint, payload}`; each entry's SSRF allow-list is its own configured host |
| **talentbrew** | legacy AJAX `search-jobs/results` endpoint | **no browser** â€” `TALENTBREW_BOARDS` (full result URLs); parses the returned HTML fragment (title + URL + location); `CurrentPage` pagination |
| **career_pages** | headless browser render + schema.org `JobPosting` | **opt-in** â€” needs `BROWSER_ENABLED=true` **and** `CAREER_PAGE_URLS`; renders public JS-only career pages; **never a LinkedIn URL** |

The 7 ATS providers above are all **plain HTTP JSON** (no browser, no key, no
login) and share `services/providers/ats_common.MultiBoardATSProvider`:
iterate the configured boards, skip a board that 404s / is blocked, and only
mark the provider `blocked` (all denied) / `temporarily_unavailable` (all
unreachable) when *every* board failed. `ashby`, `workday`, `oracle_hcm`,
`smartrecruiters`, `rippling`, `phenom`, `talentbrew` ship **disabled** except
`greenhouse` / `lever` / `ashby` / `smartrecruiters` / `rippling`, which ship
enabled via the **bundled board lists** in `data/ats_boards/*.txt` (best-effort
lists of real public boards; a dead token self-skips). The effective list for a
provider is the bundled file **+** `<X>_BOARDS_FILE` (your own file) **+**
`<X>_BOARDS` (env CSV), de-duplicated, `valid_slug`-validated and capped at
`ATS_MAX_BOARDS`; `ATS_USE_BUNDLED_BOARDS=false` ignores the shipped lists.

Bounded description enrichment (`ATS_ENRICH_DESCRIPTIONS`, `ATS_ENRICH_MAX`) is
best-effort and **relevance-ordered** (LLM-free `job_ranking.score`) â€” a failing
detail endpoint never fails the search, an absent field is left `null` (never a
guess), and a keyword search paginates *past the first page* until it has
`page_size` matches (bounded by a per-provider deadline + `_MAX_PAGES`).

> **JobSpy** (the `python-jobspy` aggregator) was **evaluated and not
> integrated** â€” see `REFACTOR_REPORT.md Â§11`. It runs its own HTTP client
> (bypassing the `http.py` SSRF/size/politeness chokepoint), pulls a heavy
> `pandas` + native-TLS dependency tree with an unpatched transitive CVE, and
> its incremental coverage over the 20 existing providers is marginal.

`ProviderStatus` is one of `success` Â· `partial` Â· `unavailable` Â·
`disabled` and is reported per provider in every search response (the
`sources` map). It is derived â€” see the fine-grained `ProviderState` below.

#### `ProviderState` â€” why a source failed

Alongside the coarse `sources` map, every internal search response
carries a `provider_states` map with a **fine-grained** reason per provider:

| `ProviderState` | Coarse `ProviderStatus` | Meaning |
|---|---|---|
| `available` | `success` | ran fine â€” **including 0 results** (an empty search is not a failure) |
| `degraded` | `partial` | ran, but no strategy fully succeeded |
| `auth_required` | `unavailable` | the source demanded a login / hit an authwall or checkpoint |
| `blocked` | `unavailable` | hard denial â€” `403` / `451` / `HTTP 999` |
| `temporarily_unavailable` | `unavailable` | `429`, timeout, 5xx, circuit-breaker open â€” retry later |
| `error` | `unavailable` | an unexpected exception inside the provider |
| `disabled` | `disabled` | not configured / not in `JOBS_ENABLED_PROVIDERS` |

`sources` stays byte-identical to before (existing clients unaffected);
`provider_states` is purely additive.

#### `GET /api/admin/providers` â€” per-provider health (superadmin)

*(Phase 6: the public `GET /api/jobs/sources` was removed â€” provider health is
admin-only now. Same data, from `registry.all_info()`.)*

Each row carries `enabled` / `circuit_state` / `state` **plus** rolling run
metrics (cache-backed, 7-day TTL â€” the same mechanism as the circuit breaker,
no new state store): `runs`, `error_rate`, `last_run_at`, `last_ok_at`,
`last_offer_count`, `last_duration_ms`, `last_error` (a **sanitised** short
label â€” URLs and token-like strings stripped, never a secret), and
`health_note` â€” set **only** when a provider looks *structurally* broken:
the circuit is open, or it has produced results before but the last N runs
came back `available` with **zero** offers (an upstream API / markup change).
`health_note` is `null` for a provider that is simply new, or one whose
search legitimately found nothing.

#### LinkedIn â€” strategy matrix (legitimate public surface only)

| # | Strategy | Access | Format | Use |
|---|---|---|---|---|
| A | `GET /jobs-guest/jobs/api/seeMoreJobPostings/search` | public, no login | HTML card list | primary list â€” paged by `start=+25`, `LINKEDIN_MAX_PAGES` pages, `f_TPR` recency filter, salary off the card |
| B | `GET /jobs/search` â€” HTML cards **merged with** the JSON-LD `ItemList` | public, no login | HTML + JSON-LD | fallback list; the `ItemList` also fills dates/descriptions on card offers |
| C | `GET /jobs-guest/jobs/api/jobPosting/<id>` | public, no login | HTML fragment | per-offer enrichment (description, criteria, apply URL) |
| D | `GET /jobs/view/<id>` â†’ JSON-LD `JobPosting` | public (gated) | JSON-LD | enrichment fallback (dates, salary) |
| â€” | Voyager `/voyager/api/...` | **requires `li_at` auth** | JSON | **never used â€” forbidden** |

**Legal / security boundary.** The `linkedin` provider is a **public
collector only** â€” the authenticated path is *intentionally not
implemented*. No `li_at` cookie, no login, no Voyager or any
authenticated/private API, **no Selenium/browser pointed at LinkedIn**, no
CAPTCHA solving, no stealth browser, no proxy rotation, no anti-bot
fingerprint spoofing. Automating an authenticated LinkedIn session
violates the LinkedIn User Agreement, so it is not built â€” regardless of
framing. A `403` / `451` / `HTTP 999` â†’ the provider state is `blocked`;
an authwall / `/checkpoint/challenge` / `/uas/login` page â†’ `auth_required`;
a `429` / timeout â†’ `temporarily_unavailable`. All coarse to
`sources["linkedin"] == "unavailable"`, and the other providers continue
normally.

`LINKEDIN_MAX_PAGES` (default `5`, 25 cards/page) controls list depth;
`LINKEDIN_FRESHNESS_DAYS` (default `30`, `0` to disable) sets the `f_TPR`
recency filter; `LINKEDIN_ENRICH_MAX` (default `12`) caps per-offer guest
enrichment (only offers still missing a description, relevance-ordered,
deadline-bounded â€” guest fetches are throttled to ~1 req/host/s). When
`preferred_companies` is set and the first pass came back short, the provider
runs **one** extra guest-API query per preferred company and merges by
`source_job_id`.

### Freshness

The database is a **cache / index**, never proof a job is live. Every
offer carries `posted_at` Â· `scraped_at` Â· `last_verified_at` Â·
`expires_at` Â· `is_active`. Freshness is recomputed on every read:

- **fresh** â€” verified within `JOB_FRESH_TTL_HOURS` (24h)
- **stale** â€” older, but within `JOB_STALE_TTL_DAYS` (14d)
- **expired** â€” past `expires_at`, `is_active=False`, or too old to trust
- **unknown** â€” no dates at all

`GET /api/jobs/{id}` on a **stale** row **revalidates** it against its
provider. Revalidation is **tri-state**: `gone` (source 404/removed) â†’
`is_active=False`; `alive` â†’ `last_verified_at` bumped; **`unknown`
(provider errored / blocked) â†’ previous state kept** â€” a transient outage
never marks a job dead. An expired/inactive offer is **never** returned as
an active search result, and applying to one yields `unavailable`.

### Deduplication

Strong identity â€” same `(source, source_job_id)` or same **canonical URL** â€”
always merges. `normalize_url` keeps functional query params (`gh_jid`,
`jobId`â€¦) and drops only `utm_*` + a referral blocklist, so two different
jobs on one path are not collapsed. The softer signal â€” same
`slug(company)|slug(title)|slug(city)`, or title similarity â‰¥
`JOB_DEDUP_TITLE_RATIO` (0.90) within the same company + city â€” merges **only**
when the two are compatible: a job and an internship, or two different
both-set experience levels, are separate openings. **Different cities are
never merged.** A merged offer keeps the highest-priority source, unions
skills, keeps the longest description and earliest `posted_at`, and exposes
every other URL in `also_seen_on`. The fuzzy pass is bucketed by
`(company, city)` â€” O(nÂ·k), not O(nÂ²).

### Ranking

Deterministic and **LLM-free** â€” a weighted blend of title/query overlap
(0.30), skills overlap (0.20), location/remote fit (0.15), job/internship
type (0.12), experience level (0.08), language (0.05), freshness (0.10),
plus a `preferred_companies` boost; `excluded_companies` are removed
entirely.

### Cache

Reuses `services.cache_service.cache`. Key
`v{CACHE_VERSION}:jobs:search:{digest(request âˆ’ pagination)}`, TTL
`JOB_SEARCH_CACHE_TTL` (900s, never indefinite). Pagination is applied
*after* the cache, and freshness filtering still runs on a cache hit so a
cached row can never resurface as active past its TTL.

### Application workflow

`apply` verifies the offer exists + is live, verifies the selected CV /
cover letter belong to the caller (`403` otherwise), **auto-selects the most
relevant stored CV** when none is given (offer-language match â†’ higher ATS score
â†’ most recent), computes a deterministic LLM-free **match score**, resolves the
real external URL via the offer's provider, and stores a `JobApplication` with
the user's context/answers plus provenance (`source`, `normalized_job_key`,
`match_score`, `attempt_count`, `duration_ms`, `error_detail`). A **duplicate
guard** returns the existing record (`status: duplicate`) when the same user
already applied to the same posting â€” matched on the offer row *or* the
normalised `company|title|city` identity. It returns one of `manual_required` /
`requires_user_action` / `unavailable` / `failed` / `duplicate`. **There is no
`submitted` status** â€” the service never completes an application on an external
site (that needs the anti-bot / auth / CAPTCHA flows we do not automate).

### Administration (`/api/admin/*`, superadmin only)

`User.is_superadmin` gates the admin API. It is in **no** request schema
(signup, the profile PUT and the context POST all ignore an injected
`is_superadmin`); the first admin is set by `SUPERADMIN_EMAILS` on startup
(existing accounts only, never demotes), then by another superadmin's `PATCH`.
Self-lockout is blocked (you cannot deactivate yourself, revoke your own
superadmin, or remove the last one).

- **Users** â€” `GET/PATCH /api/admin/users[/{id}]`: list (with per-user CV /
  letter / application counts), toggle `is_active` / `is_superadmin`.
- **ATS boards** â€” `GET/POST/PATCH/DELETE /api/admin/ats-boards` (+
  `POST /test` for an unsaved token, `POST /{id}/test` which persists the result
  on the row): a **DB overlay** on top of `data/ats_boards/*.txt` +
  `<PROVIDER>_BOARDS` â€” enabled rows add tokens, disabled rows remove them. Zero
  rows == the pre-existing file/env behaviour. Slug / `https` validation kept;
  the full SSRF guard still runs at fetch time.
- **Provider stats** â€” `GET /api/admin/providers`: runs / success rate / total
  offers collected / avg + p95 latency / consecutive failures / `health_note`.
- **Usage / application / document stats** â€” `GET /api/admin/stats/*`: live SQL
  aggregates, nothing fabricated. DAU/WAU/MAU/YAU come from the **`usage_events`**
  log (distinct active users per window) when it holds any rows, else the
  `last_active_at` fallback. Per-user rows carry `session_count` /
  `search_count` / `job_view_count`; application stats carry `avg_match_score`,
  `duplicate_count`, `cv_used_count`, â€¦
- **LLM** â€” `GET /api/admin/llm` (model / endpoint / limits / provider /
  `available_providers`, **never the key**); `PATCH /api/admin/llm` changes any
  of those at runtime (no `api_key` field â€” keys stay environment secrets),
  persisted + health-checked + auto-rolled-back on failure;
  `POST /api/admin/llm/test` runs a minimal probe.
- **Dashboard** â€” `GET /api/admin/dashboard`: usage + apps + docs + provider
  stats + a per-provider **boards** section + LLM config, 60 s cache
  (`?refresh=true` to bypass).

### LLM providers

The LLM layer (`services/llm/`) supports **5 backends**, all speaking the
OpenAI Chat Completions API (so no extra dependency): `nvidia` (default),
`openai`, `gemini` (via Google's OpenAI-compatible endpoint),
`openai_compatible`, `custom`. Pick one with `LLM_PROVIDER`; each has its own
`<PROVIDER>_API_KEY` / `_MODEL` / `_BASE_URL` block that wins over the generic
`LLM_*` fallback. Business code is unchanged â€” it still calls
`services.gemini_client.call_gemini`, which keeps the SHA-256 response cache,
the per-minute rate limit and the call counters and delegates to the active
provider (which walks its own model fallback chain).

### User profile & matching

`GET/PUT/DELETE /api/profile` stores a persistent, owner-scoped
`UserProfile` (`target_titles`, `skills`, `locations`, `remote_preference`,
`salary_min/max`, `employment_types`, `sectors`, `excluded_keywords`, â€¦).
It feeds two things: the **preference extractor** (the agent merges
the profile as the base, and can write the resolved context back with
`save_to_profile: true`), and the **auto-apply matcher**
(`services/job_match_service`) which produces `match_score` + `match_reasons`
+ `missing_skills` on every `/api/jobs/{id}/apply`. With `prepare: true` the
apply returns status `prepared`; with `generate_letter: true` (+ `cv_profile`)
it renders a tailored cover letter through the existing letter pipeline and
links it. It still **never submits** on an external site.

### Security

SSRF guard in `services/providers/http.py` (HTTPS-only, per-provider host
allow-list, DNS resolution with private / loopback / link-local / reserved /
RFC 6598 carrier-grade-NAT (`100.64.0.0/10`) IP rejection re-checked on
every redirect hop), response-size cap (`JOB_HTTP_MAX_BYTES`, or a per-call
`max_bytes` for ATS JSON boards â€” still streamed + enforced), bounded
redirects, per-host politeness delay, honest identifying User-Agent,
retry only on connection errors / 5xx, and a POST `json_body` that is sent
on the first hop only (never replayed across a redirect). Scraped HTML is
converted to safe **plain text** before it is ever stored or returned. Text
that will reach
the LLM (the conversation agent) is wrapped in explicit delimiters and marked
as untrusted data â€” a job description / conversation can never become an
instruction. All routes require JWT; applications, saved jobs, CVs and
cover letters are strictly owner-scoped.

### Adding a provider

1. `services/providers/<name>_provider.py` â€” subclass `JobProvider`, set
   `name` / `allowed_hosts` / `application_priority`, implement `_search`
   (do all I/O via `services.providers.http`), optionally `revalidate` /
   `application_method`. Normalise to `NormalizedOffer`.
2. Register it in `services/providers/registry.py` (`_PROVIDER_CLASSES`).
3. Add it to `JOBS_ENABLED_PROVIDERS`.
4. Drop a fixture in `tests/fixtures/jobs/` and a test.

No change to `job_search_service.py` is ever required.

### Adding an ATS board (no code)

Two ways, merged together:

- **Bundled** â€” edit `data/ats_boards/<provider>.txt` (one token per line,
  `#` comments allowed). Ships with real public boards for
  `greenhouse` / `lever` / `ashby` / `smartrecruiters` / `rippling`.
- **Operator** â€” set `<X>_BOARDS_FILE` to your own file, and/or append to the
  `<X>_BOARDS` env var (CSV). Both are *added on top of* the bundled list.

`ATS_USE_BUNDLED_BOARDS=false` drops the shipped lists. Every token is
`valid_slug`-validated (URL configs go through the same `http.validate_url`
SSRF guard); the merged list is de-duplicated and capped at `ATS_MAX_BOARDS`.
A token that 404s / is blocked is silently skipped; the provider only reports
`blocked` / `temporarily_unavailable` when **every** configured board fails.

| Provider | Env var (+ `_FILE`) | What to add | Where to find it |
|---|---|---|---|
| greenhouse | `GREENHOUSE_BOARDS` | slug | `boards.greenhouse.io/<slug>` |
| lever | `LEVER_BOARDS` | slug | `jobs.lever.co/<slug>` |
| ashby | `ASHBY_BOARDS` | slug | `jobs.ashbyhq.com/<slug>` |
| workday | `WORKDAY_TENANTS` | full board URL | `https://<tenant>.wdN.myworkdayjobs.com/<Site>` |
| oracle_hcm | `ORACLE_HCM_SITES` | full CandidateExperience URL | must be a `*.oraclecloud.com` host |
| smartrecruiters | `SMARTRECRUITERS_BOARDS` | slug (**case-sensitive**) | `jobs.smartrecruiters.com/<Slug>` |
| rippling | `RIPPLING_BOARDS` | board slug | `ats.rippling.com/<slug>` |
| talentbrew | `TALENTBREW_BOARDS` | full `â€¦/search-jobs/results?â€¦` URL | the site's network tab (XHR) |
| phenom | `PHENOM_BOARDS` | JSON `{company, endpoint, payload}` | the site's `/widgets` POST call |

Slugs are validated (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`) â€” a bad value is
dropped, not spliced into a request path. URL-based configs go through the
same `http.validate_url` SSRF guard as every other fetch.

### The headless browser (`BrowserSessionManager`)

`services/providers/browser.py` owns a **single, lock-serialised, headless
Chromium session** shared by browser-based providers (today: only
`career_pages`). It is **off by default** (`BROWSER_ENABLED=false`) and
every browser provider self-disables when it is off.

- **Lazy** â€” the WebDriver starts on the first `get_page()`, never at
  import. `import selenium` missing or `BROWSER_ENABLED=false` â†’
  `is_available()` is `False`.
- **One driver, serialised** by an `RLock` â€” the search fan-out is a
  thread pool, so at most one page renders at a time (no "10 Chromes").
- **SSRF-guarded** â€” every navigation goes through the same
  `http.validate_url` (HTTPS-only, private/loopback/reserved-IP rejection).
- **Recycled** after `BROWSER_MAX_PAGES_PER_SESSION` renders, **reaped**
  after `BROWSER_IDLE_SHUTDOWN_SECONDS` idle, **recovered once** on a
  `WebDriverException`; `main.lifespan` calls `browser_session.shutdown()`.
- No credentials, no cookies, no profile persistence, no authenticated
  sessions, and `career_pages` refuses any `linkedin.com` URL outright.

Docker: the image installs `chromium` + `chromium-driver` via the
`INSTALL_BROWSER=true` build ARG (`docker build --build-arg
INSTALL_BROWSER=false` to skip); `docker-compose.yml` sets `shm_size:
512m` and passes `BROWSER_ENABLED` through. The image still boots fine
with the browser disabled.

---

## Multilingual support (FR / EN)

Every endpoint that produces text accepts a `language` field, validated by
Pydantic as `Literal["fr", "en"]` â€” any other value is rejected with `422`
(the extraction endpoint takes it as an optional form field and
auto-detects otherwise).

| Endpoint | What `language` controls |
|---|---|
| `POST /api/extract-cv` | The language of the LLM section prompts, and the `language` echoed on the response. Section-header detection works for **both** languages automatically regardless (`EDUCATION`/`FORMATION`/`ACADEMIC BACKGROUND`, `WORK EXPERIENCE`/`EXPÃ‰RIENCE PROFESSIONNELLE`, `SKILLS`/`COMPÃ‰TENCES`, `PROJECTS`/`PROJETS`, `LANGUAGES`/`LANGUES`, â€¦), and `detected_language` always reports what was actually found in the document. |
| `POST /api/generate-cv` Â· `POST /api/optimize-existing-cv` | The generated professional summary, career objective, optimised bullet wording, skill-category names, **PDF section titles** ("Skills" â†’ "CompÃ©tences", "Professional Experience" â†’ "ExpÃ©rience Professionnelle"), and inline labels ("Tech:" â†’ "Technos:", "Present" â†’ "PrÃ©sent", duration units). |
| `POST /api/generate-letter` | The whole letter body, the PDF's subject label ("Subject:" â†’ "Objet :"), the subject fallback ("Application for the X position" â†’ "Candidature au poste de X") and the date format. |

**The candidate's own factual data is never translated** â€” names,
companies, job titles, dates, technologies and certifications are passed
through verbatim. Only newly generated prose and fixed UI labels change
with `language`. This is enforced by a single language-directive line
injected into each LLM prompt; the prompts' rules and JSON schemas are
otherwise identical between languages.

---

## ATS optimisation

`services/ats_optimizer.py` runs in one of two modes:

- **Job-targeted** (a `job_description` is provided): extracts categorised
  keywords from the posting, computes a keyword-match score against the
  profile, and â€” if the score is below 80 % or keywords are missing â€”
  asks the LLM to weave the missing keywords **naturally** into the
  summary and bullet points.
- **General-purpose** (no job description): writes a strong,
  domain-specific summary and objective without targeting any employer.

In both modes the LLM is instructed never to fabricate metrics,
technologies or achievements â€” only to rephrase and reprioritise what is
already in the profile. If the score is already â‰¥ 80 % with no missing
keywords, the optimiser **skips the LLM call entirely** and reformulates
locally.

The final `X-ATS-Score` header is this keyword-match percentage (0â€“100).

---

## Dates

Extraction is deliberately conservative about dates (accuracy > completeness):

**Education** dates are kept **exactly as written** â€” "PrÃ©sent" is never
turned into a year:

| CV text | `education` |
|---|---|
| `"2022 - 2024"` | `start_date: "2022"`, `end_date: "2024"` |
| `"Septembre 2022 - Juin 2024"` | `start_date: "Septembre 2022"`, `end_date: "Juin 2024"` |
| `"2022 - PrÃ©sent"` | `start_date: "2022"`, `end_date: "PrÃ©sent"` |
| `"Depuis 2022"` | `start_date: "2022"`, `end_date: "PrÃ©sent"` |
| no date | `start_date: null`, `end_date: null` |

**Experience** `period` is a **duration**:

| CV text | `experience.period` |
|---|---|
| `"Stage de trois mois"` | `"3 mois"` (the stated duration, kept) |
| `"Internship - 2 months"` | `"2 months"` |
| `"Jan 2023 - Mar 2024"` | `"1 an 3 mois"` / `"1 year 3 months"` (computed) |
| `"2022 - 2024"` | `"2 ans"` / `"2 years"` (computed) |
| `"2020 - Present"` | computed up to today |
| no date and no duration | `null` |

A stated duration is **never** turned into invented calendar dates. The
current date used for an ongoing role is read dynamically
(`datetime.now()` in `services/date_parser.py`) â€” never written into a
prompt. Each section is parsed in its own LLM call, so a work-experience
date can never leak onto an education entry.

## Hyperlink extraction (Â§4)

A rÃ©sumÃ© often shows only the word **"LinkedIn"** as visible text, with
the real URL hidden in a **PDF link annotation**. Reading the extracted
text alone misses it. `services/link_extractor.py` reads
`page.get_links()`, classifies every URL (linkedin / github / portfolio /
email), and â€” per the source priority below â€” an **annotation URL wins**
over anything guessed from the text and is used **byte-for-byte** (never
reformatted, never rebuilt from a guessed username). DOCX hyperlink
relationships are read the same way.

Source priority: **1.** PDF hyperlink â†’ **2.** URL written in the text â†’
**3.** regex â†’ **4.** Gemini (interpretation only) â†’ **5.** never invented.

The five written forms are all recognised:
`https://www.linkedin.com/in/x`, `https://linkedin.com/in/x`,
`www.linkedin.com/in/x`, `linkedin.com/in/x`,
`https://www.linkedin.com/in/x/`.

## Anti-hallucination contract

Every pipeline treats fabrication as a **hard bug**, not an edge case.

- **Extraction** (`ExtractedCVProfile`, `extraction_models.py`): every
  field is `Optional`. The pipeline never injects a placeholder like
  `"Unknown Company"` or a guessed date â€” a missing value is `null`
  (or `[]` / `""`), the gap is recorded in `validation_issues`, and it
  pulls the section's `confidence_score` down. Section-parser prompts
  share one `ANTI_HALLUCINATION_RULES` block (`services/parser_common.py`,
  EN + FR) forbidding inferred dates / employers / degrees / technologies /
  locations and requiring dates to be copied verbatim.
- **Source grounding** (`services/source_grounding.py`) â€” the safety net
  *after* Gemini: every value the model returned for a section is checked
  back against **that section's own text**, and anything not supported is
  removed:
    - a technology / skill not present in the section text is dropped
      (`["Python", "Django"]` with a source that only says "Python" â†’
      `["Python"]`);
    - an education `start_date`/`end_date` whose year is not in the
      education text is nulled (kills a date borrowed from another entry
      or section);
    - an experience `location` that is merely a substring of the company
      name is nulled ("Polyclinique â€¦ de LaÃ¢youne" â‡ `location: "LaÃ¢youne"`);
    - `degree` that swallowed the speciality is split
      ("Master SystÃ¨mes â€¦" â†’ `degree: "Master"`, `field: "SystÃ¨mes â€¦"`).
- **`interests` / `personal_qualities`** are `list[str]`, split from the
  section block (wrapped lines rejoined). Any contact info, the
  candidate's own name, a job title or a header word that a two-column
  layout folded into the block is dropped.
  Technology lists are also forced to individual strings
  (`"Python, React"` â†’ `["Python", "React"]`) and PDF line-breaks rejoined
  (`"scikit-\nlearn"` â†’ `"scikit-learn"`) â€” never merged when that would
  change meaning.
- **`professional_summary`** is taken only from a real "Profile / Summary
  / Ã€ propos" section; header/contact content that the layout splitter
  folded into it (phone, email, address, LinkedIn) is stripped, and if
  nothing meaningful remains the value is `null` â€” the summary is never
  reconstructed.
- **Language levels** are returned exactly as written
  (`"Langue maternelle"`, `"Courant"`, `"B2"`) â€” extraction does not
  translate or normalise them.
- **Generation** (`CVProfile`, `cv_models.py`): the data has been
  human-validated by this stage, so `name` / `email` / `phone` are
  required â€” but the optimisation prompts still explicitly forbid
  inventing new facts, and the analyzer's JSON-parse fallback is
  deliberately conservative (no invented seniority label, strengths or
  templated summary).
- **Cover letter**: the letter prompt forbids bracket placeholders
  (`[Hiring Manager]`, `[Company]`) and restricts the model to facts
  already in the profile. When the job description yields no company name,
  the letter's recipient block and the position in the subject line are
  simply **omitted** â€” never filled with `"the Company"` / `"the advertised
  position"` (the fallbacks the original standalone service used).

There are **no** default sentinel values like `2020`, `"Unknown Company"`
or `"Professional"` anywhere in the codebase.

---

## Testing

The suite lives in `tests/` and **never hits a real LLM** â€” `call_gemini`
is patched in every consumer module via the `mock_llm` fixture
(`tests/conftest.py`).

```bash
pip install -r requirements-dev.txt
pytest                       # 892 unit + integration tests, ~3 min
```

The suite runs the database on a throwaway SQLite file, replaces MinIO
with an in-memory store, forces the app cache to memory, and **blocks all
outbound job-provider HTTP** (a network safety-net) â€” no external services
are needed. `test_auth.py`, `test_cache.py`, `test_minio.py`,
`test_cvs.py` cover auth / cache / MinIO / ownership. The **job search**
(370+ tests) is covered by `test_jobs_schemas.py`, `test_providers_http.py`
(SSRF incl. CGNAT + `json_body` + per-call `max_bytes` + circuit breaker),
`test_provider_linkedin.py` (strategy fallback + isolation + enrichment +
authwall â†’ `auth_required`), `test_provider_indeed.py`,
`test_provider_json_apis.py`, `test_provider_wwr.py`,
`test_provider_hackernews.py`, `test_provider_greenhouse_lever.py`,
`test_provider_ashby.py` / `_workday` / `_oracle_hcm` / `_smartrecruiters` /
`_rippling` / `_phenom` / `_talentbrew.py` (one per ATS: parse + pagination +
enrichment + empty/404/403/429/malformed), `test_ats_security.py` (slug/id
sanitisation + SSRF + untrusted-response sanitisation),
`test_jobs_ats_integration.py` (real ATS providers end-to-end through
`JobSearchService`), `test_ats_pagination.py` (keyword search reads past the
first page, deadline-bounded), `test_ats_boards_config.py` (bundled + file +
env board-list merge / dedup / cap / validation), `test_ats_enrichment.py`
(relevance-ordered enrichment budget), `test_ats_security.py`,
`test_provider_robustness.py` (every ATS Ã— timeout/5xx/429/403/SSRF/junk/
null-rows/oversized â€” never raises), `test_provider_metrics.py` (run metrics
+ `health_note` broken-provider detection), `test_parsing_dates.py` (`to_utc`
across every real format; a bad value is `null`, never a guess),
`test_provider_career_pages.py`, `test_provider_state.py` (outcome â†’ state
matrix), `test_browser_session.py` (lifecycle with a fake WebDriver â€” no real
Chromium), `test_job_search_service.py` (orchestration + cache +
one-source-down + `provider_states`), `test_job_dedup.py` (URL
canonicalisation + fuzzy-merge guards), `test_job_freshness.py`,
`test_job_ranking.py`, `test_jobs_api.py`, `test_job_applications.py` â€” all
offline, with stored HTML/JSON/RSS fixtures under `tests/fixtures/jobs/`.

| File | Scope |
|---|---|
| `test_section_splitter.py` | FR/EN header detection, language detection, section isolation |
| `test_contact_extractor.py` | email/phone/URLs, name (Title/UPPER/hyphenated/split/2-column), labelled address & nationality, no fabrication |
| `test_date_parser.py` | every supported date format & range, "never invents" |
| `test_local_section_extractor.py` | cert years, language levels kept **verbatim** (`Langue maternelle`, accented FR), local skills fallback |
| `test_parsers.py` | the 4 section parsers + `parser_common` + `ResumeStructurer` facade + Level 5 fallback |
| `test_skills_parser.py` + `test_skills_extraction.py` | **flat `list[str]`** skills: no categories ever, conjunction / grouped-string split, line-cut repair, duplicates, invented-skill grounding, FR/EN, local fallback |
| `test_link_extractor.py` | **spec Â§4/Â§23**: LinkedIn URL recovered from a PDF annotation when it is NOT in the text; 6 written URL formats; annotation URL > text guess; an overflow URL goes to `others` |
| `test_project_links.py` | a repo / demo URL (from the projects text or a PDF annotation) is attached to the matching project; a bare GitHub profile is not; an existing value is never overwritten |
| `test_column_detector.py` | pure page geometry: single vs two-column, full-width header peel, staggered column start, false positives (centred title / one-row contact / stacked blocks), per-page purity, confidence |
| `test_layout_pipeline.py` | real ReportLab PDFs end-to-end (**spec Â§18 Aâ€“E, I, J**): single-column, two-column, two-column + full-width header, page 1 two-col / page 2 single-col, LinkedIn-only-as-annotation, ambiguous multi-zone â†’ flagged, FR + EN |
| `test_text_cleaner.py` | wrapped-line rejoin (`"des
prioritÃ©s"` â†’ one item) without crossing a section/date boundary; list splitting |
| `test_extraction_dates.py` | education dates kept **verbatim** (`"PrÃ©sent"` stays), experience `period` as a **computed duration**, no date borrowed across sections, split technologies |
| `test_extraction_precision.py` | **spec Â§17**: degree/field split (4 examples), durationâ†’`"3 mois"`, calendar period verbatim, invented-technology removal, cross-section date rejection, company-derived-location removal, summary purity |
| `test_validator_and_scorer.py` | validation findings, de-duplication, tech splitting, end-before-start, confidence bounds |
| `test_pipeline_integration.py` | full extraction pipeline: section-scoped calls (â‰¤ 4, one per section), no fabrication, language hint vs detection, fallback trigger, response contract |
| `test_generation_pipeline.py` | ProfileAnalyzer â†’ ATS â†’ CVGenerator â†’ PDFGenerator, FR + EN, valid PDF, conservative fallbacks |
| `test_api_integration.py` | every endpoint via `TestClient`: contracts, `language` field, `4xx` guards, streaming PDF + headers (incl. `/api/stats` and `/api/optimize-existing-cv` ATS headers), no disk writes |
| `test_letter_generation.py` | cover letter: `JobCompanyParser` (empty JD â†’ no call, bad JSON â†’ empty), `LetterGenerator` (language directive), `LetterPDFGenerator` (`render_to_bytes`, FR/EN labels, no-company still renders), `POST /api/generate-letter` (FR + EN, `422` guards, no disk write) |
| `test_file_extraction.py` | real PDF (ReportLab) + DOCX (python-docx, document-order paragraphs + tables) â†’ text â†’ pipeline |
| `test_live_extraction.py` | **opt-in**, real LLM calls: `CV_LIVE_RESUME_DIR=<dir> pytest tests/test_live_extraction.py -s` |

### Postman / Newman

`postman/CV-Assistant.postman_collection.json` is the **v2** collection â€”
**39 requests, ~90 assertions** covering the whole authenticated flow:
signup / signin / `/me` (+ `401` without a token), the verification-code
password reset (dev-mode code, no-enumeration, single-use `400`,
reuse `400`), `extract-cv` (Bearer + guards), `generate-cv` /
`optimize-existing-cv` / `generate-letter` (JSON + MinIO presigned URL),
the **application-cache hit** (identical `generate-cv` repeat served in
milliseconds with zero new LLM calls), `cvs` / `letters` list Â· get Â·
download Â· delete, and the per-user **`403`** ownership checks.

It needs a full live stack (DB + MinIO + `NVIDIA_API_KEY`) with
`PASSWORD_RESET_DEV_MODE=true` so the reset code is returned in the body.
Bring the stack up with compose, then:

```bash
docker compose up -d          # api + postgres + redis + minio
newman run postman/CV-Assistant.postman_collection.json \
  -e postman/CV-Assistant.postman_environment.json \
  --working-dir postman --timeout-request 240000
```

The id-dependent requests self-`skipRequest()` if an upstream real-LLM
call was rate-limited by the NVIDIA backend, so a transient provider
outage shows as *skipped*, never as a cascade of false failures.

---

## Known limitations

- **Two-column / designer PDF templates.** `column_detector.py` decides
  the layout **per page** from line coordinates (real vertical gutter,
  two populated zones, full-width header band above the columns) and
  `pdf_layout_reader.py` rebuilds the reading order (header, then the whole
  left column, then the whole right column â€” never zipped row-by-row).
  This handles clean single- and two-column CVs, sidebar CVs and a
  full-width name/title bar above two columns. **Heavily-styled Canva /
  Europass templates with three or more overlapping zones (or
  character-level text corruption from the PDF itself, e.g. `"e expertis e
  avÃ©rÃ© e"`) cannot be reliably ordered** â€” reading order is genuinely
  ambiguous. When that is detected the page is flagged
  `low_layout_confidence`: every `confidence_score` is capped at 55 and a
  `"layout"` `validation_issue` (severity `warning`) is raised, which is
  exactly what the frontend review form exists to catch. A green test run
  does **not** guarantee a real CV reconstructed cleanly â€” always check the
  `layout` validation issue and the reconstructed text for such templates.
- **Scanned / image-only PDFs** yield no text (no OCR). The endpoint
  returns `422` with a clear message.
- **Broken source data** (e.g. a LinkedIn URL split across two lines in
  the PDF) is surfaced as a missing/partial field, never patched up.

---

## Project structure

See [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md) for the full
file-by-file breakdown, execution flow, and module dependency graph.

## CI/CD Pipeline

Ce projet utilise **GitHub Actions** pour assurer l'intégration continue (CI). Le pipeline est défini dans .github/workflows/ci.yml et s'exécute automatiquement lors des push et pull_request sur la branche principale.

### Étapes du Pipeline (CI)

1. **Install & Test** :
   - Configure un environnement Python 3.11 isolé.
   - Utilise un cache pour les dépendances (pip) afin de réduire le temps d'exécution.
   - Installe les dépendances du projet depuis equirements.txt et equirements-dev.txt.
   - Exécute les tests unitaires via pytest. Les tests utilisent une base de données SQLite en mémoire (configurée dans 	ests/conftest.py) et mockent les services externes (Redis, MinIO, LLMs) pour garantir que l'exécution soit rapide, fiable, et ne nécessite pas d'infrastructure lourde.

2. **Docker Build Validation** :
   - Vérifie que l'image Docker du backend peut être construite correctement à l'aide du Dockerfile du projet.
   - Cette étape assure que les modifications du code ne cassent pas le processus de conteneurisation.

### Déploiement Continu (CD) - Stratégie

Actuellement, le projet est prêt pour intégrer une phase de Déploiement Continu (CD). Une fois l'infrastructure cible définie (ex: AWS, Render, VPS), la pipeline peut être étendue de la manière suivante :
- **Build & Push** : Après le succès des tests, construire l'image Docker finale et la pousser vers un registre de conteneurs (ex: Docker Hub ou GitHub Container Registry).
- **Déploiement Automatique** : Déclencher un webhook ou se connecter via SSH au serveur cible pour pull la nouvelle image et redémarrer les services (ex: docker compose pull && docker compose up -d).
