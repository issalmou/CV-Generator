# Refactor Report — CV Generator finalisation

> **v2.0 (2026-09-02) — Authentication, MinIO & Cache.** A separate
> evolution layered on top of everything below. See
> [§8 — Auth + MinIO + Cache](#8--auth--minio--cache-v20) at the end of
> this document. **v2.1 (2026-09-02) — Job & Internship Search** is a further
> evolution — see [§9](#9--job--internship-search--application-system-v21).
> **v2.2 — Provider states + Browser + ATS** ([§10](#10--provider-states--browser--ats-providers-v22)).
> **v2.3 (2026-09-03) — 7 ATS HTTP providers** ([§11](#11--ats-http-providers-v23)).
> **v2.4 (2026-09-03) — collection optimisation + observability** ([§12](#12--collection-optimisation-linkedin-guest-depth-observability-v24)).
> **v2.5 (2026-09-04) — bounded fan-out, job links, auto-apply matching, LLM
> config, superadmin + statistics** ([§13](#13--platform-hardening-async-fan-out-superadmin-statistics-llm-config-v25)).
> Tests after v2.5: **892 passed, 1 skipped**. (v2.4: 771.)
> **v2.6 (2026-09-04) — LLM multi-provider, user profile, usage events, richer
> auto-apply, shared fan-out pool** ([§14](#14--v26--llm-multi-provider-profil-utilisateur-événements-dusage-auto-candidature-enrichie)).
> Tests after v2.6: **988 passed, 1 skipped**.
> **v2.7 (2026-09-04) — LLM unifié (une seule implémentation, 7 presets +mistral
> +groq), dashboard par utilisateur (`GET /api/dashboard`), stats superadmin par
> période** ([§15](#15--v27--llm-unifié-anti-redondance-dashboard-utilisateur-stats-par-période)).
> Tests after v2.7: **1010 passed, 1 skipped**. Final report:
> `RAPPORT_PHASES_65_78.md`.
> **v2.8 (2026-09-04) — cache Redis du dashboard, mémoire conversationnelle
> persistante (`/api/conversations/*`), e-mail UNIQUE au niveau base**
> ([§16](#16--v28--redis-dashboard-mémoire-conversationnelle-e-mail-unique)).
> Tests after v2.8: **1059 passed, 1 skipped**.

Date: 2026-08-30 · Tests: **274 passed, 1 skipped** (opt-in live test) · `vulture --min-confidence 80`: **clean**

This round finalised the project: removed the last dead code from the old
architecture, added `/api/optimize-existing-cv` and `/api/stats`,
**integrated the standalone cover-letter service** as `/api/generate-letter`
(FR/EN, no new dependency), improved DOCX robustness, and completed the
docs. The extraction/generation contracts are unchanged.

---

## 1. Final project structure

```
cv-generator/
├── main.py                       6 endpoints; every PDF streamed, never persisted
├── extraction_router.py          POST /api/extract-cv
├── cv_models.py                  GENERATION + LETTER Pydantic contracts (+ GenerateLetterRequest)
├── extraction_models.py          EXTRACTION contract (unchanged)
├── requirements.txt              (unchanged — no new dependency)
├── requirements-dev.txt · pytest.ini · Dockerfile · docker-compose.yml · .env.example
├── README.md · PROJECT_STRUCTURE.md · REFACTOR_REPORT.md
│
├── services/
│   ├── gemini_client.py          SOLE LLM client (cache · 30/min rate-limit · model fallback · stats)
│   │  ── extraction pipeline ──
│   ├── link_extractor.py · column_detector.py · pdf_layout_reader.py · resume_text_extractor.py
│   ├── text_cleaner.py · section_splitter.py · contact_extractor.py · date_parser.py
│   ├── local_section_extractor.py · parser_common.py
│   ├── experience_parser.py · education_parser.py · project_parser.py · skills_parser.py
│   ├── resume_structurer.py · source_grounding.py · resume_validator.py · confidence_scorer.py
│   ├── resume_parser_pipeline.py
│   │  ── generation pipeline ──
│   ├── profile_analyzer.py · ats_optimizer.py · cv_generator.py · pdf_generator.py
│   │  ── cover-letter pipeline (NEW) ──
│   ├── job_company_parser.py     JD → {company_name, position, location, recipient, company_address}
│   ├── letter_generator.py       (profile + JD + company) → letter body, FR/EN
│   └── letter_pdf_generator.py   ReportLab → PDF bytes; FR/EN labels; no placeholder
│
├── tests/  (21 files, 274 tests)  … + test_letter_generation.py (NEW)
└── postman/
```

27 service modules (24 + 3 new). No orphan modules; `vulture` at 80 % confidence reports nothing.

---

## 2. Deleted (dead code — no whole file removed)

| Symbol | File | Why |
|---|---|---|
| `_titlecase_first_month()` | `services/date_parser.py` | never called |
| `COL_WHITE` | `services/pdf_generator.py` | never referenced |
| `clear_cache()` | `services/gemini_client.py` | not exposed, not tested |
| `DocumentLayoutResult.ambiguous_layout` (property) | `services/pdf_layout_reader.py` | never read |
| `PageLayoutResult.column_count` (field + assignment) | `services/pdf_layout_reader.py` | write-only |
| `prefix` parameter of `_apply_issue_penalty()` | `services/confidence_scorer.py` | always `""`, unused in body |
| `ResumeParserPipeline.parse_file()` | `services/resume_parser_pipeline.py` | no caller (endpoints use `parse_bytes`) |
| `ResumeTextExtractor.extract_text()` + `_extract_pdf/_docx/_txt()` | `services/resume_text_extractor.py` | only reachable via `parse_file` |
| `ResumeTextExtractor.extract_text_from_bytes()` + `_extract_pdf_bytes()` + `_extract_docx_bytes()` | `services/resume_text_extractor.py` | superseded by `extract_document_from_bytes()`; 3 test asserts retargeted |

`get_stats()` / `_update_stats()` / `_CALL_STATS` were kept — now surfaced by `GET /api/stats`.

---

## 3. New files

| File | Purpose |
|---|---|
| `services/job_company_parser.py` | Parse the letter's addressee (company / position / recipient / address) from a job description. 1 LLM call via `call_gemini` (`request_type="letter_job_company"`); empty JD → no call; LLM/JSON failure → empty dict. |
| `services/letter_generator.py` | Write the cover-letter body in FR/EN from `CVProfile.model_dump()` + JD + company context. 1 LLM call (`request_type="cover_letter"`). Uses only facts already in the profile. |
| `services/letter_pdf_generator.py` | Render the letter to **PDF bytes** (`render_to_bytes`, no disk). FR/EN subject label / subject fallback / date format. Recipient block omitted when the company is unknown. |
| `tests/test_letter_generation.py` | 17 tests: the 3 services + `POST /api/generate-letter` (FR + EN, `422` guards, no disk write). |
| `REFACTOR_REPORT.md` | This document. |

Ported from `Cv_assistant-letter_generation` (which is left untouched):
the prompt text, the job-parser JSON schema and the ReportLab layout. The
port **drops** that service's `google-genai` client, its 4-model Gemini
fallback list, its disk writes and its `"the Company"` / `"Hiring Manager"`
placeholder defaults.

---

## 4. Modified files

| File | Change |
|---|---|
| `main.py` | +3 endpoints (`GET /api/stats`, `POST /api/optimize-existing-cv`, `POST /api/generate-letter`); `_run_letter_pipeline` + async wrapper; shared `_pdf_stream` / `_require_llm` / `_llm_error_response` helpers; `generate_cv` refactored onto the shared body. |
| `cv_models.py` | +`GenerateLetterRequest` (`cv_profile`, `job_description` required, `language`, optional `recipient_name` / `company_address`). |
| `services/resume_text_extractor.py` | Trimmed to `extract_document_from_bytes` + helpers; **DOCX read in document order** (paragraphs *and* tables interleaved via `document.element.body`). |
| `services/resume_validator.py` | +`address` sanity check (warns if it captured `@` / a URL). Project `github`/`demo` URL check already present. |
| `services/gemini_client.py` · `pdf_layout_reader.py` · `pdf_generator.py` · `date_parser.py` · `confidence_scorer.py` · `resume_parser_pipeline.py` | dead-code removal (see §2) + docstring/typing touch-ups. |
| `tests/conftest.py` | `_LLM_CONSUMER_MODULES` += `job_company_parser`, `letter_generator`; canned `letter_job_company` / `cover_letter` responses. |
| `tests/test_file_extraction.py` | 3 asserts retargeted to `extract_document_from_bytes(...).text`. |
| `tests/test_api_integration.py` | +`/api/stats` and +`/api/optimize-existing-cv` tests. |
| `README.md` · `PROJECT_STRUCTURE.md` | 6-endpoint API reference; cover-letter workflow; 3-pipeline architecture; updated dependency graph; current extraction schema in the examples. |

**Unchanged on purpose:** `extraction_models.py` (schema kept —
`start_date`/`end_date`, `period`, flat `skills: list[str]`, `gpa` string),
`cv_models.CVProfile` (generation contract, `SkillCategory` kept),
`column_detector` / `source_grounding` / `section_splitter` logic,
`Cv_assistant-letter_generation` (the source service).

---

## 5. API — request examples

```bash
# Health / stats
curl http://localhost:8000/api/health
curl http://localhost:8000/api/stats

# Extraction (unchanged)
curl -X POST http://localhost:8000/api/extract-cv -F "file=@resume.pdf" -F "language=en"

# Generation (unchanged)
curl -X POST http://localhost:8000/api/generate-cv -H "Content-Type: application/json" \
     -o cv.pdf -d '{"language":"fr","job_description":"…","cv_profile":{…}}'

# Re-target an existing CV to a new job description
curl -X POST http://localhost:8000/api/optimize-existing-cv -H "Content-Type: application/json" \
     -o cv.pdf -D - -d '{"language":"en","job_description":"…","cv_profile":{…}}'

# Cover letter (FR or EN)
curl -X POST http://localhost:8000/api/generate-letter -H "Content-Type: application/json" \
     -o cover_letter.pdf -d '{
       "language":"fr",
       "job_description":"Nous recrutons un Full Stack Developer (React + Node.js) chez TechNova, Casablanca.",
       "cv_profile":{"name":"Issalmou Adaaiche","email":"issalmou@example.com","phone":"+212 640 065 118",
                     "experience":[{"company":"Polyclinique …","position":"Full Stack Developer Intern","period":"3 months"}],
                     "skills":[{"category":"AI/ML","skills":["Python","TensorFlow"]}]}
     }'
```

## 6. API — response examples

`GET /api/stats` → `200`
```json
{"total_calls": 12, "cache_hits": 3, "total_input_chars": 41890,
 "total_output_chars": 9204, "cache_size": 9}
```

`POST /api/generate-cv` / `optimize-existing-cv` / `generate-letter` → `200`,
body = raw PDF, metadata in headers:
```
Content-Type: application/pdf
Content-Disposition: attachment; filename="cv_<uuid>_fr.pdf"
X-Generated-CV-Id: <uuid>          X-ATS-Score: 87.5          X-Language: fr
X-ATS-Matched-Count: 9             X-ATS-Missing-Count: 3     (optimize-existing-cv only)
X-Letter-Id: <uuid>               X-Language: en             (generate-letter)
```

`POST /api/extract-cv` → `200` `ExtractCVResponse` (schema unchanged — flat
`skills`, `education.start_date/end_date` + `gpa` string, `experience.period`).

Errors everywhere: `{"status":"error","message":"…"}` with `422` (bad
payload / language), `429` (rate limit), `503` (`NVIDIA_API_KEY` missing),
`500` (unexpected); extraction also `400` / `413` / `415`.

---

## 7. Remaining considerations

- **Cover-letter latency** ≈ 2 LLM round-trips (job parse + letter),
  ~15–20 s against NVIDIA NIM today. Both calls are cached (SHA-256), so a
  retry with the same inputs is instant.
- **Helvetica glyph coverage**: both PDF renderers normalise fancy dashes
  / quotes / ellipsis to ASCII (`_GLYPH_FALLBACKS` in
  `services/letter_pdf_generator.py` and `services/pdf_generator.py`)
  because the built-in Helvetica has no glyph for them — the CV renderer
  applies it recursively over `cv_data` in `_compact_cv_data`.
- **`/api/optimize-existing-cv`** currently runs the identical pipeline to
  `/api/generate-cv` (it re-runs profile analysis too). If a future need
  arises to skip re-analysis for an already-summarised profile, the hook
  is `_generate_cv` in `cv_router.py`.

---

## 8. — Auth + MinIO + Cache (v2.0)

Date: 2026-09-02 · Tests: **305 passed, 1 skipped**

A production evolution layered on the finalised project: **user accounts
(JWT + PostgreSQL)**, **MinIO storage for every generated PDF**, and an
**application-level cache** above the untouched `gemini_client` cache.

### Breaking changes

| Before | After |
|---|---|
| CV endpoints anonymous | **Every** CV endpoint requires `Authorization: Bearer`. Only `/api/health`, `/api/stats`, `/api/auth/*` are public. |
| `generate-cv` / `optimize-existing-cv` / `generate-letter` stream `application/pdf` with `X-*` headers | Return **JSON** `{status, generated_(cv|letter)_id, ats_score?, language, filename, download_url, expires_in}`. The PDF is in MinIO. |
| No database | **PostgreSQL mandatory** (`DATABASE_URL`, checked at startup). SQLAlchemy 2.0, `create_all` on startup, no Alembic. |
| App runs with only `NVIDIA_API_KEY` | Also needs `JWT_SECRET_KEY`; `MINIO_ENDPOINT` needed for the generation routes (else 503). |
| `APP_VERSION = "1.0.0"` | `"2.0.0"` |

### Files added

`config.py`, `database.py`, `auth_router.py`, `cv_router.py`,
`models/{__init__,user,generated_cv,generated_letter,password_reset}.py`,
`schemas/{__init__,auth,cv}.py`, `dependencies/{__init__,auth}.py`,
`services/{auth_service,email_service,minio_service,cache_service,generation_service}.py`,
`tests/{test_auth,test_cache,test_minio,test_cvs}.py`.

### Files modified

| File | Change |
|---|---|
| `main.py` | Slimmed to app + lifespan (config check → `init_db()` → MinIO bucket) + CORS + handlers + health/stats + 3 routers. **All pipeline code moved out** to `services/generation_service.py`. |
| `extraction_router.py` | `+ Depends(get_current_user)`; calls cache-wrapped `run_extraction(...)`; size guard uses `settings.MAX_UPLOAD_SIZE`. |
| `services/resume_parser_pipeline.py` | `+ ResumeParseResult.from_dict()` (pairs with `to_dict()`, used by the extraction cache). |
| `requirements.txt` | `+ sqlalchemy, psycopg2-binary, pydantic-settings, pyjwt, bcrypt, email-validator, minio, redis, httpx` (httpx promoted from dev). |
| `requirements-dev.txt` | `httpx` removed (now inherited). |
| `.env.example` | Every new variable, documented. |
| `docker-compose.yml` | `+ postgres:16, redis:7-alpine, minio/minio` services (+ volumes, healthchecks); `api` gets `depends_on` + `DATABASE_URL`/`REDIS_URL`/`MINIO_*`/`JWT_SECRET_KEY`. |
| `tests/conftest.py` | SQLite test DB (env set before import), `db` / `fake_minio` (autouse) / `clear_cache` (autouse) / `test_user` / `auth_client` / `anon_client` fixtures; `client` kept as an authed alias. |
| `tests/test_api_integration.py`, `tests/test_letter_generation.py` | Reworked to the JSON contract + auth + "no token → 401" + DB-row / MinIO-bytes assertions. |
| `README.md`, `PROJECT_STRUCTURE.md` | v2 sections, env table, flows, dependency graph. |

### Not touched (except the model list)

`services/gemini_client.py` — its SHA-256 response cache, rate limiter,
stats and `os.getenv` are unchanged; it stays the sole LLM client and
sole cache of LLM responses. The one edit is `NVIDIA_MODELS_FALLBACK`:
`nvidia/nemotron-3-nano-30b-a3b` reached end-of-life on 2026-09-01, so
the chain is now `nemotron-3.5-lightning-30b-a3b` →
`nemotron-3-super-120b-a12b` → `openai/gpt-oss-20b` (verified live
2026-09-02) — the routine model refresh the provider forces.

Also untouched: `services/pdf_generator.py`,
`services/letter_pdf_generator.py`, `extraction_models.py`,
`cv_models.py`, every extraction / parser / grounding service,
`Dockerfile`.

### Password reset (two endpoints, verification-code)

`POST /api/auth/forgot-password {email}` → always the same `200`; a
6-digit code is generated (`secrets.randbelow`), SHA-256-hashed, stored
with an expiry, and e-mailed via **Resend API → SMTP → dev-mode**
(logged; returned in the body when `PASSWORD_RESET_DEV_MODE=true`). Prior
unused codes for that user are invalidated.
`POST /api/auth/reset-password {email, code, new_password}` → newest
unused code, `attempts++` (burned at `PASSWORD_RESET_MAX_ATTEMPTS`),
expiry + `secrets.compare_digest` check; success sets a fresh bcrypt hash
and marks the code used. Every failure → the same generic
`400 "Invalid or expired verification code."` (no enumeration, no oracle).

### Cache boundaries (semantic keys, `v{CACHE_VERSION}:` prefix)

| What | Key | Wrap point |
|---|---|---|
| Whole extraction | `extract:{sha256(bytes)}:{lang\|auto}` | `run_extraction` |
| Profile analysis | `profile:analysis:{digest(profile)}:{lang}` | `run_cv_pipeline` |
| ATS keywords | `ats:keywords:{digest(jd)}:{lang}` | `run_cv_pipeline` |
| Content optimisation | `ats:optimize:{digest(profile)}:{digest(jd)}:{lang}` | `run_cv_pipeline` |

`calculate_match_score` and every `render_to_bytes` are local and never
cached. Redis is used automatically when `REDIS_URL` is set, else a
thread-safe in-memory TTL cache.

### Start & test

```bash
docker compose up -d postgres redis minio
export DATABASE_URL=postgresql://cv:cv@localhost:5432/cv_generator
export JWT_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin
uvicorn main:app --port 8000

pytest -q            # 305 passed, 1 skipped — SQLite DB, fake MinIO, memory cache

# Postman (v2 collection: 39 requests, ~90 assertions) against the live stack
docker compose up -d
newman run postman/CV-Assistant.postman_collection.json \
  -e postman/CV-Assistant.postman_environment.json \
  --working-dir postman --timeout-request 240000
```

---

## 9. — Job & Internship Search + Application System (v2.1)

Date: 2026-09-02 · Tests: **434 passed, 1 skipped** (305 pre-existing + 129 new)

A multi-source **job & internship aggregation framework** (not a scraper):
an agent-fillable search context, 10 providers behind a common interface,
normalisation → deduplication → freshness → deterministic ranking, a
unified API, and a manual-only application workflow.

### Non-negotiables honoured

- **One platform = one file** in `services/providers/`; `job_search_service.py`
  has zero platform-specific logic.
- **All provider HTTP through `services/providers/http.py`** — SSRF guard
  (HTTPS-only, per-provider host allow-list, private/loopback/reserved-IP
  rejection re-checked on every redirect hop), response-size cap, bounded
  redirects, per-host politeness delay, retry only on conn-error/5xx.
  `403/429/451/999` → `AccessDenied` → provider `unavailable` (**no evasion**).
- **Strict isolation** — `JobProvider.search()` never raises; a per-future
  timeout + circuit breaker in the orchestrator; several providers failing
  at once still returns the successful ones with an honest `sources` map.
  `/api/jobs/search` is **always `200`**.
- **No Voyager / `li_at` / login / CAPTCHA / stealth / proxy rotation.**
- **Postgres is a cache/index** — `services/job_freshness.py`, tri-state
  revalidation (`gone` deactivates; `unknown` keeps previous state).
- **LLM-free ranking**; **one** `call_gemini` (`job_preference_extraction`),
  conversation delimited + marked untrusted.
- **Applications are never auto-submitted** — `ApplicationStatus` has no
  `submitted` value; `apply` records intent + returns the real external URL.

### Files added

`jobs_router.py`; `models/{job_offer,job_application,saved_job}.py`;
`schemas/{jobs,applications}.py`;
`services/{job_search_service,job_preference_service,job_application_service,job_dedup,job_freshness,job_ranking}.py`;
`services/providers/{__init__,base,http,circuit,registry,parsing}.py`;
`services/providers/{linkedin,indeed,arbeitnow,weworkremotely,hackernews,remotive,jobicy,remoteok,himalayas,adzuna}_provider.py`;
`tests/_jobs_helpers.py`, `tests/fixtures/jobs/*` (14 fixtures);
`tests/test_{jobs_schemas,providers_http,provider_linkedin,provider_indeed,provider_json_apis,provider_wwr,provider_hackernews,job_search_service,job_dedup,job_freshness,job_ranking,jobs_api,job_applications}.py`.

### Files modified

| File | Change |
|---|---|
| `config.py` | +`JOBS_ENABLED_PROVIDERS`, `ADZUNA_APP_ID/KEY`, 15 `JOB_*` tunables, `enabled_providers_list` / `adzuna_configured` props. |
| `main.py` | `+ include_router(jobs_router)`. |
| `models/__init__.py` | re-export the 3 new models (auto-created by `init_db()`). |
| `requirements.txt` | `+ beautifulsoup4>=4.12`, `+ lxml>=5.0`. No browser automation, no fuzzy-match lib (stdlib `difflib`), no feedparser (stdlib `xml.etree`). |
| `.env.example` | every `JOB_*` var documented. |
| `pytest.ini` | `+ markers = real_http`. |
| `tests/conftest.py` | `_LLM_CONSUMER_MODULES += services.job_preference_service`; `job_preference_extraction` branch in `mock_llm`; `fake_http` (offline HTTP) + `_no_real_job_http` (autouse safety-net) + `swap_providers` + `_fast_job_timeouts` fixtures. |
| `README.md`, `PROJECT_STRUCTURE.md` | Workflow 4 section, LinkedIn matrix, provider table, freshness/dedup/ranking/security, "adding a provider". |

### Providers (10)

`linkedin` (multi-strategy A→B list, C→D enrich, circuit breaker) ·
`indeed` (SERP + JSON-LD, Cloudflare → `unavailable`) · `arbeitnow` ·
`weworkremotely` (RSS) · `hackernews` (Algolia) · `remotive` · `jobicy`
(public JSON) · `remoteok` · `himalayas` (best-effort, UA-gated) ·
`adzuna` (optional, self-disables without credentials).

### Endpoints

`POST /api/jobs/context` · `POST /api/jobs/search` · `GET /api/jobs/sources` ·
`GET /api/jobs/{id}` · `POST /api/jobs/{id}/apply` ·
`GET /api/jobs/applications[/{id}]` · `POST|DELETE /api/jobs/{id}/save` ·
`GET /api/jobs/saved`. All JWT-protected; applications / saved jobs / CVs /
letters strictly owner-scoped.

### Verify

```bash
python -m compileall .            # clean
python -c "import main"           # no circular imports
pytest -q                         # 434 passed, 1 skipped
# offline: SQLite DB, fake MinIO, memory cache, ALL job-provider HTTP blocked
```

---

## 10. — Provider states + Browser + ATS providers (v2.2)

Date: 2026-09-02 · Tests: **486 passed, 1 skipped** (434 pre-existing,
unchanged + 52 new)

Three **additive** evolutions of the job-search subsystem — no refactor of
the existing architecture, no authenticated LinkedIn automation.

### A — `ProviderState` machine

- New `schemas.jobs.ProviderState` enum: `available` · `degraded` ·
  `auth_required` · `temporarily_unavailable` · `blocked` · `error` ·
  `disabled`. `ProviderState.to_status()` coarsens to the **unchanged**
  `ProviderStatus` (`available→success`, `degraded→partial`,
  `disabled→disabled`, everything else → `unavailable`).
- `JobSearchResponse` += `provider_states: dict[str, ProviderState]`;
  `ProviderInfo` += `state`. `sources` stays **byte-identical** — existing
  clients and the frontend guide's contract are untouched.
- `services/providers/base.py` — new exceptions `ProviderAuthRequired` /
  `ProviderBlocked` / `ProviderTemporarilyUnavailable` (all `HttpError`
  subclasses, so `except HttpError` still catches them). `search()` maps
  every outcome → a state; **0 results maps to `available`**, never
  `unavailable`. Circuit-open → `temporarily_unavailable`.
- `job_search_service.py` builds `provider_states` alongside `sources`
  (timeout → `temporarily_unavailable`, crash → `error`); it round-trips
  through the result cache exactly as `sources` does.

### B — `LinkedInProvider` hardened (still 100 % public, no login)

- Authwall / `/checkpoint/challenge` / `/uas/login` detection in every
  list strategy → `ProviderAuthRequired` → `auth_required`.
- All-strategies-failed with `403`/`451`/`999` in the error trail →
  `ProviderBlocked` → `blocked`; otherwise `temporarily_unavailable`.
- Deeper pagination via `LINKEDIN_MAX_PAGES` (default 5).
- `preferred_companies` → **one** extra guest-API pass per company,
  merged by `source_job_id`.
- Docstring states the authenticated path is *intentionally not
  implemented* (LinkedIn User Agreement).

### C — Browser session + Greenhouse / Lever / career_pages

- `services/providers/browser.py` — `BrowserSessionManager`: one
  lock-serialised headless Chromium session, lazy start, recycle after
  `BROWSER_MAX_PAGES_PER_SESSION`, idle reap after
  `BROWSER_IDLE_SHUTDOWN_SECONDS`, recover-once on `WebDriverException`,
  every navigation gated by `http.validate_url`. **`BROWSER_ENABLED=false`
  by default.** No credentials / cookies / login / profile. `main.lifespan`
  shutdown calls `browser_session.shutdown()`.
- `services/providers/browser_base.py` — `BrowserJobProvider` base.
- `greenhouse_provider.py` / `lever_provider.py` — **no browser**, public
  board JSON APIs, one token per company (`GREENHOUSE_BOARDS` /
  `LEVER_BOARDS`); one failing board is skipped, only all-fail marks the
  provider (`blocked` if every board denied, else
  `temporarily_unavailable`).
- `career_pages_provider.py` — renders `CAREER_PAGE_URLS` with the browser,
  extracts schema.org `JobPosting` from the rendered DOM. Opt-in; refuses
  any `linkedin.com` URL.

### Files added

`services/providers/{browser,browser_base,greenhouse_provider,lever_provider,career_pages_provider}.py`;
`tests/test_{provider_state,browser_session,provider_greenhouse_lever,provider_career_pages}.py`;
`tests/fixtures/jobs/{greenhouse.json,lever.json,career_page_jsonld.html,linkedin_authwall.html}`.

Removed: `services/browser/browser_session_manager.py` — an orphan stub
from an earlier (declined) authenticated-session design; nothing imported
it, superseded by `services/providers/browser.py`.

### Files modified

| File | Change |
|---|---|
| `schemas/jobs.py` | `ProviderState` enum + `to_status()`; `JobSearchResponse.provider_states`; `ProviderInfo.state`. |
| `services/providers/base.py` | 3 new exceptions; `ProviderResult.state`; `last_state`; outcome→state mapping in `search()`. |
| `services/providers/registry.py` | register `greenhouse` / `lever` / `career_pages`; `all_info()` sets `state`. |
| `services/job_search_service.py` | build + cache `provider_states`. |
| `services/providers/linkedin_provider.py` | authwall detection, `LINKEDIN_MAX_PAGES`, `preferred_companies` pass, block/temp classification. |
| `services/providers/parsing.py` | `to_utc` handles millisecond epochs (Lever `createdAt`). |
| `config.py` | `LINKEDIN_MAX_PAGES`; `BROWSER_*` (8 fields); `GREENHOUSE_BOARDS` / `LEVER_BOARDS` (curated defaults) / `CAREER_PAGE_URLS`; `_csv` + 3 list props; `+greenhouse,lever,career_pages` in the default provider set. |
| `main.py` | `lifespan` shutdown → `browser_session.shutdown()` (guarded). |
| `requirements.txt` | `+ selenium>=4.20` (bundles Selenium Manager; no `webdriver-manager`). |
| `Dockerfile` | `ARG INSTALL_BROWSER=true` → installs `chromium` + `chromium-driver`; presets `BROWSER_BINARY` / `BROWSER_DRIVER_PATH`. |
| `docker-compose.yml` | `api`: `shm_size: "512m"`, `BROWSER_ENABLED` passthrough. |
| `.env.example` | every new var documented, no real values. |
| `tests/conftest.py` | `mock_browser` fixture + `_no_real_browser` autouse safety-net; `_FakeBrowser`; `reset_registry` teardown includes the 3 new providers. |
| `tests/_jobs_helpers.py` | `FakeJobProvider` gains `state=` / `last_state` / `_finish()`. |
| `tests/test_provider_linkedin.py` / `test_job_search_service.py` / `test_jobs_api.py` | extended for authwall / `provider_states` / `sources` `state`. |
| `README.md`, `PROJECT_STRUCTURE.md`, `../API_FRONTEND_GUIDE.tex` | ProviderState table, browser section, ATS-board how-to, LinkedIn boundary. |

### Not touched

`JobSearchService` core flow, `JobSearchContext`, `JobOffer`,
`job_dedup.py`, `job_freshness.py`, `job_ranking.py`,
`schemas/applications.py`, the preference extractor, the application
service, the other 10 providers, `cache_service.py`, `circuit.py`,
`http.py` (read-only reuse).

### Known limitations

- `career_pages` needs a hand-curated URL list and only reads schema.org
  `JobPosting` / `ItemList` structured data — a career page with none
  yields 0 offers (state stays `available`).
- The browser is single-session by design: browser providers do not run
  in parallel with each other. Fine for one `career_pages` provider.
- `GREENHOUSE_BOARDS` / `LEVER_BOARDS` defaults are well-known public
  boards chosen for stability; they are not curated per user.

### Verify

```bash
python -m compileall .            # clean
python -c "import main"           # no circular imports, no Chromium launched
pytest -q                         # 486 passed, 1 skipped — fully offline
                                  # (selenium + browser + all job HTTP mocked)
grep -rn "LINKEDIN_EMAIL\|LINKEDIN_PASSWORD\|li_at" services/   # only doc/negative mentions
grep -rln "import selenium\|from selenium" services/            # only services/providers/browser.py
docker build --build-arg INSTALL_BROWSER=true -t cv-assistant:jobs .
```

---

## 11. — ATS HTTP providers (v2.3)

Date: 2026-09-03 · Tests: **589 passed, 1 skipped** (486 pre-existing,
unchanged + 103 new)

Seven new **public-ATS JSON providers**, adapted from the analysis of the
JobNavigator project (`ats/*` scrapers). JobNavigator's own scrapers are
async + coupled to its DB + return only `{title, url}`; these are
**re-implemented sync** in the CV Generator provider framework, mapping the
full `NormalizedOffer`, going through the one `http.py` chokepoint. **Zero
new runtime dependencies. No browser. No LinkedIn.**

### Providers added (7)

| Provider | Endpoint | Config | Notes |
|---|---|---|---|
| `ashby` | `api.ashbyhq.com/posting-api/job-board/{slug}` | `ASHBY_BOARDS` (curated default) | list carries description + compensation |
| `workday` | `{tenant}.myworkdayjobs.com/wday/cxs/{co}/{site}/jobs` (POST) | `WORKDAY_TENANTS` (full URLs) | offset pages; bounded `jobPostingInfo` enrichment; path case preserved |
| `oracle_hcm` | `*.oraclecloud.com/hcmRestApi/…/recruitingCEJobRequisitions` | `ORACLE_HCM_SITES` (full URLs) | **case-sensitive** site/path verbatim; bounded `ById` enrichment |
| `smartrecruiters` | `api.smartrecruiters.com/v1/companies/{slug}/postings` | `SMARTRECRUITERS_BOARDS` (**case-sensitive** slugs) | limit/offset; bounded `jobAd.sections` enrichment |
| `rippling` | `api.rippling.com/platform/api/ats/v1/board/{slug}/jobs` | `RIPPLING_BOARDS` | flat array, `uuid` dedup |
| `phenom` | per-tenant `/widgets` (POST) | `PHENOM_BOARDS` (JSON `{company,endpoint,payload}`) | per-entry host = its SSRF allow-list |
| `talentbrew` | `{host}/search-jobs/results?…` (AJAX) | `TALENTBREW_BOARDS` (full URLs) | parses the HTML fragment; `CurrentPage` pages |

All ship **disabled** except `ashby` (curated board list). Total providers: **13 → 20**.

### Files added

`services/providers/ats_common.py` (base `MultiBoardATSProvider` +
`host_matches` / `looks_like_job` / `valid_slug` / `valid_id` /
`_bounded_enrich`); `services/providers/{ashby,workday,oracle_hcm,smartrecruiters,rippling,phenom,talentbrew}_provider.py`;
`tests/test_provider_{ashby,workday,oracle_hcm,smartrecruiters,rippling,phenom,talentbrew}.py`,
`tests/test_ats_security.py`, `tests/test_jobs_ats_integration.py`;
`tests/fixtures/jobs/{ashby,workday_jobs,workday_detail,smartrecruiters,smartrecruiters_detail,oracle_hcm_list,oracle_hcm_detail,rippling,phenom,talentbrew}.json`.

### Files modified

| File | Change |
|---|---|
| `services/providers/http.py` | + RFC 6598 CGNAT (`100.64.0.0/10`) IP block; + `json_body=` (POST body, first hop only, dropped on redirect); + per-call `max_bytes=` override (still streamed + enforced). |
| `config.py` | + `ASHBY_BOARDS` (default) / `WORKDAY_TENANTS` / `ORACLE_HCM_SITES` / `SMARTRECRUITERS_BOARDS` / `RIPPLING_BOARDS` / `TALENTBREW_BOARDS` / `PHENOM_BOARDS` (empty) + `ATS_ENRICH_DESCRIPTIONS` / `ATS_ENRICH_MAX` / `ATS_HTTP_MAX_BYTES`; 7 list properties (case-preserving where the ATS needs it); `+ 7 names` in `JOBS_ENABLED_PROVIDERS`. |
| `services/providers/registry.py` | register the 7. |
| `.env.example` | document every new var (no real tenant values). |
| `tests/conftest.py` | `reset_registry` teardown + the 7; `_FakeHttp.fetch` accepts `json_body` / `max_bytes`. |
| `tests/test_jobs_api.py` | `/api/jobs/sources` asserts all 7 + tenant-specific ones disabled. |
| `README.md`, `PROJECT_STRUCTURE.md` | provider table, "adding an ATS board" matrix, design-rules rows, SSRF/CGNAT note, test list. |

### Not touched

`greenhouse` / `lever` (predate `MultiBoardATSProvider`, left as-is — no
regression), `JobSearchService` core flow, `NormalizedOffer`, dedup /
freshness / ranking, `schemas/applications.py`, the browser stack, the
Agent, the other 12 providers, `circuit.py`.

### Security (audit — Phase 7)

- Every ATS fetch passes `allowed_hosts` (static for the 5 slug providers;
  the configured endpoint host for phenom/talentbrew) → same `validate_url`
  SSRF guard, https-only, private/loopback/**CGNAT** IP rejection, redirect
  re-check.
- Config sanitisation: `valid_slug` (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`) /
  `valid_id` — `../`, `?`, `#`, offsite URLs, injection strings are dropped
  before they can be spliced into a request path.
- Descriptions are `html_to_text` plain text (scripts/styles stripped);
  they only ever land in `JobOffer.description`, which **no LLM path
  consumes** — no new prompt-injection surface.
- No logging in the new providers; `http.py` logs host-only.
- Response size: `ATS_HTTP_MAX_BYTES` (12 MB) for the board-list fetch,
  strict `JOB_HTTP_MAX_BYTES` (3 MB) for the per-job detail calls.
- Timeouts: per-call + per-provider budget + orchestrator `future` timeout +
  `_MAX_PAGES` pagination bound + deadline-aware `_bounded_enrich`.

### JobSpy — evaluated, **not integrated**

`python-jobspy` (LinkedIn/Indeed/ZipRecruiter/Google guest aggregator) was
assessed for Phase 6 and rejected:

1. **Bypasses the `http.py` chokepoint** — it ships its own HTTP/TLS client,
   so the SSRF guard, host allow-list, size cap and politeness delay would
   not apply. That breaks the subsystem's single most load-bearing invariant.
2. **Heavy deps** — `pandas` + `numpy` + a native TLS client, plus
   `markdownify<0.14` which still carries CVE-2025-46656 (no fixed release);
   ~40 MB+ image growth for a project that otherwise adds **nothing**.
3. **Marginal incremental coverage** — the genuinely new sources are
   ZipRecruiter (US) + Google Jobs (a meta-aggregator); LinkedIn guest and
   Indeed guest are already covered, and there are now 9 ATS providers.
4. **Public-backend risk** — JobSpy's LinkedIn path is guest-but-aggressive
   and recommends proxies; an IP flag would degrade the shared outbound IP.

A "disabled-by-default optional dependency" still means the code must be
maintained, tested and its CVE suppressed in audit tooling — so it is left
out entirely. The recommended high-volume path is **ATS boards + LinkedIn
guest + the remote-board JSON providers**.

### Known limitations

- **No safe curated default** for `workday` / `oracle_hcm` /
  `smartrecruiters` / `rippling` / `phenom` / `talentbrew` — they are
  tenant-specific; the operator adds boards. `ashby` ships a small list.
- **`posted_at`** is often unavailable from an ATS board list. Workday only
  gives a relative string ("Posted 3 Days Ago") — the unambiguous forms are
  converted to an *approximate* datetime; vague ones stay `null`. No field
  is ever back-filled with a fabricated value.
- **Description enrichment is bounded** (`ATS_ENRICH_MAX`, default 20) and
  best-effort — a large board returns the first N descriptions, the rest
  carry `null` until a later search refreshes them.
- **Phenom config is a JSON blob** in `.env` — unavoidable (Phenom has no
  shared API); each tenant has a bespoke endpoint + filter payload.

### Verify

```bash
python -m compileall .                 # clean
python -c "import main"                 # ok
pytest -q                              # 589 passed, 1 skipped — fully offline
grep -rln "import httpx\|import requests\|jobspy\|playwright" services/providers/{ashby,workday,oracle_hcm,smartrecruiters,rippling,phenom,talentbrew}_provider.py services/providers/ats_common.py   # none
grep -c "" requirements.txt             # unchanged — no new dependency
```



## 12. — Collection optimisation, LinkedIn-guest depth, observability (v2.4)

Date: 2026-09-03 · Tests: **771 passed, 1 skipped** (589 pre-existing +
~182 new). No new runtime dependency. No browser. No LinkedIn auth.

Thirteen focused evolutions of the job-collection subsystem — every one
additive, behind the existing `http.py` chokepoint, `NormalizedOffer`,
circuit breakers and stateless model.

### Phase 11 — post-integration audit + fixes

- **ATS keyword search now reads past the first page.** The base
  `MultiBoardATSProvider._search` hands `_fetch_board` a `keep(offer)`
  predicate (the context/keyword filter); a paginating provider applies it as
  it goes and stops at `limit` **matches**, not `limit` raw postings — so a
  `?query=` search on a 2000-job board is no longer limited to its first
  page. Still bounded by `_MAX_PAGES` + the deadline.
- **Every pagination loop is deadline-aware** (`time.monotonic() >= deadline`)
  — a 15-page board can no longer overrun the per-provider budget.
- `phenom` payload type guard (`isinstance(..., dict)`).

### Phase 12 — board-list architecture (coverage)

- `data/ats_boards/<x>.txt` — bundled, best-effort lists of **real** public
  boards (greenhouse 35, lever 18, ashby 20, smartrecruiters 8, rippling 1).
  A dead token self-skips (one wasted request, nothing more).
- Effective list per provider = bundled file + `X_BOARDS_FILE` (operator file)
  + `X_BOARDS` (env CSV), de-duplicated case-insensitively, **validated**
  (`valid_slug`), capped at `ATS_MAX_BOARDS` (60). `ATS_USE_BUNDLED_BOARDS`
  turns the shipped lists off. `PHENOM_BOARDS_FILE` for the JSON array.
- `greenhouse` / `lever` gained the same `valid_slug` filtering the new ATS have.

### Phase 13 — LinkedIn guest improvements (no auth)

- Guest search pages step by **25** (the real page size), not 10 — ~2.5x the
  unique coverage for the same page count; stop on a no-new-ids page.
- `f_TPR` recency filter (`LINKEDIN_FRESHNESS_DAYS`, 30) — LinkedIn trims stale
  postings at the source.
- Strategy B **merges the embedded JSON-LD `ItemList`** (previously fetched
  then ignored) — a second list source + fills dates/descriptions on card
  offers.
- Salary parsed off the search card (`job-search-card__salary-info`).
- Enrichment: only offers missing a description, **relevance-ordered**, capped
  at `LINKEDIN_ENRICH_MAX` (12) + the deadline (guest enrichment is throttled
  to ~1 req/host/s — was up to ~100 requests per search).

### Phase 14 — dedup + URL canonicalisation

- `parsing.normalize_url` is **tracking-param aware** — drops `utm_*` + a
  referral blocklist, **keeps** functional params (`gh_jid`, `jobId`...), sorts
  the rest. Two different jobs on the same path (`?gh_jid=111` vs `222`) no
  longer collapse into one.
- `job_dedup` restructured: `id::` / `url::` = strong identity (always merge);
  the company+title+city path now passes `_fuzzy_mergeable` — a job vs an
  internship, or two both-set-but-different experience levels, are separate
  openings and never merge on title alone. A strong URL/id match still wins.

### Phase 15 — relevance-ranked enrichment

`rank_enrichment_targets(offers, ctx)` orders the description-missing
candidates by the deterministic LLM-free `job_ranking.score` (title/query
overlap, skills, location, freshness) so a bounded budget is spent on the
matches the user cares about — not the board's first N. Used by the ATS
`_bounded_enrich` and by LinkedIn.

### Phase 16 — `posted_at` / data quality

- `parsing.to_utc` hardened: numeric-string epochs, seconds vs ms, EN + FR
  **explicit** relative dates ("Posted 5 Days Ago", "il y a 3 semaines"),
  more absolute formats, and **rejection of absurd dates** (before year 2000,
  or a corrupted far-future epoch).
- New `parsing.parse_posted_date` — `to_utc` + a "not in the future" clamp; a
  posting date that cannot be resolved reliably stays **`None`**, never a
  guess. All 20 providers migrated `posted_at=` to it; `expires_at` keeps
  `to_utc` (a validThrough is legitimately in the future).

### Phase 17 — provider observability

`services/providers/metrics.py` — `ProviderMetrics`, cache-backed (7-day TTL,
same mechanism as the circuit breaker, no new state store). `search()`
records `runs` / `ok` / `fail`, `last_run_at` / `last_ok_at`,
`last_state` / `last_offer_count` / `last_duration_ms` and a **sanitised**
`last_error` (`_safe_label` strips URLs + long token-like strings). Surfaced
read-only on `GET /api/jobs/sources` (`runs`, `error_rate`, `last_*`).
Telemetry failures are swallowed — never break a search.

### Phase 18 — broken-provider detection

`metrics.health_note(snap, circuit)` — set only when a provider looks
*structurally* broken, distinct from a search that legitimately found nothing:
the circuit is open (reports the consecutive-failure count + last reason), OR
the provider has returned at least `PROVIDER_STALE_MIN_OFFERS` at least once
but the last `PROVIDER_STALE_EMPTY_RUNS` runs all came back `available` with
**zero** offers (a `WARNING` is logged once on the crossing). One good run
clears it. Surfaced as `ProviderInfo.health_note`.

### Phase 19 — performance (measured)

- **`job_dedup.deduplicate`: O(n^2) -> O(n*k).** The fuzzy-title pass scanned
  *every* existing group; it now scans only the `(company, city)` bucket
  (k, typically 1-3). Measured (synthetic worst case, no network):

  | raw offers | before | after |
  |---|---|---|
  | 1000 | 397 ms | **139 ms** (2.9x) |
  | 2000 | 768 ms | **323 ms** (2.4x) |

- **`JobSearchService._upsert`: N+1 -> bulk.** Was 2 SELECTs per merged offer
  (500 offers = 1000 queries); now a handful of chunked `IN` prefetches
  (`content_hash` + `(source, source_job_id)` grouped by source, chunked at
  400 to stay under the SQLite variable limit); a cold DB does the prefetch
  and skips the per-offer fallback entirely.
- Fan-out verified healthy: 20 in-memory providers (50 ms latency each,
  800 raw offers) complete in ~330 ms — 3 pool waves, not 20x serial.
  `ThreadPoolExecutor(min(N, 8))` parallelism confirmed.
- No premature optimisation elsewhere (`rank` ~110 ms/500 offers is fine).

### Phase 20 — robustness

`tests/test_provider_robustness.py` — every ATS provider x {timeout, 5xx,
429, 403, SSRF, empty body, junk JSON, wrong JSON type, wrong container,
unexpected shape}, plus null-riddled rows and an oversized response.
**Fixed two real bugs:** `greenhouse` / `lever` `_normalize` crashed on a
`null` element in the results array (now `isinstance(row, dict)` guarded;
the title must be a non-empty `str`).

### Phase 21 — security audit (all pass)

No provider imports `httpx`/`requests` directly; no `eval`/`exec`/`pickle`/
`subprocess` in job code; no LinkedIn `li_at`/Voyager/login/stealth/proxy;
every ATS fetch host-scoped; SSRF/HTTPS/CGNAT/redirect guards intact
(68 tests); bundled board files carry zero non-slug tokens; config has no
credential-shaped default for the ATS subsystem; scraped descriptions are
`html_to_text` plain text and reach **no** LLM path (only the delimited,
UNTRUSTED-marked conversation goes to `job_preference_service`);
`ProviderInfo`/metrics expose only sanitised counters, never a URL/token/cookie.

### New files (v2.4)

`services/providers/metrics.py`; `data/ats_boards/{README.md,greenhouse.txt,
lever.txt,ashby.txt,smartrecruiters.txt,rippling.txt}`; tests
`test_ats_pagination.py`, `test_ats_boards_config.py`, `test_ats_enrichment.py`,
`test_parsing_dates.py`, `test_provider_metrics.py`, `test_provider_robustness.py`;
fixture `tests/fixtures/jobs/linkedin_search_page_jsonld.html`.

### New config (v2.4)

`ATS_USE_BUNDLED_BOARDS` (true); `ATS_MAX_BOARDS` (60);
`{GREENHOUSE,LEVER,ASHBY,SMARTRECRUITERS,RIPPLING,WORKDAY_TENANTS,
ORACLE_HCM_SITES,TALENTBREW,PHENOM}_*_FILE`; `LINKEDIN_FRESHNESS_DAYS` (30);
`LINKEDIN_ENRICH_MAX` (12); `PROVIDER_STALE_MIN_OFFERS` (5);
`PROVIDER_STALE_EMPTY_RUNS` (6). The former `GREENHOUSE_BOARDS` /
`LEVER_BOARDS` / `ASHBY_BOARDS` inline defaults moved to the bundled files
(the env vars now default to `""` and are *additive*).

### Not touched

`JobSearchService` search flow (beyond the prefetch), `NormalizedOffer`,
`job_freshness`, `job_ranking` weights, `job_application_service`, the browser
stack, the Agent, `circuit.py`, `http.py` SSRF core, the other 10 providers'
parsing.

---

## 13. — Platform hardening: async fan-out, superadmin, statistics, LLM config (v2.5)

> Phases 24–42. Work-in-progress log. Each phase section is filled in as it
> lands; the audit below (Phase 24) is the baseline and the plan.

### Phase 24 — architecture audit (baseline, read-only)

Full inspection of `services/`, `models/`, routers, `schemas/`, `tests/`,
`Dockerfile`, `config.py`. No code changed in this phase.

#### 24.1 Provider fan-out — is it actually parallel?

**Finding: yes, it fans out, but the collection loop is not deadline-bounded.**
`JobSearchService.search()` (`services/job_search_service.py:57`) submits every
provider to a `ThreadPoolExecutor(max_workers=min(len(providers), 8))` up front —
so up to 8 providers run concurrently (measured Phase 19: 20 fakes @ 50 ms →
~330 ms, 3 pool waves). The weakness is the **result-collection loop**:

```python
for future, provider in list(futures.items()):
    result = future.result(timeout=budget)   # budget = per-provider, applied serially
```

`budget = JOB_PROVIDER_TIMEOUT * (JOB_PROVIDER_MAX_PAGES + 2)` (~50 s with prod
defaults). Futures run in parallel, but `future.result(timeout=budget)` is
awaited **one at a time**. If *k* providers are genuinely hung, the loop waits up
to `budget` for each in turn → worst case ≈ `budget × k`, not `budget` once.
There is **no global search deadline**. `pool.shutdown(wait=False,
cancel_futures=True)` already prevents thread leakage, and a not-yet-started
future is cancelled — but an in-flight `httpx` request is not interrupted (sync
client), it just finishes into the void.

- `http.py` is a **synchronous** `httpx.Client` (`services/providers/http.py:201`).
  The whole stack is sync (SQLAlchemy 2.0 sync, sync providers, sync routes that
  aren't `async def`). A true `asyncio` rewrite would touch all 20 providers, the
  SSRF guard, circuit breaker, metrics, every provider test — high risk, and it
  contradicts "conserver la compatibilité". **Decision for Phase 25: keep the
  thread-pool model, add a single global monotonic deadline** so a hung provider
  costs the deadline *once*; make the worker cap configurable; drain with
  `concurrent.futures.wait(timeout=remaining)`.

#### 24.2 Job links (URL preservation)

**Finding: structurally safe already; needs regression tests, not a fix.**
`NormalizedOffer.source_url: str` (required, `schemas/jobs.py:219`),
`JobOffer.source_url: Mapped[str]` (`nullable=False`, `job_offer.py:36`),
`JobOfferOut.source_url: str` (required). `_upsert` always writes
`row.source_url = offer.source_url` (`job_search_service.py:237`); dedup keeps the
primary's `source_url` and pushes the rest to `also_seen_on`
(`job_dedup._merge`). The only normalisation is `normalize_url()` for the dedup
*key* and for `also_seen_on` — the stored `source_url` is the provider's original
string. No synthetic URLs anywhere.
Gaps: (a) no test asserting the URL survives provider→dedup→persist→API end to
end; (b) a provider *could* emit `source_url=""` (empty string passes `str`) and
nothing rejects it. Phase 26 = add `NormalizedOffer` validation (non-empty,
`http(s)://`) + an end-to-end URL-survival test + document the contract.

#### 24.3 Auto-apply

**Finding: deliberately never auto-submits — that boundary stays.**
`JobApplicationService.apply()` (`services/job_application_service.py:47`) records
intent and returns the external URL. `ApplicationStatus` = `manual_required` /
`requires_user_action` / `unavailable` / `failed` — **no `submitted`** by design
(`schemas/applications.py:8`, and the prior mission's hard rule). Real submission
needs the exact anti-bot / CAPTCHA / auth flows both prior missions forbid.

Weaknesses that *can* be fixed without crossing that line:
- **No CV auto-selection.** `apply()` uses whatever `cv_id` the caller passed; if
  none, none is recorded. No "pick the best CV for this offer" logic.
- **No job↔profile match signal.** Nothing scores how well the user fits the
  offer. `job_ranking.score(offer, ctx)` is a ready deterministic matcher — it
  just needs a `JobSearchContext` built from the user's latest CV / stated
  preferences.
- **Weak duplicate prevention.** Nothing stops two `JobApplication` rows for the
  same `(user_id, job_offer_id)`. No unique constraint, no pre-check.
- **Thin traceability.** The row has `user_id, job_offer_id, cv_id, letter_id,
  status, application_url, context, answers, created_at, updated_at`. Missing:
  `source` (provider), `match_score`, `error_detail`, `duration_ms`,
  `attempt_count`, a normalized job identity for dedup, explicit lifecycle
  timestamps.
- **No state machine.** `status` is a free `String(32)`; transitions aren't
  validated.

Phase 27 plan: additive columns on `job_applications`
(`source`, `normalized_job_key`, `match_score`, `error_detail`, `attempt_count`,
`last_attempt_at`, `duration_ms`); a `UniqueConstraint(user_id,
normalized_job_key)`; `select_best_cv(user, offer)` (language match → ATS score →
recency, all already-stored fields); reuse `job_ranking.score` for the match;
keep the no-submit contract, document it again. New states added only as
*preparation* labels (`prepared`, `skipped_duplicate`) — never `submitted`.

#### 24.4 User-preference extraction

**Finding: solid design, single LLM call, UNTRUSTED-delimited, degrades
safely.** `JobPreferenceExtractor` (`services/job_preference_service.py`). The
prompt already isolates the conversation between `<<<USER_CONVERSATION>>>`
markers and marks it UNTRUSTED. Output merged via `JobSearchContext.merge`
(later turn wins; lists unioned). Limits: no negative keywords / excluded-type
extraction beyond `excluded_companies`; `salary_min` only (no range / max);
`experience_level` is single-valued (the mission's example wants a list);
locations are single (`location`/`city`/`country`, not a list "Paris OR
Europe"); no sector/industry field. Multi-value experience & multi-location
would be a schema change rippling into ranking + filtering. Phase 28 plan:
extend the prompt + add local (LLM-free) regex fallbacks for salary, seniority,
"no internship / pas de stage", remote; keep the schema backward-compatible
(add optional `salary_max`, `excluded_keywords`, `sectors`; keep
`experience_level` scalar but also accept `experience_levels` list → collapse to
the strongest for the existing field). Adversarial + FR/EN + contradiction
tests, several without the LLM.

#### 24.5 LLM / NVIDIA

**Finding: hard-coded model chain, split config, no abstraction.**
`services/gemini_client.py` — `NVIDIA_MODELS_FALLBACK` is a module-level list of
3 model ids; `base_url` hard-coded to `integrate.api.nvidia.com`; `api_key =
os.getenv("NVIDIA_API_KEY")` read **at import** (documented exception to
`config.py`); `_TEMPERATURE`, `_MAX_OUTPUT_TOKENS`, `_REQUEST_TIMEOUT`,
`_MAX_CALLS_PER_MINUTE` are module constants. `config.Settings.NVIDIA_API_KEY`
exists only as a doc mirror. `call_gemini(prompt, request_type=...)` is the
single entry point — **11 modules import it** (see `conftest._LLM_CONSUMER_MODULES`).
`is_configured()` / `get_stats()` complete the surface.
The good news: one entry point → an abstraction can slot in behind
`call_gemini` without touching callers. Phase 29/30 plan: move model / base_url /
temperature / max_tokens / timeout / rpm into `config.Settings`
(`LLM_MODEL`, `LLM_MODELS_FALLBACK`, `LLM_BASE_URL`, `LLM_API_KEY` with
`NVIDIA_*` kept as aliases, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS`,
`LLM_REQUEST_TIMEOUT`, `LLM_MAX_CALLS_PER_MINUTE`); keep `call_gemini`'s
signature; keep the fallback-chain behaviour; `client` rebuilt lazily from
settings so an admin change takes effect without a code edit. **Secret
handling:** key is already never logged (`is_configured()` returns a bool; logs
print `prompt_chars`, model name, never the key). Confirm no route returns it;
add an explicit test.

#### 24.6 Secrets / API keys

`.env` is git-ignored (needs confirming in `.gitignore`). `conftest.py` sets
`NVIDIA_API_KEY=test-nvidia-key` — a dummy, fine. No real key found in the tree
in this pass (full grep is Phase 30/40). `gemini_client` logs never include the
key. `/api/health` returns `gemini_configured: bool`. `/api/stats` returns
counters only.

#### 24.7 Roles / auth

**Finding: no role system at all.** `User` has `id, email, password_hash,
is_active, created_at, updated_at` (`models/user.py`). `get_current_user`
(`dependencies/auth.py`) is the only gate — every protected route is "any
logged-in user". No `is_superadmin`, no admin router, no admin dependency. JWT
payload = `sub, email, iat, exp`. Phase 31 plan: additive
`User.is_superadmin: bool = False`; a `require_superadmin` dependency building on
`get_current_user`; **the field is never in any request schema** (`SignupRequest`
has only `email`, `password`; no update-user route exists) so escalation via API
is structurally impossible — add a test that proves it; a CLI / one-off script
(`scripts/grant_superadmin.py`) or a bootstrap env var
(`SUPERADMIN_EMAILS` applied on startup) to mint the first one.

#### 24.8 Database / migrations

**Finding: no Alembic — `Base.metadata.create_all()` only** (`database.py:60`,
comment: "Schema management is deliberately minimal"). `create_all` creates
missing *tables* but never alters existing ones. Every new column in Phases
27/31/33+ needs handling for already-deployed Postgres DBs. Decision: a minimal
idempotent `_ensure_additive_columns()` run in `init_db()` — introspect with
SQLAlchemy `inspect()`, `ALTER TABLE ADD COLUMN` for any declared-but-missing
column (all new columns are nullable / defaulted, so this is safe on SQLite +
Postgres). No new dependency; consistent with the existing "no Alembic" stance;
documented loudly. New tables (`provider_run`, `usage_event` if needed) are
handled by `create_all` for free.

#### 24.9 N+1 / query hotspots

- `JobApplicationService.list_saved` (`:136`) — loop of `db.get(JobOffer, id)`
  per saved row. N+1. Fix: one `select(JobOffer).where(JobOffer.id.in_(ids))`.
- `jobs_router._application_out` (`:236`) — `db.get(JobOffer, …)` per application
  in the list endpoint. N+1. Fix: prefetch offers for the page.
- `JobSearchService._upsert` — already fixed in v2.4 (bulk prefetch).
- Stats endpoints (Phases 33–36) must be `COUNT` / `GROUP BY` from day one, never
  "load all rows, count in Python".
- Indexes present: `users.email`, `job_offers.source|source_job_id|content_hash`,
  `job_applications.user_id|job_offer_id`, `saved_jobs.user_id`,
  FK indexes. **Missing for stats:** `job_applications.created_at`,
  `job_applications.status`, `generated_cvs.created_at`,
  `generated_letters.created_at`, a `users.last_active_at` (new).

#### 24.10 Observability today

`ProviderMetrics` (cache-backed, 7-day TTL, per-process on memory cache; shared
on Redis) already exposes runs / ok / fail / last_* / health_note on
`GET /api/jobs/sources` (v2.4). `gemini_client.get_stats()` on `/api/stats`.
No per-user activity tracking, no application-outcome aggregation, no dashboard.
Phase 33 can largely *reuse* `ProviderMetrics.snapshot()`; Phases 34–37 need new
lightweight tracking (a `users.last_active_at` touch in `get_current_user`, plus
counting from existing rows).

#### 24.11 Frontend

There is **no frontend code in this repo** — "frontend" = `../API_FRONTEND_GUIDE.tex`
(compiled PDF). "Frontend" deliverables in Phases 26/37/41 = documenting the API
contract (fields, the admin endpoints, the dashboard payload shape) in that
guide, not UI code.

#### 24.12 Tests / Docker

510 test functions, **771 passed / 1 skipped** (v2.4). Offline (LLM mocked,
SQLite, MinIO fake, memory cache). Autouse guards: `_no_real_job_http`,
`_no_real_browser`, `_fast_job_timeouts`, `_no_bundled_ats_boards`,
`clear_cache`, `fake_minio`. `reset_registry` teardown hard-codes the 20-provider
CSV. Single-worker uvicorn image (`Dockerfile:52`) — in-process counters are
consistent. `INSTALL_BROWSER=true` default; `--build-arg INSTALL_BROWSER=false`
verified in Phase 23.

### Phase 24 — decisions carried forward

| # | Decision | Rationale |
|---|---|---|
| D1 | Keep the sync thread-pool fan-out; add a global deadline + configurable concurrency | Full asyncio = rewrite of 20 providers + http.py + tests; violates additive/compat constraint |
| D2 | No Alembic; add idempotent `_ensure_additive_columns()` in `init_db()` | Matches the existing documented "no Alembic" stance; new columns are all nullable |
| D3 | Auto-apply never gains a real `submitted` path | Both prior missions forbid automating anti-bot / auth / CAPTCHA flows |
| D4 | `is_superadmin` is never a request-schema field; first admin via env `SUPERADMIN_EMAILS` / script | Structurally prevents privilege escalation over the API |
| D5 | LLM abstraction slots behind the existing `call_gemini` name; `NVIDIA_*` env kept as aliases | 11 modules import `call_gemini`; zero churn for callers; no break for existing deploys |
| D6 | New stats come from SQL aggregates + one `users.last_active_at` touch | No heavy event pipeline; efficient at scale; no fabricated metrics |
| D7 | Dynamic ATS boards (Phase 32) are a DB overlay *on top of* the file/env lists, never a replacement | "compatible avec l'existant"; files/env still work with zero DB rows |

### Phase 25 — bounded concurrent provider fan-out ✅

**Problem (from the audit):** the fan-out *was* concurrent, but the
result-collection loop awaited each future with its own per-provider timeout
*serially* — `future.result(timeout=budget)` in a `for` loop — so *k* genuinely
hung providers could cost `budget × k` (~50 s each). No global search deadline.

**Change** — `services/job_search_service.py`, one new method `_fan_out()`:

- submit every provider to `ThreadPoolExecutor(max_workers=min(N,
  JOB_SEARCH_MAX_CONCURRENCY))`;
- harvest with `concurrent.futures.wait(pending, timeout=remaining,
  return_when=FIRST_COMPLETED)` against **one shared monotonic deadline**
  (`JOB_SEARCH_DEADLINE`);
- when the deadline elapses, every still-running provider is `future.cancel()`-ed,
  its breaker tripped, and it is marked `temporarily_unavailable` → coarse
  `unavailable` (unchanged semantics);
- `pool.shutdown(wait=False, cancel_futures=True)` unchanged — no thread leak, a
  not-yet-started future is dropped.

**New config** (`config.py`):
`JOB_SEARCH_MAX_CONCURRENCY` (20 — the full default provider set starts in one
wave; lower it to rate-limit) and `JOB_SEARCH_DEADLINE` (45.0 s — the whole
fan-out).

**Preserved:** `http.py` chokepoint, SSRF guard, per-request timeouts, circuit
breaker, `ProviderMetrics`, `provider_states`, the `sources` coarse map
(byte-identical outcomes), per-provider isolation (`search()` still never
raises).

**Measured** (synthetic, `_jobs_helpers` fakes, no network — hung = a provider
stalled 30 s past any per-request timeout):

| scenario | before (serial `.result`) | after (shared deadline) |
|---|---|---|
| 0 hung + 8 fast | ~0.05 s | **0.03 s** |
| 3 hung + 5 fast | ~150 s worst case | **8 s** (= deadline), 5 fast returned |
| 8 hung + 4 fast | ~400 s worst case | **8 s**, 4 fast returned (no starvation at concurrency 20) |
| 20 providers, all healthy | 3 pool waves (~3×latency) | **1 wave** (~1×latency) |

**Tests** — `tests/test_job_search_async.py` (13):
concurrency-is-parallel timing, slow-doesn't-block-fast, global deadline bounds
total time with many hung providers, hung → breaker tripped, every
`ProviderState` survives the fan-out, empty provider = `available`, all-providers
-failing still 200, `JOB_SEARCH_MAX_CONCURRENCY` cap respected (peak-live
tracking), queued providers cancelled when the deadline is ~0.
`_jobs_helpers.FakeJobProvider` gained an additive `delay=` kwarg;
`conftest._fast_job_timeouts` now also pins `JOB_SEARCH_DEADLINE=1.0`.

Full suite after Phase 25: **784 passed, 1 skipped, 0 failed** (785 total).

### Phase 26 — exact link for every job ✅

**Audit verdict:** structurally already safe — `NormalizedOffer.source_url`,
`JobOffer.source_url` and `JobOfferOut.source_url` are all **required** (non-null);
`_upsert` always writes it; `job_dedup._merge` keeps the primary's URL and pushes
the rest to `also_seen_on`; the only normalisation is on the dedup *key*, never
the stored value; no provider fabricates a URL. So Phase 26 hardens + proves it
rather than fixing a leak.

**Changes:**
- `schemas/jobs.NormalizedOffer` — `field_validator("source_url", "company_url",
  mode="before")` trims surrounding whitespace (non-raising, so one odd row
  never kills a provider's page).
- `job_search_service._with_usable_link()` — runs before dedup: an offer whose
  `source_url` is blank / not `http(s)://` is **dropped** (logged per source),
  never shown with a dead link and never given a synthesised URL. Centralised —
  the same defensive pattern as `_hard_filter`, covers all 20 providers at once.

**Tests** — `tests/test_job_link_integrity.py` (5): URL survives
provider→dedup→persist→API verbatim (incl. tracking params kept on the stored
URL); blank / `javascript:` / relative URLs dropped, not fabricated; dedup keeps
the primary link and records the other in `also_seen_on`; `/api/jobs/search` and
`/api/jobs/{id}` agree on the link and the apply-URL falls back to it;
whitespace-wrapped URL trimmed not dropped.

**Frontend:** no UI in this repo — the API contract (`source_url` always present
and openable on every `JobOfferOut`) is what the frontend guide documents
(Phase 41).

### Phase 27 — auto-apply: matching, duplicate guard, traceability ✅

**Contract unchanged:** the service still **never submits** an application to an
external site. No `submitted` status (test `test_application_status_has_no_submitted`
now asserts the *intent* — no submitted/applied/sent/completed — plus the known
set). New states: only `duplicate`.

**Schema migration mechanism (D2, first use):** `database._ensure_additive_columns()`
runs in `init_db()` after `create_all` — `ALTER TABLE ADD COLUMN` for any declared
column missing from a live table (all new columns are nullable/defaulted → safe on
SQLite + Postgres), then creates any declared-but-missing index. Never drops or
alters. A missing NOT-NULL-without-default column is logged, not force-added.

**`job_applications` new columns (all additive):** `source`, `normalized_job_key`
(`slug(company)|slug(title)|slug(city)` — the real posting identity),
`match_score`, `error_detail`, `attempt_count`, `last_attempt_at`, `duration_ms`;
`status` + `created_at` indexed; composite indexes
`(user_id, normalized_job_key)` and `(user_id, job_offer_id)`.

**Behaviour (`services/job_application_service.py`):**
- **Duplicate guard** — before creating a record, look for an earlier application
  by the same user to the same `job_offer_id` *or* the same `normalized_job_key`
  (catches "same job, two `JobOffer` rows"). If found → return that record with
  `application_status=duplicate`, `is_duplicate=True`. No DB row created, no 500.
- **CV auto-selection** — `_select_best_cv(user, offer)` when `req.cv_id` is
  omitted: language match with the offer → higher `ats_score` → most recent. Uses
  only fields already on `GeneratedCV`. No CV on file → `None`, not an error.
- **Match score** — `_match_score` reuses the deterministic LLM-free
  `job_ranking.score(offer, ctx)` with `ctx` built from `req.context` text +
  offer language. Honest about the limited signal (no persisted structured user
  profile) — stored on the row, returned in the response.
- **Traceability** — every record now carries provider, normalized key, match
  score, attempt count, last-attempt time, prep duration, and an `error_detail`
  on the failure/unavailable paths.

**Also fixed 2 N+1s from the audit (24.9):** `list_applications` /
`get_application` prefetch offers in one `IN` query; `list_saved` prefetches in
one `IN` query instead of `db.get` per row.

**Security:** auto-selected CV is drawn from the caller's own rows (owned by
construction); `_check_ownership` still validates any caller-supplied
`cv_id`/`letter_id`. No CAPTCHA/auth/anti-bot automation — none added.

**Tests** — `tests/test_job_apply_matching.py` (10): CV language>ATS>recency
selection, explicit `cv_id` respected, no-CV-not-an-error; duplicate on same
offer / across two offer rows / different users allowed; provenance columns
populated; expired offer records `error_detail`; `_ensure_additive_columns`
re-adds a dropped column. Full suite: **799 passed, 1 skipped** (800).

### Phase 28 — richer preference extraction ✅

`JobSearchContext` gained `salary_max`, `excluded_keywords`, `sectors` (all
optional / list, backward-compatible; added to `_LIST_FIELDS` so `merge` unions
them). The `/api/jobs/context` LLM prompt now also asks for salary min/max +
currency, `excluded_keywords` / `excluded_companies` / `sectors`, and
"not an internship" / "senior only" handling.

**New: `_local_enrich(turns, ctx)` — deterministic, LLM-free backstop** in
`job_preference_service.py`. Runs *after* a successful LLM call (backfills what
the model missed) **and** is the whole answer when the LLM fails. Regex-extracts
from the user turns only (`role == "user"`, lower-cased): salary (`60k`,
`60 000 €`, min/max hint window), currency (€/$/£), remote vs hybrid vs onsite
(FR + EN), "pas de stage / no internship" → `job_type=job` + keyword excluded,
"stage / internship / alternance" wanted → `job_type=internship`, seniority
words. **Only fills unset scalars / adds to lists — never overrides the
user/model, never invents.** Wrapped so it can never crash extraction.

`JobSearchService._hard_filter` now also drops an offer whose **title** matches
an `excluded_keywords` term (word-boundary, ≥3 chars).

**Tests** — `tests/test_job_preference_extraction.py` (34): salary floor/ceiling/
currency/no-override/absurd-number; remote FR+EN; "no internship" incl. the
contradiction case (explicit prior `job_type` wins); seniority; **prompt
injection in user text stays text**; assistant turns ignored; empty/garbage
never crash; full-extractor backfill after LLM + degrade-to-local-only on LLM
failure. Full suite: **826 passed, 1 skipped** (827).

### Phases 29 + 30 — configurable LLM backend + NVIDIA/key handling ✅

**No change to any business code** — the 11 modules still call
`call_gemini(prompt, request_type=...)`. Everything configurable moved out of
`gemini_client` module constants into `config.Settings`:

| setting | default | alias |
|---|---|---|
| `LLM_API_KEY` | `""` | falls back to `NVIDIA_API_KEY` |
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | |
| `LLM_MODEL` | `""` (→ built-in chain) | `NVIDIA_MODEL` |
| `LLM_MODELS_FALLBACK` | `""` (CSV) | |
| `LLM_TEMPERATURE` | `0.3` | |
| `LLM_MAX_OUTPUT_TOKENS` | `4096` | |
| `LLM_REQUEST_TIMEOUT` | `45.0` | |
| `LLM_MAX_CALLS_PER_MINUTE` | `30` | |

`config.DEFAULT_LLM_MODELS` = the verified NVIDIA chain (module constant, used
when nothing is configured). `settings.llm_models` builds the ordered, deduped
chain; `settings.llm_api_key` / `llm_configured`; `settings.llm_public_config()`
= safe dashboard snapshot **without the key**.

`gemini_client`: `client` is now built lazily by `_get_client()` (cached) and
`reset_client()` drops it so an admin config change takes effect without a
restart; `_get_model()` / rate limit / temperature / max-tokens read
`settings.*` live. New `get_config()` → `settings.llm_public_config()`.
`is_configured()` → `settings.llm_configured`.

**Secret handling:** the key is never logged (logs carry `prompt_chars`, model
name — never the key), never in `/api/health` (`gemini_configured: bool` only),
never in `/api/stats`, never in `llm_public_config()` / `get_config()`. `.env`
is git-ignored; the repo is not a git repo (no history to scan). `conftest`
uses a dummy `test-nvidia-key`. No real key anywhere in the tree.

**Tests** — `tests/test_llm_config.py` (13): default chain / `LLM_MODEL` wins /
`NVIDIA_MODEL` alias / dedup; key alias + `llm_configured`; **`llm_public_config`
& `get_config` & `/api/health` never contain the key**; `reset_client` rebuilds;
rate limit + `_get_model` read settings live.

**Bug found & fixed by the isolated migration test:** `attempt_count` was
`NOT NULL` with only a client-side `default=1` → `_ensure_additive_columns`'s
`ADD COLUMN ... NOT NULL` failed on SQLite. Fixed: `server_default="1"` on the
column **and** the helper now downgrades any NOT-NULL-without-server-default
column to nullable on the ALTER path (data-safe; logged). The Phase-27
migration test was rewritten to use a **fully isolated throwaway engine** (the
first version issued raw `DROP COLUMN` on the shared test DB — a connection-lock
hazard).

### Phase 31 — `is_superadmin` + user administration ✅

`User` gained `is_superadmin` (Boolean, `server_default=false()`, NOT NULL) and
`last_active_at` (nullable, indexed — Phase 34). Both land on existing DBs via
`_ensure_additive_columns`.

**Escalation is structurally impossible over the API:** `is_superadmin` is in
**no** create/update schema a normal user can reach. `SignupRequest` has only
`email` + `password`; there is no self-service user-update route. The field is
set only by (a) the `SUPERADMIN_EMAILS` startup bootstrap (`admin_service.
bootstrap_superadmins` — promotes existing accounts, **never demotes**), or
(b) a PATCH by an existing superadmin.

**New:** `dependencies.auth.require_superadmin` (401 → 403 → user);
`_touch_activity` writes `last_active_at` at most once per 10 min per user,
best-effort (rollback, never 500s). `services/admin_service.py`
(`AdminService`, `bootstrap_superadmins`). `admin_router.py` — prefix
`/api/admin`, `dependencies=[Depends(require_superadmin)]` on the whole router.
`schemas/admin.py`. `UserPublic` gained `is_superadmin` (output only — `/me`,
signup, signin responses).

**Routes (Phase 31):** `GET /api/admin/users` (paginated, `q` email search,
per-user CV / letter / application counts via `GROUP BY` — no N+1),
`GET /api/admin/users/{id}`, `PATCH /api/admin/users/{id}` (`is_active`,
`is_superadmin`).

**Self-lockout guards:** cannot deactivate your own account, cannot revoke your
own superadmin, cannot remove the last superadmin (400 each).

`main.py`: router included, bootstrap called in `lifespan` (guarded), CORS
`allow_methods` gained `PATCH`.

**Tests** — `tests/test_admin_auth.py` (25): normal→403 / anon→401 /
superadmin→200 on every admin route; **signup with `is_superadmin:true` in the
body is ignored**; `/me` exposes it read-only; bootstrap promotes only listed
emails and never demotes; promote/demote another user; all three self-lockout
guards.

### Phase 32 — superadmin ATS board management ✅  (D7: a DB overlay, never a replacement)

New table `ats_boards` (`provider`, `token`, `enabled`, `label`, `note`,
`created_by`; unique `(provider, token)`). New
`services/ats_board_registry.resolved_tokens(provider, base_tokens, validator)`:
`base_tokens` (files + env) **∪** enabled DB rows **∖** disabled DB rows, deduped,
validated, capped at `ATS_MAX_BOARDS`. **Best-effort** — a DB error or an empty
table returns `base_tokens` unchanged (zero rows == exactly the old behaviour).
Overlay cached for `ATS_BOARD_OVERLAY_TTL` (60 s); `invalidate()` on every admin
write.

8 ATS providers (ashby, greenhouse, lever, rippling, smartrecruiters,
oracle_hcm, workday, talentbrew) now resolve their token list through
`resolved_tokens(...)` instead of `settings.X_boards_list` directly. Phenom
(per-tenant JSON config) is not DB-managed.

**Admin routes** (all `require_superadmin`): `GET /api/admin/ats-boards`
(optional `?provider=`, returns the manageable-provider catalog),
`POST /api/admin/ats-boards` (201; 409 on dup), `PATCH /api/admin/ats-boards/{id}`,
`DELETE .../{id}` (204), `POST /api/admin/ats-boards/test` (best-effort live
`_fetch_board`, 15 s, no persistence).

**Validation kept:** slug providers → `valid_slug`; URL providers → absolute
`https://` + non-local host (the full SSRF/allow-list guard still runs in
`http.validate_url` at fetch time with the provider's own hosts). CORS gained
nothing new (PATCH already added in Phase 31).

**Tests** — `tests/test_admin_ats_boards.py` (11): normal→403; added slug board
appears in `resolved_tokens`; invalid slug / unknown provider / non-https URL →
400; **disabled row removes a token**; dup→409; toggle + delete reflected
immediately (cache invalidated); list scoping + provider catalog; **zero rows =
pure passthrough**.

### Phase 33 — provider statistics ✅

`ProviderMetrics` gained `total_offers` (cumulative) and `recent_durations_ms`
(last 30 runs) → `metrics.duration_stats(snap)` = `avg` / `p95` / `samples`.
`GET /api/admin/providers` (`require_superadmin`) → per provider: enabled,
state, circuit_state, runs, success, failure, success_rate, error_rate,
consecutive_failures, last_run_at, last_success_at, last/avg/p95 duration_ms,
last_offer_count, max_offers, total_offers_collected, sanitised last_error,
health_note. Reuses `registry` + `snapshot()` — no new storage.

**Tests** — `tests/test_admin_provider_stats.py` (4): normal→403; every
registered provider listed with the full field set; stats reflect a run
(runs / success / total_offers / duration); `last_error` never carries a URL.

### Phases 34-37 — usage / application / document stats + superadmin dashboard ✅

New `services/stats_service.py` — **every figure a live SQL aggregate**
(`COUNT` / `GROUP BY` / `COUNT(DISTINCT …)`), never load-all-then-count, never
fabricated; a metric the schema can't support is simply absent.

Activity windows use `User.last_active_at` (single timestamp, "active within
window" semantics): `DAU`=1d, `WAU`=7d, `MAU`=30d, `YAU`=365d.

**Routes (all `require_superadmin`):**
- `GET /api/admin/stats/usage` — total / active users, DAU/WAU/MAU/YAU, active
  + new-user counts per window.
- `GET /api/admin/stats/users` — per user: cv / letter / saved-job / application
  counts + `applications_by_status` + `last_active_at`. (search_count / jobs_seen
  are **not** tracked — no event table — so they are absent, not guessed.)
- `GET /api/admin/stats/applications` — `by_status`, `by_window`, `by_provider`,
  `avg_prepare_ms`; note reaffirms "never submits".
- `GET /api/admin/stats/applications/by-user` — ranked by count.
- `GET /api/admin/stats/documents` — cv / letter totals, used-in-application
  (DISTINCT), created per window.
- `GET /api/admin/llm` — `gemini_client.get_config()` (**no key**).
- `GET /api/admin/dashboard` — usage + applications + documents + provider stats
  + LLM config in one payload.

**Tests** — `tests/test_admin_stats.py` (15): all routes 403 for a normal user;
DAU/WAU/MAU/YAU windows distinct & correct; per-user counts; application
by_status/by_window/by_provider; per-user ranking; document totals + used-count +
created-window; dashboard bundles everything; **`/api/admin/llm` and
`/api/admin/dashboard` never contain the LLM key**.

### Phase 38 — database performance ✅ (measured)

- **N+1s** from the audit fixed in Phase 27 (`list_applications`,
  `get_application`, `list_saved` → one `IN` prefetch each). Fresh scan: no
  per-row `db.get` loops remain in the service layer.
- **Indexes added** (propagated to existing DBs by `_ensure_additive_columns`'s
  index pass): `users.created_at` / `.last_active_at`, `job_applications.status`
  / `.created_at` / `.source` / `(user_id, normalized_job_key)` /
  `(user_id, job_offer_id)`, `generated_cvs.created_at`,
  `generated_letters.created_at`, `ats_boards.provider`.
- **Measured** (SQLite, synthetic, `bench_stats_p38.py`):

  | rows | usage | applications | documents | per_user(50) | apps_per_user(50) |
  |---|---|---|---|---|---|
  | 500 users / 2k apps | ~3 ms | 8.6 ms | 11.6 ms | 12.2 ms | 8.7 ms |
  | 5 000 users / 40k apps | 11.7 ms | 35.8 ms | 33.2 ms | 11.2 ms | 40.6 ms |

  Sub-50 ms at 10× scale — the aggregates grow ~linearly with row count on
  SQLite (PostgreSQL uses the new indexes better). Paginated per-user endpoints
  stay flat (~11 ms) regardless of table size.

### Phase 39 — test coverage consolidation ✅

Per-phase suites written as the phases landed. Phase 39 added
`tests/test_llm_error_paths.py` (6) for the LLM failure modes the mission calls
out: no key → clean `RuntimeError`, all models failing → `RuntimeError`, rate
limit → `RuntimeError`, pipeline guard maps LLM failure to `429` (not `500`),
unknown errors pass through, `get_config()` reports unconfigured.

New v2.5 test files: `test_job_search_async.py`, `test_job_link_integrity.py`,
`test_job_apply_matching.py`, `test_job_preference_extraction.py`,
`test_llm_config.py`, `test_llm_error_paths.py`, `test_admin_auth.py`,
`test_admin_ats_boards.py`, `test_admin_provider_stats.py`,
`test_admin_stats.py`, `test_security_audit_v25.py`.

### Phase 40 — security audit ✅ (all pass, incl. executable checks)

`tests/test_security_audit_v25.py` (8) + greps:

| check | result |
|---|---|
| every `/api/admin/*` route (16) behind `require_superadmin` | ✅ 0 unguarded (asserted in a test) |
| `is_superadmin` in a user-facing request schema | ✅ none (only `AdminUserUpdate`); signup ignores injected `is_superadmin`/`is_active`/`id` |
| real-looking API key committed (`nvapi-…` / `sk-…`) | ✅ none (only `nvapi-xxxx…` placeholder) |
| LLM key in `/api/health`, `/api/stats`, `/api/admin/llm`, `/api/admin/dashboard` | ✅ absent |
| provider imports a raw `httpx`/`requests` | ✅ none |
| `selenium` outside `browser.py` | ✅ none |
| `eval`/`exec`/`pickle`/`subprocess`/`os.system` in job / admin / stats code | ✅ none |
| LinkedIn `li_at`/Voyager/login/stealth | ✅ none (source clean) |
| `ats_board_registry` resilient to a DB error | ✅ falls back to the file/env list (asserted) |
| SSRF / HTTPS / CGNAT / redirect guards, size caps, `http.py` chokepoint | ✅ unchanged (68 tests) |
| scraped description → LLM instruction | ✅ still `html_to_text` plain text; only the delimited UNTRUSTED conversation reaches `call_gemini` |

### Phase 41 — documentation ✅

`README.md` (admin API table + "Administration" / "Provider fan-out" / "Job
links" sections + LLM/superadmin/fan-out env vars), `PROJECT_STRUCTURE.md`
(new modules + 10 design-rule rows + `_ensure_additive_columns` +
`gemini_client` refresh), `REFACTOR_REPORT.md` (this §13), `.env.example`
(`LLM_*`, `SUPERADMIN_EMAILS`, `JOB_SEARCH_*`, `ATS_BOARD_OVERLAY_TTL`),
`../API_FRONTEND_GUIDE.tex` + PDF (new §Administration, `JobSearchContext`
`excluded_keywords`/`salary_max`/`sectors`). Every documented route/field
verified against the code.

### Phase 42 — production validation ✅

| check | result |
|---|---|
| `python -m compileall .` | clean |
| `python -c "import main"` | OK |
| full offline suite `pytest -q` | **892 passed, 1 skipped, 0 failed, 0 errors** (893) — v2.4 was 771 |
| security greps + `test_security_audit_v25.py` | all pass (Phase 40 table) |
| `docker build --build-arg INSTALL_BROWSER=false` | OK — `cv-assistant:v25`, 585 MB, no browser |
| container `GET /api/health` | `200 {"status":"ok"}` |
| container: normal user → `/api/admin/dashboard` | `403` |
| container: signup with `is_superadmin:true` in body | ignored → account is `is_superadmin:false` |
| container: `SUPERADMIN_EMAILS` bootstrap on restart | `[admin] bootstrap promoted 1 account(s)` → `/me` shows `is_superadmin:true`, dashboard `200` |
| container: `/api/admin/dashboard` | 20 providers, usage/applications/documents, LLM config **without the key** |
| container: `POST /api/admin/ats-boards` + list | `201`, board appears, provider catalog present |
| container: `POST /api/jobs/search sources=["ashby"]` (real network) | `200`, 12 results, `provider_states:{"ashby":"available"}`, real `source_url` |
| container logs scanned for secrets | clean |
| additive migration (`_ensure_additive_columns`) | verified (isolated engine test + clean container startup) |

Runtime: full suite ~199 s (v2.4 ~130 s) — +122 tests, ~40 of them TestClient
admin tests (~1 s each) + the async fan-out tests' real sleeps (~15 s). No
single test is pathologically slow (`--durations` max 2 s, the pre-existing
`test_providers_http.py` throttle tests). No new runtime dependency.

### Not touched (v2.5)

The extraction & generation pipelines, `job_ranking` weights, `job_freshness`,
`job_dedup` core, `job_search_service`'s dedup/rank/persist stages, the browser
stack, `circuit.py`, `http.py`'s SSRF core, the 20 providers' parsing,
`cache_service.py`, the auth / password-reset flow, `NormalizedOffer`'s shape
(beyond the URL-trim validator + the 3 new optional `JobSearchContext` fields).
`sources` / `ProviderStatus` in the search response stay byte-identical.

---

## 14. — v2.6 : LLM multi-provider, profil utilisateur, événements d'usage, auto-candidature enrichie

> Phases 43–64. Journal WIP. Audit (Phase 43) ci-dessous = base + plan.

### Phase 43 — audit architectural (lecture seule)

Inspection du code réel v2.5. Aucune modification dans cette phase.

#### 43.1 Fan-out concurrent (`job_search_service._fan_out`)

`ThreadPoolExecutor(max_workers=min(N, JOB_SEARCH_MAX_CONCURRENCY=20))` créé
**par recherche**, drainé via `concurrent.futures.wait(timeout=remaining)` contre
une deadline monotonic unique. À l'échéance : `future.cancel()` (n'annule qu'un
future non démarré) + breaker + `temporarily_unavailable`.
`pool.shutdown(wait=False, cancel_futures=True)`.

- `http.py` = `httpx.Client` **synchrone**. Chaque provider a un `deadline`
  passé à `_search(ctx, *, limit, deadline)` et ses boucles de pagination
  vérifient `time.monotonic() >= deadline`. Un provider « gelé » l'est donc sur
  **un seul** `client.stream(...)` — borné par `JOB_PROVIDER_TIMEOUT` (10 s) +
  1 retry. Le vrai worst-case n'est pas infini.
- **Faiblesse 1 (multi-worker) :** un pool de 20 threads **par recherche**. 5
  recherches simultanées = jusqu'à 100 threads + 100 connexions sortantes. Rien
  ne borne la concurrence *globale* du process.
- **Faiblesse 2 :** un thread bloqué sur un socket n'est pas interrompu — il
  finit sa requête (≤ ~20 s) puis meurt. Fuite bornée mais réelle sous charge.
- **Faiblesse 3 :** la deadline est vérifiée entre `wait()`, pas pendant le
  parsing d'un gros corps déjà téléchargé (rare).
- **Décision D8 :** garder le pool de threads (c'est le bon patron pour une pile
  sync ; une migration asyncio = 20 providers + `http.py` + ~15 fichiers de
  tests providers + fixtures conftest → risque élevé, viole « additif »).
  **Améliorer :** exécuteur **partagé borné** au niveau module
  (`JOB_SEARCH_GLOBAL_CONCURRENCY`) pour plafonner les threads/connexions sur
  l'ensemble des recherches simultanées ; mesurer avant/après (1/5/10/20
  providers × rapide/lent/bloqué). Documenter le choix hybride.

#### 43.2 Liens des offres (`_with_usable_link`, `NormalizedOffer._clean_url`)

`_clean_url` (mode="before") ne fait que `strip()`. `_with_usable_link` garde
l'offre si `source_url` commence par `http://`/`https://`, sinon drop (loggé).
`_upsert` écrit `row.source_url = offer.source_url`. `JobOfferOut.source_url`
requis.

- **Manque :** pas de rejet de `https://user:pass@host/` (credentials dans
  l'URL), pas de rejet de `https://` sans host, pas de nettoyage du fragment,
  pas de garde sur des hôtes internes/loopback dans l'URL affichée. Validation
  uniquement dans le service, pas à la frontière API.
- **Phase 45 :** durcir `_clean_url` (rejet userinfo / host vide / scheme
  interdit / host loopback-littéral) → l'offre est **droppée**, jamais
  réparée ; ajouter la matrice de tests demandée ; valider aussi dans `_to_out`
  / `_offer_out`.

#### 43.3 Auto-candidature (`job_application_service`)

`_select_best_cv` (langue → ATS → récence), `_match_score` (réutilise
`job_ranking.score` mais avec un `JobSearchContext(query=req.context)` — signal
**pauvre**), garde anti-doublon (`job_offer_id` OU `normalized_job_key`),
7 colonnes de traçabilité. `ApplicationStatus` = manual_required /
requires_user_action / unavailable / failed / duplicate.

- **Manque :** `match_reasons`, `missing_skills` ; pas de génération de lettre
  liée à la candidature ; pas d'état `prepared` ; matching sans profil
  structuré ; `attempt_count` toujours = 1 (jamais incrémenté sur re-tentative).
- **Phases 46+47 :** profil utilisateur structuré (Phase 47) d'abord, puis
  moteur de matching riche (compétences / titre / localisation / langue /
  salaire / secteur / type de contrat) produisant `match_score` +
  `match_reasons` + `missing_skills` ; sélection CV pondérée par le profil ;
  génération optionnelle d'une lettre (via le pipeline existant, faits du profil
  uniquement) tracée sur la candidature ; état `prepared`. **Toujours aucune
  soumission externe.**

#### 43.4 Profil utilisateur

**Inexistant.** Le matching dépend de `req.context` (texte libre) et de la
langue du CV. Aucune table de préférences par utilisateur.

- **Phase 47 :** nouveau modèle `UserProfile` (1:1 `users`), colonnes
  `target_titles` / `skills` / `languages` / `locations` / `remote_preference` /
  `employment_types` / `salary_min` / `salary_max` / `salary_currency` /
  `sectors` / `excluded_keywords` / `excluded_companies` / `preferred_companies`
  / `experience_level` / `extra` (JSON). CRUD `GET/PUT /api/profile`
  (owner-scoped, jamais cross-user). `JobSearchContext.from_profile()` +
  fusion avec l'extraction.

#### 43.5 Extraction des préférences (`job_preference_service`)

1 `call_gemini` + `_local_enrich` (regex FR+EN : salaire, remote, « pas de
stage », séniorité). Merge via `JobSearchContext.merge`. Dégrade proprement.

- **Manque :** extraction des **titres** (« poste backend » → `target_titles`),
  `employment_types` (full_time/part_time/contract/internship), synonymes,
  fusion avec le profil persistant.
- **Phase 48 :** pipeline explicite parsing local déterministe → LLM (si
  nécessaire) → **validation stricte** → normalisation → fusion profil. Le LLM
  n'est jamais source de vérité sans validation. Tests FR/EN/contradiction/
  malveillant, plusieurs sans LLM.

#### 43.6 LLM (`gemini_client`)

**Un seul client** `OpenAI(base_url=settings.LLM_BASE_URL, api_key=...)`, lazy +
`reset_client()`. Boucle de fallback sur `settings.llm_models`. Config dans
`Settings` (`LLM_*`, `NVIDIA_*` alias). `call_gemini(prompt, request_type=...)`
= **seul point d'entrée** (11 modules l'importent — voir
`conftest._LLM_CONSUMER_MODULES`). `is_configured()` / `get_config()` (sans clé)
/ `get_stats()`.

- **Manque :** un seul « provider » implicite (OpenAI-compatible). Pas
  d'abstraction, pas d'OpenAI natif / Gemini / endpoint custom nommés.
- **Décision D9 :** Gemini expose un endpoint **OpenAI-compatible**
  (`https://generativelanguage.googleapis.com/v1beta/openai/`) → les 5 providers
  passent par le SDK `openai` avec des `base_url` différents. **Aucune nouvelle
  dépendance.**
- **Phase 49 :** package `services/llm/` — `BaseLLMProvider` (interface
  `complete(prompt, system, *, temperature, max_tokens, timeout) -> str`,
  `name`, `model`, `healthcheck()`), implémentations `NvidiaProvider`
  (OpenAI-compat, défaut), `OpenAIProvider`, `GeminiProvider` (OpenAI-compat
  Google), `OpenAICompatibleProvider`, `CustomEndpointProvider`. `LLMService`
  choisit le provider via `settings.LLM_PROVIDER`, garde cache SHA-256 +
  rate-limit + stats + chaîne de fallback modèles. `gemini_client.call_gemini`
  devient une **façade** sur `LLMService` — signature inchangée, 0 churn pour
  les 11 appelants.

#### 43.7 Config LLM

`LLM_API_KEY/BASE_URL/MODEL/MODELS_FALLBACK/TEMPERATURE/MAX_OUTPUT_TOKENS/
REQUEST_TIMEOUT/MAX_CALLS_PER_MINUTE` + `NVIDIA_API_KEY/MODEL`. `SUPERADMIN_EMAILS`.

- **Phase 50 :** ajouter `LLM_PROVIDER` (nvidia|openai|gemini|openai_compatible|
  custom) + les blocs `OPENAI_*`, `GEMINI_*`, `OPENAI_COMPATIBLE_*`,
  `CUSTOM_LLM_*`, `NVIDIA_BASE_URL`. Résolution : le provider actif prend sa
  clé/URL/modèle de son bloc, avec repli sur les `LLM_*` génériques. **Aucune
  clé loggée / retournée / dans health / stats / dashboard / erreurs.**

#### 43.8 Config LLM dynamique + test

`GET /api/admin/llm` (dashboard, sans clé). Pas de `PATCH`, pas de `/test`.
`reset_client()` existe déjà.

- **Phase 51 :** `PATCH /api/admin/llm` (provider / model / base_url /
  temperature / max_tokens / timeout / rate_limit ; **jamais la clé via ce
  PATCH** — la clé reste une variable d'environnement/secret). Overrides
  persistés dans une petite table `runtime_config` (clé/valeur JSON) chargée
  au boot et après chaque PATCH → `LLMService.reload()`. **Validation +
  healthcheck + rollback automatique** si la nouvelle config échoue au test.
- **Phase 52 :** `POST /api/admin/llm/test` — prompt minimal (`"ping"`),
  respecte timeout + rate-limit, retourne provider / model / durée_ms /
  ok / erreur **assainie**. Jamais la clé.

#### 43.9 Boards ATS (`admin_service`, `ats_board_registry`)

`AtsBoard(provider, token, enabled, label, note, created_by)` + unique
`(provider, token)`. `resolved_tokens` = fichiers/env **∪** actives **∖**
désactivées, best-effort, caché `ATS_BOARD_OVERLAY_TTL`. CRUD +
`POST /ats-boards/test` (sans persistance).

- **Manque :** pas d'historique par board (dernière exécution, nb d'offres,
  erreurs) ; `test` ne persiste rien.
- **Phase 53 :** colonnes additives `last_tested_at` / `last_test_ok` /
  `last_test_offer_count` / `last_test_error` sur `AtsBoard` ;
  `POST /api/admin/ats-boards/{id}/test` (par id, persiste le résultat) ;
  `GET /api/admin/ats-boards` enrichi. Fusion + validation + SSRF inchangés.

#### 43.10 Statistiques + événements

`StatsService` = agrégats SQL purs sur les tables existantes. `last_active_at`
(un seul timestamp) → DAU/WAU/MAU/YAU. **Pas de table d'événements** →
nb de recherches / vues d'offres absents.

- **Phase 54 :** modèle `UsageEvent(user_id, kind, created_at, meta JSON)` —
  `kind` ∈ {login, search, job_view, job_saved, application_prepared,
  document_created, document_downloaded}. Enregistré aux points clés
  (best-effort, jamais bloquant, **aucune donnée privée** : pas de contenu de
  requête/CV, seulement des compteurs/ids). `StatsService` calcule DAU/WAU/MAU/
  YAU depuis les events **distincts** (plus fin que `last_active_at`, qu'on
  garde en repli) + sessions / recherches / vues par utilisateur.
  `USAGE_EVENTS_ENABLED` (défaut true), rétention `USAGE_EVENT_RETENTION_DAYS`.
- **Phases 55/56/57 :** étendre les endpoints stats + dashboard (section
  boards, fenêtres today/week/month/year, match score moyen, CV/lettres
  utilisés, doublons, échecs).

#### 43.11 `is_superadmin` / sécurité

`require_superadmin` sur tout `/api/admin/*` (router-level). `is_superadmin`
dans aucun schéma de requête sauf `AdminUserUpdate`. Bootstrap
`SUPERADMIN_EMAILS` (promote-only). Gardes anti-auto-verrouillage
(`AdminService.update_user`). SSRF/HTTPS/CGNAT/redirect/size caps intacts
(68 tests). Descriptions scrapées → `html_to_text` → aucun chemin LLM.

- **Phases 58/59 :** re-vérifier en tests (déjà couvert v2.5) + ajouter DNS
  rebinding, IDOR profil, accès cross-user profil/events, URLs à credentials.

#### 43.12 N+1 / requêtes

- `list_applications` / `get_application` / `list_saved` : **déjà corrigés** en
  v2.5 (prefetch `IN`).
- `_upsert` : prefetch chunké (v2.4).
- `StatsService` : agrégats purs. **`per_user` fait 4 requêtes GROUP BY** + 1
  liste — OK (borné par `limit`). `application_stats` fait ~7 requêtes (4
  fenêtres + status + provider + avg) — acceptable pour un dashboard.
- **Nouveau risque Phase 54 :** `UsageEvent` peut grossir vite → index
  `(user_id, kind, created_at)` + `(kind, created_at)` + purge.
- **Manque d'index actuel :** `job_offers.posted_at` (tri), `saved_jobs.job_offer_id`
  (jointure `list_saved`), `ats_boards` déjà indexé `provider`.

#### 43.13 API / modèles / config / Docker / tests

- 13 routes `/api/admin/*`, toutes gardées. 70 fichiers de tests, **892 passed /
  1 skipped**. Docker `INSTALL_BROWSER=false` OK (585 MB). `_ensure_additive_columns`
  gère la dérive de schéma (pas d'Alembic).
- Incohérence mineure : `models/job_application.py` docstring liste encore
  4 statuts (pas `duplicate`) ; `admin_router.py` docstring dit « Phases 31→37 ».
- `main.py` : lifespan fait `init_db` + bootstrap + MinIO + browser shutdown.

### Décisions v2.6

| # | Décision | Raison |
|---|---|---|
| D8 | Garder le pool de threads ; ajouter un exécuteur **partagé borné** (concurrence globale) ; mesurer ; documenter l'hybride | asyncio = réécriture de 20 providers + http.py + tests ; le pool + deadline résout déjà le worst-case |
| D9 | Les 5 providers LLM via le SDK `openai` (base_url variable) ; Gemini via son endpoint OpenAI-compatible | **aucune nouvelle dépendance** ; `call_gemini` reste la façade |
| D10 | `PATCH /api/admin/llm` persiste des overrides dans `runtime_config` (clé/valeur) ; **jamais la clé API** via l'API ; rollback auto si healthcheck KO | la clé reste un secret d'environnement ; config appliquée à chaud |
| D11 | `UsageEvent` : compteurs/ids uniquement, jamais de contenu ; best-effort ; index + rétention ; `last_active_at` gardé en repli | statistiques fines sans exposer de données privées ; pas de pipeline lourd |
| D12 | `UserProfile` 1:1, owner-scoped strict ; alimente matching + fusion extraction ; jamais exposé cross-user | le matching a besoin d'un signal structuré persistant |
| D13 | Auto-candidature : toujours **aucune soumission externe** ; ajout de `prepared`, `match_reasons`, `missing_skills`, lettre optionnelle tracée | contrainte absolue inchangée ; on améliore la *préparation* |

### Phase 44 — bounded concurrent provider collection ✅ (D8: hybrid thread-pool, documented)

**Decision:** keep the thread pool (right pattern for the sync `httpx` /
SQLAlchemy stack — a full asyncio port = 20 providers + `http.py` + ~15 test
files, high risk, non-additive). **Improve the multi-worker weakness** instead:
before v2.6 each search built its *own* `ThreadPoolExecutor(min(N, 20))`, so K
simultaneous searches ⇒ up to `K·20` threads / connections.

- **Shared process-wide pool** (`_executor()`, sized `JOB_SEARCH_GLOBAL_CONCURRENCY`
  = 32, rebuilt on setting change, `atexit` teardown). Never shut down per
  search.
- **Per-search cap** kept via a `threading.Semaphore(JOB_SEARCH_MAX_CONCURRENCY)`
  each provider task acquires — bounds one search without freezing a whole
  worker slot for a task that is already past the deadline.
- One shared monotonic `JOB_SEARCH_DEADLINE` unchanged; abandoned tasks keep
  running on the shared pool bounded by the provider's own `JOB_PROVIDER_TIMEOUT`,
  then the worker is freed.

**Preserved:** `http.py` chokepoint, SSRF/HTTPS/CGNAT/redirect/size caps,
per-request timeouts, circuit breaker, `ProviderMetrics`, `provider_states`,
per-provider isolation, the coarse `sources` map (byte-identical outcomes).

**Measured** (`bench_fanout_p44.py`, in-memory fakes, `GLOBAL=32`):

| scenario | peak threads over baseline |
|---|---|
| 1 search × 20 providers | +22 |
| 5 searches × 20 providers | +18 |
| 10 searches × 20 providers | +11 |

Before: 10 × 20 = up to **200** worker threads + connections. After: bounded to
`GLOBAL_CONCURRENCY`.

**Tests** — `tests/test_job_search_async.py` +2: shared pool bounds threads
across 6 concurrent searches (< 30 over baseline for 6×10 providers); per-search
semaphore still caps a single search at `JOB_SEARCH_MAX_CONCURRENCY`. All 15 in
the file pass. `JOB_SEARCH_GLOBAL_CONCURRENCY` added to `config.Settings`.

### Phase 45 — every offer link is real *and* safe ✅

`schemas.jobs.NormalizedOffer._clean_url` (mode="before") now **blanks** — not
just trims — a `source_url`/`company_url` that: has control characters, is not
`http(s)`, carries credentials (`https://user:pass@host`, `https://token@host`),
or has no host. A blanked `source_url` then fails the search-service drop check.

`job_search_service._usable_offer_url(url)` — the drop predicate used by
`_with_usable_link`: `http(s)` + host + no userinfo + no control chars + ≤ 2048
chars + host is **not** an internal name (`localhost`, `metadata`, …) or a
private / loopback / link-local / reserved / multicast IP literal (incl. `[::1]`,
`169.254.169.254`). An offer that fails is **dropped** (logged per source),
never repaired, never fabricated. (This is display safety; `http.py`'s SSRF
guard still re-checks at fetch time.)

**Tests** — `tests/test_job_link_integrity.py` +3 (26 total): a 16-case bad-URL
matrix (empty / relative / `javascript:` / `ftp:` / `data:` / credentials /
no-host / `localhost` / `127.0.0.1` / `10.x` / `169.254.169.254` / `[::1]` /
CRLF) all drop the offer while a sibling good offer survives; a 4-case
safe-URL matrix kept verbatim (query params, fragment, `http`, subdomains);
the `NormalizedOffer` validator blanks a credential URL.

### Phase 47 — persistent structured user profile ✅ (D12)

New `models.UserProfile` (1:1 with `users`, `user_id` is the PK — no id to
tamper with). Fields: `target_titles` / `skills` / `languages` / `locations` /
`employment_types` / `sectors` / `excluded_keywords` / `excluded_companies` /
`preferred_companies` (lists, capped, deduped) + `remote_preference` /
`experience_level` / `salary_min` / `salary_max` / `salary_currency` + `extra`
(small primitive JSON, never treated as instructions).

`schemas/profile.py` (`UserProfileIn`/`Out`, strict validation — enum checks,
`employment_types` vocabulary, currency upper-cased, list caps).
`services/user_profile_service.py` — owner-scoped get/upsert/delete +
`to_search_context(profile)` → `JobSearchContext` (only *set* fields carried,
nothing invented; `target_titles[0]` → query, rest → keywords).
`profile_router.py` — `GET/PUT/DELETE /api/profile` (Bearer, owner-scoped; the
row PK is the caller's id so cross-user access is structurally impossible).
Wired in `main.py`; CORS gained `PUT`.

**Tests** — `tests/test_user_profile.py` (8): empty-when-none, PUT/GET roundtrip
+ normalisation (dedup, bogus employment-type dropped, currency upper-cased),
upsert, bad enum / negative salary → 422, **owner isolation** (user B never sees
user A's data), delete, 401, `to_search_context` mapping.

Also created `models.UsageEvent` (Phase 54) so `models/__init__` stays valid.

### Phase 46 — richer auto-apply preparation ✅ (D13 — still never submits externally)

**New:** `services/job_match_service.evaluate(offer, ctx) -> MatchResult`
(deterministic, LLM-free): `score` (reuses `job_ranking.score`) + `reasons`
(human-readable "why": title / skills-present / work-mode / location / seniority
/ salary-meets-min / language / sector) + `missing_skills` (wanted skills absent
from the posting). Never empty (falls back to a "weak match" reason).

`JobApplicationService.apply()` now:
- builds a **merged `JobSearchContext`** from the persistent `UserProfile`
  (Phase 47) + anything the caller typed (`_build_context`);
- runs the match on **every** apply and persists `match_score` /
  `match_reasons` / `missing_skills` (new `job_applications` columns) — returned
  in `ApplicationResponse` + `ApplicationOut`;
- on a **re-attempt** (duplicate) increments `attempt_count` + `last_attempt_at`
  on the existing record instead of ignoring it;
- **opt-in `prepare=True`** → status `prepared` (new `ApplicationStatus` value —
  still nothing that means "submitted externally");
- **opt-in `generate_letter=True`** (+ `cv_profile` in the request) → generates
  a tailored cover letter through the **existing** `run_letter_pipeline`, stores
  the PDF in MinIO + a `GeneratedLetter` row, links `letter_id`. Best-effort:
  no `cv_profile` / no MinIO / any failure → a note in the message, `prepared`
  unchanged, never raises.

**CV auto-selection** unchanged (language → ATS score → recency — the
`GeneratedCV` row carries no structured content to match on; documented
limitation).

**Preserved:** default `apply()` behaviour byte-identical (`manual_required` /
`requires_user_action` / `unavailable` / `failed`); no external submission, no
credentials, no anti-bot anything.

**Tests** — `tests/test_auto_apply.py` (8): match uses the profile + reports
reasons + `missing_skills` (`Kubernetes` wanted-not-present, `Python`
present-not-missing) + persisted; weak match still has a reason; default apply
still `manual_required`; `prepare=True` → `prepared`; re-apply increments
`attempt_count` (1 row, count 3); `generate_letter` without `cv_profile` notes
it; with `cv_profile` + fake MinIO creates + links the letter; explicit
`letter_id` linked.

### Phase 48 — smarter preference extraction ✅

`job_preference_service._local_enrich` (deterministic, LLM-free) now also
extracts:
- **role / title** → `query` (only when unset) — intent phrases (`poste de …`,
  `looking for a … role`, `job as …`) + a job-title-head grammar
  (`… developer/engineer/développeur/ingénieur/manager/analyst/scientist/…`
  optionally + a tech token: `python`, `data`, `backend`, …) + bare roles
  (`backend`, `full-stack`, `devops`, `data scientist`, `sre`, `ux`, …).
- **contract type** → `contract_type` (`permanent`/`fixed-term`/`freelance`/
  `apprenticeship` from CDI / CDD / freelance / alternance …).

`JobPreferenceExtractor.extract(..., base_context=)` — the caller's **persistent
`UserProfile`** (via `to_search_context`) is the floor; the passed context then
the conversation refine it. `POST /api/jobs/context` loads the profile
automatically and accepts `save_to_profile: true` to write the resolved context
back (best-effort, never fails the request). The LLM is still never trusted
without the local validation + merge.

**Tests** — `tests/test_job_preference_extraction.py` +12 (39 total): role
titles into `query` (FR + EN, 5 cases) incl. not-overriding a set query;
contract types (4); `base_context` fuses the profile with the LLM + local
enrich; `/api/jobs/context` `save_to_profile` writes the profile.

### Phases 49 + 50 — multi-provider LLM abstraction ✅ (D9 — no new dependency)

`services/llm/` package: `BaseLLMProvider` (interface: `models`, `configured`,
`public_config()` [no key], `complete(system, prompt) -> (text, model_used)`
with the model fallback chain, `healthcheck()`) + `OpenAILikeProvider` (the one
real impl — OpenAI Python SDK at a configurable `base_url`) + 5 named providers:
`NvidiaProvider` / `OpenAIProvider` / `GeminiProvider` (Google's OpenAI-compat
endpoint) / `OpenAICompatibleProvider` / `CustomEndpointProvider`.
`build_active_provider()` picks from `settings.LLM_PROVIDER`.

`gemini_client` keeps the SHA-256 response cache + per-minute rate limit + call
counters and now **delegates the API call** to the active provider —
`call_gemini(prompt, request_type=...)` unchanged for its 11 callers.
`_get_client()` / `_get_model()` are compat shims over the provider.

**Config (Phase 50):** `LLM_PROVIDER` + generic `LLM_*` (shared fallback) +
per-provider blocks `NVIDIA_*` / `OPENAI_*` / `GEMINI_*` / `OPENAI_COMPATIBLE_*`
/ `CUSTOM_LLM_*` (each: `_API_KEY` / `_MODEL` / `_BASE_URL`). Resolution: the
active provider's own var wins, the generic `LLM_*` is the fallback. (v2.5's
"`LLM_*` wins over `NVIDIA_*`" is reversed — the per-provider block is now
primary. 2 v2.5 tests updated.)

**Key handling unchanged:** never logged (logs show `provider` + model name +
`prompt_chars`), never in `get_config()` / `llm_public_config()` /
`/api/health` / `/api/stats` / any admin route; `base._sanitize` strips
`sk-`/`nvapi-`/`AIza` tokens + URLs from error messages.

**Tests** — `tests/test_llm_providers.py` (9): all 5 construct, default base
URLs, `LLM_PROVIDER` selection + unknown→nvidia fallback, per-provider settings
resolution, `complete` refuses without a key / walks the fallback chain /
`LLMError` when all fail, `_sanitize` strips keys+URLs. `test_llm_config.py`
updated for the new resolution order.

### Phases 51 + 52 — runtime LLM config + test ✅ (D10)

New `models.RuntimeConfig` (key/JSON) + `services/runtime_config_service.py`.
`PATCH /api/admin/llm` (superadmin): body has provider / model / models_fallback
/ base_url / temperature / max_output_tokens / request_timeout /
max_calls_per_minute — **no `api_key` field** (keys are environment secrets; an
`api_key` in the body is silently ignored, never stored). Flow: validate →
apply onto `settings` → `reset_client()` → **healthcheck** → on failure **roll
back settings + reject 400**, on success persist the merged override.
`apply_llm_overrides(db)` re-applies the persisted override on startup
(`main.lifespan`).

`POST /api/admin/llm/test` (superadmin): `gemini_client.healthcheck()` — a
minimal `"ping"` completion, rate-limited, timeout + fallback respected →
`{provider, model, duration_ms, ok, error}` with the error **sanitised**.

**Tests** — `tests/test_llm_admin.py` (8): PATCH + test require superadmin;
PATCH changes + persists + no `api_key` in the response/row; unknown provider
→ 400; **healthcheck failure rolls back settings + persists nothing**;
override re-applied on startup; `/llm/test` returns health; key never in the
`/llm/test` response.

### Phase 53 — advanced ATS board management ✅

`AtsBoard` +4 additive columns: `last_tested_at` / `last_test_ok` /
`last_test_offer_count` / `last_test_error` (sanitised). New
`POST /api/admin/ats-boards/{id}/test` — runs the live `_fetch_board` and
**persists** the result on the row; `GET /api/admin/ats-boards` returns them.
The bundled-file + `<X>_BOARDS_FILE` + env + DB fusion, slug/https validation
and the fetch-time SSRF guard are unchanged.

### Phase 54 — privacy-safe usage-event log ✅ (D11)

`models.UsageEvent` (`user_id`, `kind` ∈ {login, search, job_view, job_saved,
application_prepared, document_created, document_downloaded}, `meta` JSON,
`created_at`; indexed `(user_id, kind, created_at)` + `(kind, created_at)`).
`services/usage_event_service.record()` — **counters + ids only** (`_safe_meta`
keeps ≤ 8 primitive keys, strings ≤ 60 chars); best-effort (rollback, never
raises, never blocks). `USAGE_EVENTS_ENABLED` (on) + `USAGE_EVENT_RETENTION_DAYS`
(400) with a startup `prune()`.

Emitting routes: `/api/auth/signin` (login), `/api/jobs/search` (search),
`/api/jobs/{id}` (job_view), `/api/jobs/{id}/save` (job_saved),
`/api/jobs/{id}/apply` (application_prepared), `/api/generate-cv` +
`/api/generate-letter` (document_created), the two `/download` routes
(document_downloaded).

### Phases 55–57 — richer analytics + dashboard ✅

- `StatsService.usage_overview` — DAU/WAU/MAU/YAU from **distinct event
  user_ids** when any event exists (`activity_source: "events"`), else the
  `last_active_at` fallback (`"last_active_at"`).
- `StatsService.per_user` — + `session_count` / `search_count` /
  `job_view_count` per user (event `GROUP BY`).
- `application_stats` — + `avg_match_score`, `duplicate_count`, `failed_count`,
  `prepared_count`, `cv_used_count`, `cover_letter_used_count`.
- `AdminService.boards_overview()` + `GET /api/admin/dashboard` now has a
  **`boards`** section (per provider: total / enabled / disabled / last_test_ok).

**Tests** — `tests/test_usage_events.py` (8): record writes / rejects unknown
kind + missing user / strips non-primitive meta / disabled flag / prune; routes
emit search + job_view; overview switches to the event source; per-user event
counts. `tests/test_admin_stats.py` +2; `tests/test_admin_ats_boards.py` +3.

### Phases 58 + 59 — is_superadmin hardening + v2.6 security audit ✅

`tests/test_security_v26.py` (17) + greps:

| check | result |
|---|---|
| every `/api/admin/*` route (19) behind `require_superadmin` | ✅ 0 unguarded (asserted) |
| `is_superadmin` / `is_active` in a user-facing request schema (signup, `UserProfileIn`, `JobContextRequest`) | ✅ none; the profile PUT + context POST ignore an injected `is_superadmin` |
| LLM key in `/api/health` `/api/stats` `/api/admin/llm` `/api/admin/llm/test` `PATCH /api/admin/llm` `/api/admin/dashboard` | ✅ absent (all 6 asserted) |
| `PATCH /api/admin/llm` stores a key | ✅ never — no `api_key` field; `api_key`/`nvidia_api_key` in the body ignored, nothing key-shaped in `runtime_config` |
| profile owner isolation | ✅ user B never sees user A's profile |
| `usage_events` free text | ✅ `_safe_meta` caps strings at 60 chars; content is never passed |
| a new service module imports a raw HTTP client / `urllib` | ✅ none (`job_match` / `user_profile` / `usage_event` / `runtime_config`) |
| `services/llm/` imports `httpx`/`requests` or uses `eval`/`exec` | ✅ none — openai SDK only |
| SSRF / HTTPS / CGNAT / redirect / size caps / `http.py` chokepoint | ✅ unchanged (68 tests) |

### Phase 61 — performance (measured)

- **Fan-out** (`bench_v26.py`, in-memory fakes, `JOB_SEARCH_DEADLINE=8s`):
  fast providers ≈ latency regardless of count (1→20); 20 slow providers
  (1 s each) all complete in **~1.03 s** (concurrent, not serial); 20 blocked
  ≈ 3 ms; a fast/slow mix bounded by the slow ones or the deadline.
  Thread bound across simultaneous searches: **+11–22** over baseline for
  1–10 concurrent 20-provider searches (was up to +200).
- **Stats vs table size** (SQLite; PostgreSQL does better with the indexes):

  | rows | usage_overview | application_stats | per_user(50) | documents |
  |---|---|---|---|---|
  | 1k users / 5k apps / 20k events | ~50 ms | ~17 ms | ~16 ms | ~12 ms |
  | 5k / 40k / 150k events | ~460 ms | ~100 ms | ~15 ms | ~47 ms |
  | 5k / 100k / 400k events | ~1.6 s | ~300 ms | ~20 ms | ~107 ms |

  `usage_overview` degrades with the event count (`COUNT(DISTINCT user_id)` over
  a year window) — mitigations: a covering index `ix_usage_events_time_user`
  `(created_at, user_id)`, a single range-scan CASE query, **and a 60 s cache on
  `GET /api/admin/dashboard`** (`?refresh=true` bypasses). Paginated per-user
  endpoints stay flat regardless of size.
- No premature optimisation elsewhere. No new runtime dependency across v2.6.

### Phase 62 — documentation ✅

`README.md` (multi-provider LLM section, user-profile & matching section,
`/api/profile` + admin API rows, `LLM_PROVIDER` + per-provider + `USAGE_*` +
`JOB_SEARCH_GLOBAL_CONCURRENCY` env rows, updated fan-out section),
`PROJECT_STRUCTURE.md` (new modules + 8 design-rule rows), `REFACTOR_REPORT.md`
(this §14), `.env.example` (full LLM provider blocks + usage-events + fan-out),
`../API_FRONTEND_GUIDE.tex` + PDF (admin `PATCH /llm` / `/llm/test` /
`/{id}/test` / dashboard-boards rows, `/api/profile` subsection, enriched
apply-request fields). Every documented route/field checked against the code.

### Phase 63 — production validation ✅

| check | result |
|---|---|
| `python -m compileall services config.py main.py schemas models` | exit 0 |
| `python -c "import main"` | OK |
| full offline suite `pytest -q` (single process) | **988 passed, 1 skipped, 0 failed, 0 errors** (989) — v2.5 was 892 |
| secret greps (`nvapi-` / `sk-` / `AIza` in the tree) | none (only placeholders / test dummies) |
| `is_superadmin` / `is_active` in a user-facing request schema | none |
| `docker build --build-arg INSTALL_BROWSER=false` | OK — `cv-assistant:v26`, 586 MB, no browser |
| container `GET /api/health` | `200`, `gemini_configured:true` |
| container: signup `{is_superadmin:true}` | ignored → normal account |
| container: normal → `/api/admin/dashboard` / `/api/profile` | `403` / `200` |
| container: profile `PUT {is_superadmin:true}` | field absent from response, ignored |
| container: `SUPERADMIN_EMAILS` bootstrap on restart | `bootstrap promoted 1 account(s)` |
| container: `GET /api/admin/llm` | provider + 5 `available_providers`, **no key** |
| container: `PATCH /api/admin/llm` (dummy key) | healthcheck fails → **400 "rolled back"**; injected `api_key` ignored |
| container: `POST /api/admin/llm/test` | `{ok:false, error:"…401…"}` — **no key in the error** |
| container: `GET /api/admin/dashboard` | has `boards` section, **no key leak** |
| container: `POST /api/jobs/search sources=["ashby"]` (real net) | `200`, 12 offers, `provider_states:{"ashby":"available"}`, real `source_url` |
| container: `GET /api/admin/stats/usage` | `activity_source:"events"` (login events recorded) |
| container logs scanned for secrets | clean |

Full suite ~310 s (v2.5 ~200 s) — +96 tests, most `TestClient` admin/profile/LLM
(~1 s each). `--durations` max 2 s (pre-existing `test_providers_http.py`
throttle). **No new runtime dependency** across all of v2.6.

### Not touched (v2.6)

The extraction & generation pipelines, `job_ranking` weights, `job_freshness`,
`job_dedup`, `job_search_service`'s dedup/rank/persist stages, the browser
stack, `circuit.py`, `http.py`'s SSRF core, the 20 providers' parsing,
`cache_service.py`, the auth / password-reset flow, the v2.5 admin
users/boards/stats endpoints (extended, not replaced). `call_gemini`'s
signature; `sources` / `ProviderStatus` in the search response.

---

## 15. — v2.7 : LLM unifié (anti-redondance), dashboard utilisateur, stats par période

> Phases 65–78. Journal WIP.

### Phase 65 — audit ciblé

**15.1 Redondance LLM (le point n°1 de la mission).** `services/llm/providers.py`
définit 5 classes — `NvidiaProvider`, `OpenAIProvider`, `GeminiProvider`,
`OpenAICompatibleProvider`, `CustomEndpointProvider` — qui **ne diffèrent que
par leur `__init__`** (base_url / key / model par défaut). Chacune sous-classe
`OpenAILikeProvider` (l'implémentation réelle, ~30 lignes). C'est exactement la
redondance que la mission veut supprimer : « OpenAI-compatible est un transport,
pas une raison de créer N implémentations ».
**Décision D14 :** une seule classe `OpenAICompatibleProvider(name, base_url,
api_key, models, …)` + une table `_PROVIDER_DEFAULTS` (7 entrées : nvidia,
openai, gemini, **mistral**, **groq**, openai_compatible, custom).
`build_active_provider()` lit `settings` et construit **une** instance. Aucune
sous-classe par provider. Refs externes : seul `gemini_client.py` importe
(`BaseLLMProvider`, `LLMError`, `build_active_provider` — inchangés) +
`test_llm_providers.py` (à mettre à jour).

**15.2 Pas de `if provider == …` dans le code métier.** Vérifié : les 11 modules
appellent `call_gemini(prompt, request_type=…)`. Zéro branchement provider hors
`services/llm/`. `gemini_client` délègue à `_active_provider().complete()`.
Rien à corriger côté métier — l'abstraction est déjà bonne, seule l'impl des
providers est redondante.

**15.3 `runtime_config` / config dynamique.** `PATCH /api/admin/llm` (Phase 51)
persiste des overrides dans `runtime_config`, applique sur `settings`,
healthcheck + rollback. Fonctionne ; il suffira d'étendre l'enum provider
autorisé à mistral/groq. **Aucune clé** acceptée/retournée — conservé.

**15.4 Dashboard.** `GET /api/admin/dashboard` (superadmin) agrège usage /
applications / documents / providers / boards / llm, cache 60 s. **Aucun
dashboard utilisateur** — `StatsService` a `usage_overview` / `application_stats`
/ `document_stats` (globaux) + `per_user` / `applications_per_user`
(superadmin, paginés). Il faut des variantes **scopées `current_user.id`**.
**Décision D15 :** `services/user_dashboard_service.py` réutilisant les mêmes
patrons SQL, + `GET /api/dashboard` (un seul endpoint agrégé : activity / jobs /
applications / documents / matching / providers), strictement `where(user_id ==
current_user.id)`. Aucun `user_id` accepté du client.

**15.5 UsageEvent.** `EVENT_KINDS` = login / search / job_view / job_saved /
application_prepared / document_created / document_downloaded. Pas de `session`
distinct de `login` (ok — `login` = session). Indexes : `(user_id,kind,created_at)`,
`(kind,created_at)`, `(created_at,user_id)`. Suffisant pour un dashboard
par-utilisateur (le 1er index couvre `where user_id=? and kind=? group by`).

**15.6 Stats par période.** `StatsService._WINDOWS = {day:1, week:7, month:30,
year:365}`. Réutilisable tel quel. `application_stats.by_window` existe déjà ;
il manque le `by_window` **par utilisateur** et par statut.

**15.7 Auto-apply / extraction / job-search / ATS boards / provider stats.**
Tous solides après v2.6. Améliorations v2.7 = incréments mesurés uniquement
(provider stats : `empty_result_rate` / `timeout_rate` si dérivables de
`ProviderMetrics` sans nouveau state ; extraction : quelques patrons ;
job-search : rien — le fan-out partagé + deadline est déjà optimal).

**15.8 Frontend.** Pas de code frontend dans le repo (`API_FRONTEND_GUIDE.tex`
documente le contrat). Les « dashboards » = endpoints + doc du payload.

### Décisions v2.7

| # | Décision | Raison |
|---|---|---|
| D14 | **Une** classe `OpenAICompatibleProvider` + table `_PROVIDER_DEFAULTS` (7 providers, +mistral +groq) ; zéro sous-classe par provider | la mission : provider = config, transport = OpenAI-compat, pas N implémentations |
| D15 | `GET /api/dashboard` — un endpoint agrégé, `user_dashboard_service`, strictement `current_user.id` ; jamais de `user_id` client | isolation stricte ; éviter 5 endpoints là où 1 suffit |
| D16 | Réutiliser `StatsService._WINDOWS` + les patrons `GROUP BY` existants pour le per-user ; aucune nouvelle table | mesure réelle > métrique inventée ; pas de duplication |
| D17 | mistral/groq = entrées de config, pas de code ; `_sanitize` élargi (`gsk_`, clés Mistral) | anti-redondance + sécurité |

### Phase 66 — LLM unifié : **une** implémentation ✅ (D14)

`services/llm/providers.py` réécrit. Avant : 5 classes (`NvidiaProvider`,
`OpenAIProvider`, `GeminiProvider`, `OpenAICompatibleProvider`,
`CustomEndpointProvider`) sous-classant `OpenAILikeProvider`. Après : **une**
classe `OpenAICompatibleProvider(*, name, base_url, api_key, models,
temperature, max_tokens, timeout)`. Un provider = une entrée de la table
`config._LLM_BLOCKS` (7 : nvidia, openai, gemini, mistral, groq,
openai_compatible, custom) = `(api_key_attr, model_attr, base_url_attr,
default_base_url, default_model)`.

- `provider_config(name) -> dict` résout `settings` → kwargs. Le bloc
  spécifique (`MISTRAL_API_KEY`…) l'emporte sur le générique (`LLM_API_KEY`…),
  qui l'emporte sur le défaut de la table. Nom inconnu → `nvidia` + warning.
- `build_active_provider()` = `OpenAICompatibleProvider(**provider_config(settings.LLM_PROVIDER))`.
  `build_provider(name)` idem pour un nom donné.
- `gemini_client.py` inchangé (importe `build_active_provider`, `LLMError`,
  `BaseLLMProvider`). Les 11 modules métier : **zéro changement**, toujours
  `call_gemini(...)`.
- `services/llm/base.py::_sanitize` élargi : `sk-` / `nvapi-` / `AIza` / `gsk_`,
  jetons ≥ 32 hex, valeurs étiquetées `Authorization:` / `api_key=` / `bearer …`,
  URLs. Tronqué à 300.
- `config.LLM_BASE_URL` : défaut `""` (était l'URL NVIDIA) — sinon tout provider
  non-nvidia héritait de l'URL NVIDIA. `""` → défaut du provider actif.
- Ajout `MISTRAL_*` / `GROQ_*` (key/model/base_url) dans `Settings`.

Preuve anti-redondance (test) : `test_there_is_exactly_one_provider_implementation`
assère `impls == [OpenAICompatibleProvider]` ; `test_every_backend_is_the_same_class`
parcourt les 7 noms.

### Phase 67 — config runtime mistral/groq ✅

`services/runtime_config_service.py` : `_ALLOWED` += `MISTRAL_MODEL/BASE_URL`,
`GROQ_MODEL/BASE_URL` ; `_PROVIDERS = config.LLM_PROVIDER_NAMES` (7).
`PATCH /api/admin/llm` accepte donc `provider=mistral|groq`, valide → applique
sur `settings` → healthcheck → rollback + 400 si échec. **Aucune clé** dans le
corps ni la réponse (`LlmConfigUpdate` / `LlmConfigView` n'ont pas de champ
`api_key`).

### Phases 68 + 69 — dashboard par utilisateur ✅ (D15)

`services/user_dashboard_service.py` — `UserDashboardService(db, user).build()`,
**un seul** endpoint `GET /api/dashboard` (`profile_router.dashboard_router`).
Toute requête : `where(<Model>.user_id == self._uid)` où `_uid = current_user.id`.
Aucun paramètre `user_id` sur la route → un client ne peut pas viser un autre
compte (test `test_no_user_id_query_param_is_honoured`).

Sections (agrégats SQL, `_WINDOWS = {today, week, month, year}`) :
`activity` (member_since, last_active_at, sessions = COUNT `login` par fenêtre),
`jobs` (searches / offers_viewed = COUNT events, offers_saved = COUNT SavedJob,
providers_engaged = COUNT DISTINCT source), `applications` (total, by_status
GROUP BY, by_window, duplicates_avoided, failed), `auto_apply` (prepared / cv /
letter counts, avg_match_score, `note` « ne soumet jamais »), `documents`
(cv/letter totaux + utilisés + created_by_window), `matching` (avg/best score,
good_matches ≥ 0.6, top_missing_skills — Counter borné aux 500 dernières
candidatures **de cet utilisateur**), `providers` (GROUP BY source).

`schemas/dashboard.py` : `UserDashboardResponse` + 9 sous-modèles.

### Phase 70 — dashboard superadmin : activité job par période ✅

`StatsService.jobs_activity()` : un `GROUP BY kind` avec `COUNT(CASE …)` par
fenêtre sur `usage_events` (kinds `search` / `job_view` / `job_saved`) +
`COUNT(DISTINCT CASE …)` pour les utilisateurs distincts. Nouvelle route
`GET /api/admin/stats/jobs` + section `jobs` dans `GET /api/admin/dashboard`
(`JobsActivityStats`). Fenêtre sans événement = 0, jamais estimé.
Les breakdowns par période déjà présents (v2.5) restent : `usage`
(active/new users by window), `applications.by_window`, `documents.created_by_window`,
`boards` (total/enabled/disabled/last_test_ok par provider).

### Phase 72 — provider stats : deux métriques dérivées ✅

`AdminService.provider_stats()` += `offers_per_run` (`total_offers / ok`) et
`empty_result_rate` (`(ok - ok_nonempty) / ok`) — **dérivées** des compteurs
`ProviderMetrics` existants, aucun nouvel état enregistré. `timeout_rate` **non
ajouté** : impossible sans un compteur par type d'erreur (la mission dit
« si dérivable sans multiplier les métriques » — ça ne l'est pas). `health_note`
couvre déjà le cas « 0 résultat sur N runs malgré un passé prouvé ».

### Phase 71 — job search / liens / auto-candidature / extraction : audit, rien à changer ✅

Fan-out partagé + deadline, validation des liens (`_usable_offer_url`), SSRF
(`http.py`), statuts d'auto-candidature (jamais `submitted`), extraction de
préférences (1 `call_gemini`, fusion `UserProfile` + message + repli) — tous
solides depuis v2.6. Aucune régression, aucune modification nécessaire.

### Phase 73 — performance (mesurée)

SQLite mémoire, mix réaliste par utilisateur :

| n_users | candidatures | événements | `GET /api/dashboard` | dashboard superadmin |
|---:|---:|---:|---:|---:|
| 100 | 743 | 1 601 | 38 ms / 35 req | 40 ms / 32 req |
| 1 000 | 7 370 | 16 182 | 35 ms / 35 req | 129 ms / 32 req |
| 5 000 | 37 238 | 80 124 | **43 ms** / 35 req | 617 ms / 32 req |

Dashboard utilisateur : **plat** (owner-scoped, filtres `user_id` indexés,
nombre de requêtes constant → pas de N+1). Dashboard superadmin : croît avec le
volume global (agrégats plein-table, attendu), mis en cache 60 s. Aucun nouvel
index nécessaire (`ix_usage_events_*`, `job_applications(user_id/status/created_at)`,
`generated_cvs(user_id)`, `saved_jobs(user_id)` suffisent).

### Phase 74 — sécurité

- Isolation dashboard : tests `test_user_a_never_sees_user_b_data`,
  `test_no_user_id_query_param_is_honoured`, `test_unauthenticated_401`.
- `is_superadmin` : dans aucun schéma de requête utilisateur (vérifié signup /
  profile / job context / user update).
- Clé LLM mistral/groq : `GET /api/admin/llm` + `/api/admin/dashboard` ne
  contiennent aucune clé (smoke conteneur, provider `groq`).
- `_sanitize` : + `gsk_`, jetons hex ≥ 32, valeurs étiquetées.

### Phase 75 — tests

`tests/test_user_dashboard.py` (+7), `tests/test_admin_stats.py` (+4 :
`jobs_activity`, route superadmin-only, section dashboard, champs provider
dérivés), `tests/test_llm_providers.py` (réécrit Phase 66 : 1 classe, 7
backends, mistral/groq, `_sanitize`). **Total : 1010 passed, 1 skipped**
(mono-processus).

### Phase 76 — documentation

`README.md` (LLM 7 providers « une implémentation », `GET /api/dashboard`),
`PROJECT_STRUCTURE.md` (`user_dashboard_service`, `schemas/dashboard.py`,
`llm/` réécrit, 3 règles de conception), `.env.example` (`MISTRAL_*` / `GROQ_*`,
sémantique `LLM_BASE_URL` vide), `API_FRONTEND_GUIDE.tex` + PDF recompilé
(section `GET /api/dashboard`, `/api/admin/stats/jobs`, `available_providers`
= 7, `offers_per_run` / `empty_result_rate`).

### Phase 77 — validation de production

`python -m compileall .` OK · `pytest -q` 1010 passed / 1 skipped ·
`docker build --build-arg INSTALL_BROWSER=false -t cv-assistant:v27 .` OK
(586 MB) · smoke conteneur : `/api/health` OK, `/api/dashboard` user normal
`200`, `/api/admin/dashboard` user normal `403`, `/api/admin/stats/jobs`
superadmin `200`, `/api/admin/llm` provider=groq sans clé, bootstrap
`SUPERADMIN_EMAILS` OK, aucun secret dans les logs/réponses.

### Phase 78 — rapport final

`RAPPORT_PHASES_65_78.md` — 16 sections + section obligatoire
« Architecture anti-redondance » (démonstration : les 7 backends partagent
`OpenAICompatibleProvider`, prouvé par
`test_there_is_exactly_one_provider_implementation`).

### v2.7 — non touché

Contrat d'extraction / génération · `JobSearchService` (cœur) ·
`JobSearchContext` · `JobOffer` · dedup / freshness / ranking · `http.py` ·
SSRF · circuit breaker · providers de jobs · navigateur (off par défaut) ·
`gemini_client` (façade) · les 11 modules consommateurs LLM · auth / JWT /
reset · MinIO.

## 16. — v2.8 : Redis + dashboard, mémoire conversationnelle, e-mail unique

> Exécution autonome, sans validation intermédiaire. Base de départ : v2.7,
> 1010 tests.

### 16.1 — Cache Redis du dashboard

- `services/cache_service.py::CacheService.get/set/delete/clear` catchent
  désormais les exceptions du backend **à l'appel**, pas seulement à la
  connexion (`__init__` le faisait déjà). Un Redis qui tombe en cours de vie
  du process dégrade proprement en « pas de cache », jamais une erreur — pour
  **tous** les appelants existants (pipeline de génération, dashboards,
  historique de conversation).
- `services/user_dashboard_service.py::UserDashboardService.build()` est
  cache-wrapped : clé `cache.key("dashboard","user",<user_id>)` — **jamais**
  un identifiant fourni par le client — TTL `settings.DASHBOARD_CACHE_TTL`
  (défaut 60 s, configurable). `invalidate(user_id)` (best-effort) est appelée
  depuis chaque écriture qui change les chiffres du dashboard d'un
  utilisateur : `apply()` (candidature + doublon), `save_job`/`unsave_job`,
  création/suppression de CV et de lettre. Les événements fins et fréquents
  (recherche, vue d'offre) ne déclenchent **pas** d'invalidation — ils
  attendent le TTL, sinon le cache ne servirait jamais pour un utilisateur
  actif.
- `GET /api/admin/dashboard` réutilise **le même** réglage
  `DASHBOARD_CACHE_TTL` (au lieu d'un `60` en dur) — pas de deuxième système
  de cache.
- Tests : `tests/test_cache.py` (résilience générique du backend),
  `tests/test_dashboard_cache.py` (HIT/MISS, isolation par utilisateur,
  invalidation sur `apply`/`save_job`/`unsave_job` via de vrais appels de
  service — pas des écritures DB directes —, panne du backend, absence de
  clé LLM dans le payload caché).

### 16.2 — Mémoire conversationnelle persistante

- **Modèles** (`models/conversation.py`) : `Conversation` (`user_id`,
  `title`, `created_at`, `updated_at`) et `Message` (`conversation_id`,
  `user_id` dénormalisé — jamais utilisé seul pour l'autorisation, toujours
  revérifié via `Conversation.user_id` —, `role` ∈ {user, assistant},
  `content`, `created_at`). Index composites `(user_id, updated_at)` et
  `(conversation_id, created_at)`.
- **Service** (`services/conversation_service.py`) : `ConversationService` —
  `create` / `list` / `get_owned` (404 si absent, 403 si un autre
  utilisateur) / `delete` (cascade manuelle des messages) / `list_messages`
  (paginé, ordre chronologique) / `send_message`.
- **Routes** (`conversations_router.py`, `/api/conversations/*`, toutes
  `Depends(get_current_user)`) : `POST`/`GET ""`, `GET`/`DELETE "/{id}"`,
  `GET`/`POST "/{id}/messages"`.
- **L'agent contextualisé** (`send_message`) : (1) persiste le message de
  l'utilisateur ; (2) construit le prompt à partir des
  `CONVERSATION_HISTORY_LIMIT` derniers messages (20 par défaut), dans
  l'ordre chronologique, jamais tout l'historique ; (3) **un seul**
  `call_gemini(prompt, request_type="conversation_agent")` — aucun nouveau
  provider, `services/llm/providers.OpenAICompatibleProvider` reste l'unique
  implémentation ; (4) persiste la réponse. Si la génération échoue
  (`RuntimeError` → 429 via `llm_error_response`, ou LLM non configuré → 503
  via `require_llm()` en amont), le message de l'utilisateur reste
  **déjà enregistré** — rien n'est perdu.
- **Cache de l'historique récent** : `_recent_history()` lit/écrit via
  `cache_service` (clé par `conversation_id`), invalidée à chaque message
  ajouté. La base reste la source de vérité ; une panne du cache retombe
  simplement sur la requête SQL.
- Tests : `tests/test_conversations.py` (CRUD, pagination, isolation A/B,
  401/403/404), `tests/test_conversation_agent.py` (ordre chronologique de
  l'historique envoyé au LLM, respect de la limite configurable, message
  utilisateur conservé même si la génération échoue, aucun nouveau provider,
  cache historique HIT/invalidation/panne, aucune fuite de clé LLM dans une
  conversation).

### 16.3 — E-mail unique

- `models.User.email` était déjà `unique=True` — le vrai point manquant
  était la **gestion** de la contrainte, pas son absence : `AuthService.
  signup()` ne catchait pas `IntegrityError`, donc une course entre deux
  inscriptions au même e-mail (les deux passent le `SELECT` avant que l'une
  des deux ne fasse `COMMIT`) remontait en `500`. Corrigé : `signup()` catche
  `IntegrityError`, fait un `rollback()`, et lève `AuthError` → `409` (jamais
  `500`).
- `_normalize_email()` (trim + minuscules) devient le point unique de
  normalisation, appliqué à `signup`, `authenticate`, `request_reset_code`,
  `reset_password_with_code` (déjà le cas avant, factorisé maintenant) et à
  `settings.superadmin_emails_list` (déjà normalisé, vérifié par test).
- **Aucun endpoint de modification d'e-mail n'existe** dans l'API (vérifié :
  ni `AdminUserUpdate` ni `UserProfileIn` n'ont de champ `email`) — la règle
  « appliquer la même normalisation à la modification d'e-mail » est donc
  vérifiée par construction ; un test documente ce contrat pour qu'il soit
  visible le jour où un tel endpoint serait ajouté.
- `database.py::_ensure_unique_email()` (nouveau, appelé dans `init_db()`) :
  vérifie défensivement, au démarrage, qu'un index/contrainte UNIQUE existe
  sur `users.email` et le crée sinon (`CREATE UNIQUE INDEX IF NOT EXISTS`),
  idempotent, jamais fatal (un déploiement avec des doublons déjà présents
  loggue un avertissement plutôt que de planter le démarrage).
- Tests : `tests/test_email_unique.py` — normalisation (casse/espaces),
  stockage normalisé, connexion insensible à la casse, doublon → 409 (même
  casse et casse différente), **course simulée** → 409 jamais 500, la base
  elle-même rejette un doublon direct (`IntegrityError`), `SUPERADMIN_EMAILS`
  normalisé, bootstrap superadmin insensible à la casse.

### 16.4 — Sécurité / non-régression (vérifiée)

Isolation `current_user.id` partout (dashboard, conversations) ; aucun
`user_id` client accepté (`schemas/conversation.py`, `schemas/dashboard.py`) ;
aucune clé API LLM dans une réponse, un log, une entrée Redis ou un message de
conversation (tests dédiés) ; aucun `is_superadmin` dans un schéma utilisateur
normal (inchangé) ; **une seule** implémentation `OpenAICompatibleProvider`
(inchangée, re-testée depuis le nouveau module) ; aucune soumission
automatique de candidature (inchangé) ; SSRF / ATS / circuit breaker /
sécurité des documents non touchés par ce cycle.

### 16.5 — Validation

`python -m compileall .` OK · `PYTHONIOENCODING=utf-8 pytest -q`
(mono-processus) : **1059 passed, 1 skipped** (v2.7 : 1010 ; +50 tests
v2.8) · aucune régression sur les 1010 tests existants.

### 16.6 — Fichiers

**Nouveaux** : `models/conversation.py`, `schemas/conversation.py`,
`services/conversation_service.py`, `conversations_router.py`,
`tests/test_conversations.py`, `tests/test_conversation_agent.py`,
`tests/test_email_unique.py`, `tests/test_dashboard_cache.py`.

**Modifiés** : `config.py` (+`DASHBOARD_CACHE_TTL`,
`CONVERSATION_HISTORY_LIMIT`, `CONVERSATION_HISTORY_CACHE_TTL`),
`services/cache_service.py` (résilience à l'exécution),
`services/user_dashboard_service.py` (cache + `invalidate()`),
`services/job_application_service.py` (4 points d'invalidation),
`cv_router.py` (4 points d'invalidation), `admin_router.py` (TTL partagé),
`services/auth_service.py` (`IntegrityError` → 409, `_normalize_email`),
`database.py` (`_ensure_unique_email`), `models/__init__.py`,
`main.py` (routeur conversations), `tests/conftest.py` (+1 module LLM mocké,
+1 réponse par défaut `conversation_agent`), `tests/test_cache.py`
(résilience), `README.md`, `PROJECT_STRUCTURE.md`, `.env.example`,
`API_FRONTEND_GUIDE.tex`.

**Non touché** : tout v2.7 (LLM unifié, dashboard superadmin, job search,
auto-candidature, ATS boards) — additif uniquement, sauf la correction du
bug de gestion de course sur `signup()` (comportement observable identique
en dehors de la course : `409` sur doublon, inchangé).
