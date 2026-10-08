# Project structure

> This file reflects the **actual** layout of the repository as of the Phase 6
> finalisation (2026-09-13) — no `*_router.py` files, no flat `services/*.py`
> pipeline modules. The backend is organised by domain:
>
> - `api/` — one HTTP router module per domain.
> - `services/cv/` — résumé extraction + CV/letter generation + skill analysis + PDF.
> - `services/jobs/` — job search (sync + async), matching, applications, ATS boards.
> - `services/conversations/` — conversational memory, the edit agent, the job agent.
> - `services/documents/` — generated-document versioning.
> - `services/providers/` — one file per job-board platform.
> - `services/llm/` — the single OpenAI-compatible LLM provider + routing + circuit.
> - Cross-cutting infra stays flat directly under `services/` (`gemini_client`,
>   `generation_service`, `cache_service`, `minio_service`, `profiling`,
>   `parser_common`, `id_utils`, `recommendations`, plus the auth/admin/stats/
>   profile/usage-event/runtime-config/user-dashboard services).
>
> `models/`, `schemas/`, `database.py`, `config.py`, `main.py` stay at the root.
> `tests/test_architecture_imports.py` enforces this layout (every domain
> package imports cleanly, the flat-`services/` allow-list, every router wired
> into `main.py`, every removed endpoint stays removed).

```
cv-generator/
├── main.py                     FastAPI app — lifespan (config check + init_db + superadmin
│                                bootstrap + MinIO bucket + browser shutdown) · CORS · exception
│                                handlers · GET /api/health · include_router() for all 8 routers.
│                                Zero business logic.
├── config.py                   pydantic-settings `Settings` — the single config object (DB, JWT,
│                                reset codes, e-mail, MinIO, cache, LLM routing, job search/ATS
│                                tuning, agent pagination). DATABASE_URL / JWT_SECRET_KEY checked
│                                at startup, not import.
├── database.py                 SQLAlchemy 2.0 engine / SessionLocal / get_db / init_db
│                                (create_all + `_ensure_additive_columns` — idempotent ADD COLUMN /
│                                CREATE INDEX for model drift, still no Alembic). PostgreSQL prod,
│                                SQLite tests.
├── cv_models.py                Pydantic models for the GENERATION + LETTER contracts (CVProfile,
│                                GenerateCVRequest/LetterRequest, ATSAnalysis) + SupportedLanguage.
├── extraction_models.py        Pydantic models for the EXTRACTION contract (all-Optional
│                                ExtractedCVProfile, ExtractCVResponse, confidence/validation).
│
├── requirements.txt / requirements-dev.txt / pytest.ini / Dockerfile / docker-compose.yml
├── .env.example / .dockerignore / .gitignore
├── README.md · PROJECT_STRUCTURE.md (this file) · RAPPORT_PHASES_65_78.md · REFACTOR_REPORT.md
│
├── api/                         HTTP layer — one module per domain, all thin (auth/ownership +
│  │                              delegate to a service; no pipeline code).
│  ├── auth.py                   /api/auth/* — signup · signin · forgot/reset-password ·
│  │                              deactivate/reactivate · GET /me. Public except /me.
│  ├── extraction.py              POST /api/extract-cv (Bearer, multipart upload).
│  ├── cv.py                      /api/generate-cv · /api/generate-letter · /api/cvs · /api/letters
│  │                              (+ /versions, /versions/{n}/download, /download, DELETE).
│  ├── jobs.py                    /api/jobs/{id} · {id}/apply · applications(/{id}) ·
│  │                              {id}/save · saved. NO manual search route (Phase 6) — the agent
│  │                              is the only entry point into JobSearchService.
│  ├── conversations.py           /api/conversations (+ /{id}, /{id}/messages) — the agent's public
│  │                              surface. `POST /{id}/messages` is `async def` (Phase 6
│  │                              finalisation): it awaits `ConversationService.send_message`,
│  │                              which awaits the job agent's real async search fan-out.
│  ├── profile.py                 GET/PUT/DELETE /api/profile + `dashboard_router`:
│  │                              GET /api/dashboard · GET /api/dashboard/jobs.
│  └── admin.py                   /api/admin/* (require_superadmin) — users · ats-boards (+/test) ·
│                                 providers · stats/* · llm (GET/PATCH/test) · dashboard.
│
├── models/                       ORM models — every business id is `String(36)`,
│  │                              `default=lambda: str(uuid4())` (never an Integer/autoincrement
│  │                              PK — asserted in tests/test_business_ids_are_uuid.py).
│  ├── user.py                    User (+ is_superadmin, token_version, last_active_at).
│  ├── user_profile.py            UserProfile — 1:1, `user_id` IS the PK.
│  ├── generated_cv.py            GeneratedCV (+ reference/version/parent_id — LOT 8 versioning;
│  │                              `reference`, e.g. "CV_A8F42K", is a human document code, NOT a UUID).
│  ├── generated_letter.py        GeneratedLetter — mirrors GeneratedCV.
│  ├── password_reset.py          PasswordResetCode (SHA-256 hash, expiry, attempts).
│  ├── reactivation_code.py       ReactivationCode — mirrors PasswordResetCode.
│  ├── job_offer.py               JobOffer — normalised listing, cache/dedup/freshness index only.
│  ├── job_application.py         JobApplication (+ match_reasons/missing_skills/traceability;
│  │                              `status` has no "submitted" value, ever).
│  ├── saved_job.py               SavedJob — the single "jobs the user is considering" table
│  │                              (`origin` agent|manual, `match_score`, `conversation_id`).
│  ├── ats_board.py               AtsBoard — superadmin DB overlay on the file/env ATS board lists.
│  ├── usage_event.py             UsageEvent — privacy-safe activity log (counters/ids only).
│  ├── runtime_config.py          RuntimeConfig — admin-changeable LLM settings (never a key).
│  └── conversation.py            Conversation (+ `pending_action` — a frozen apply proposal;
│                                 `job_browse_state` — Phase 6 finalisation: the ordered
│                                 job-id list + pagination offset a "show me more" turn slices
│                                 deterministically) · Message (role/content/document_reference).
│
├── schemas/                      Pydantic request/response contracts.
│  ├── auth.py                    SignupRequest/SigninRequest/UserPublic (`id: UUID`) / reset+
│  │                              reactivation flows.
│  ├── profile.py                 UserProfileIn/Out (`user_id: UUID`).
│  ├── cv.py                      GenerateCV/LetterJsonResponse (`generated_cv_id`/
│  │                              `generated_letter_id: UUID`) · CVListItem/LetterListItem/
│  │                              DocumentVersionItem (`id: UUID`, `reference: str`).
│  ├── jobs.py                    JobSearchContext (+ `.merge()`) · NormalizedOffer ·
│  │                              JobSearchRequest/Response (internal only — the agent builds
│  │                              these, there is no public search endpoint) ·
│  │                              JobOfferOut (`id: str` — deliberately not `UUID`: reused as
│  │                              live working data by the agent, see the module docstring) ·
│  │                              ProviderState/ProviderStatus/FreshnessStatus enums.
│  ├── dashboard_jobs.py          SelectedJobOut / SelectedJobApplication / ApplyOutcome — all
│  │                              `str` ids on purpose (same reuse-as-working-data reason as
│  │                              JobOfferOut).
│  ├── applications.py            ApplyRequest/Response · ApplicationOut · SavedJobOut — `str`
│  │                              ids (same reason); `ApplicationStatus` has no "submitted".
│  ├── conversation.py            ConversationOut/MessageOut (`id: UUID`, terminal — never reused
│  │                              as working data) · MessageSendResponse · RecommendedAction ·
│  │                              PendingConfirmation (`job_ids: list[UUID]` — always
│  │                              already-resolved real ids by the time this is returned).
│  ├── conversation_directive.py  ConversationDirective — the ONE structured LLM call per turn.
│  │                              `intent` ∈ {chat, search_jobs, list_jobs, apply_jobs,
│  │                              request_information}. `list_jobs` (Phase 6 finalisation) never
│  │                              re-searches or re-ranks — the backend deterministically slices
│  │                              the already-found, already-ordered list. `recommendations`
│  │                              (Phase 7) rides the SAME call — 0-10 raw
│  │                              `IntelligentRecommendation` candidates, validated/ranked/capped
│  │                              to 3 by `recommendation_engine.py` before ever reaching a response.
│  ├── recommendation.py          (Phase 7) `IntelligentRecommendation` — `action` is a free
│  │                              string (never a fixed enum), so a suggestion can concern the CV,
│  │                              a cover letter, the job search, a specific offer, the user's
│  │                              profile, an application, or the conversation itself. A proposal
│  │                              only — nothing anywhere dispatches on `action` to execute it.
│  ├── admin.py                   Admin user/board/stat rows (`id: UUID`) · LlmConfigView/Update
│  │                              (no api_key field, ever).
│  ├── dashboard.py                UserDashboardResponse (`user_id: UUID`).
│  ├── skill_analysis.py          SkillAnalysis · RecommendedAction (CV-generation side).
│  ├── agent_schemas.py           The edit agent's structured-action contract (document edits).
│  └── llm_schemas.py             Shared structured-output helpers for the LLM-JSON contracts.
│
├── dependencies/auth.py           get_current_user (HTTPBearer → JWT → User or 401, `tv`
│                                  token-version check) · require_superadmin (401→403→User).
│
├── services/
│  ├── gemini_client.py            SOLE LLM entrypoint (`call_gemini`) — SHA-256 response cache,
│  │                               rate limit, model-fallback chain, call stats, profiling hooks.
│  ├── llm/                        base.py (provider interface + fallback chain + healthcheck) ·
│  │                               providers.py — ONE `OpenAICompatibleProvider` class for all 7
│  │                               backends · routing.py (per-request_type provider/model table) ·
│  │                               circuit.py (per-(provider,model) breaker). No business module
│  │                               imports this package directly — only `gemini_client` does.
│  ├── profiling.py                Opt-in latency + LLM-call profiling (durations/tokens/model
│  │                               names/flags only — never content).
│  ├── generation_service.py       Cache-wrapped run_extraction / run_cv_pipeline /
│  │                               run_letter_pipeline (+ async variants) · require_llm/
│  │                               require_minio / llm_error_response guards.
│  ├── cache_service.py            MemoryCache / RedisCache / CacheService — the one place that
│  │                               imports `redis`; every call is exception-safe (a dead backend
│  │                               degrades to "no cache", never an error).
│  ├── minio_service.py            MinIO wrapper (upload/presigned_get_url/download/delete).
│  ├── id_utils.py                 `is_valid_uuid` / `require_uuid_or_404` (Phase 6 finalisation)
│  │                               — the one shared UUID-format guard, used at the service-layer
│  │                               ownership chokepoints (conversations, applications, jobs, admin
│  │                               users/boards) so a malformed id is rejected before ever
│  │                               reaching the database, while preserving the existing 404 (never
│  │                               a behaviour-changing 422).
│  ├── recommendations.py          Deterministic, closed-vocabulary `RecommendedAction` builder.
│  │                               Since Phase 7, used ONLY by `/api/generate-cv`'s post-generation
│  │                               suggestions (a stateless endpoint outside any conversation, so
│  │                               it keeps a cheap rule engine on purpose — see recommendation_engine.py's
│  │                               docstring for why). No longer called by the conversational agent.
│  ├── auth_service.py             bcrypt · JWT mint/decode (`sub` = the user's UUID `id`) ·
│  │                               password reset / deactivate / reactivate flows.
│  ├── admin_service.py            Superadmin ops — users, ATS boards (+ live /test), provider
│  │                               stats, superadmin bootstrap.
│  ├── stats_service.py            Superadmin analytics — pure SQL aggregates.
│  ├── usage_event_service.py      Privacy-safe activity log writer + retention prune.
│  ├── runtime_config_service.py   Persisted admin LLM overrides — validate + healthcheck +
│  │                               auto-rollback.
│  ├── user_profile_service.py     Owner-scoped UserProfile CRUD + `to_search_context()`.
│  ├── user_dashboard_service.py   The per-user `GET /api/dashboard` (+ `selected_jobs()` behind
│  │                               `GET /api/dashboard/jobs`) — every query `where(user_id=…)`.
│  ├── email_service.py            Reset/reactivation e-mail delivery (Resend → SMTP → dev-log).
│  │
│  ├── cv/                         Extraction pipeline (local-first) + generation + cover letter.
│  │   ├── resume_text_extractor.py · pdf_layout_reader.py · column_detector.py · link_extractor.py
│  │   ├── text_cleaner.py · section_splitter.py · contact_extractor.py · date_parser.py
│  │   ├── local_section_extractor.py · parser_common's cv-facing helpers via services/parser_common.py
│  │   ├── experience_parser.py · education_parser.py · project_parser.py · skills_parser.py
│  │   ├── resume_structurer.py · source_grounding.py · resume_validator.py · confidence_scorer.py
│  │   ├── resume_parser_pipeline.py    orchestrates the whole extraction pipeline.
│  │   ├── profile_analyzer.py · ats_optimizer.py · cv_generator.py · pdf_generator.py
│  │   ├── letter_generator.py · letter_pdf_generator.py
│  │   ├── skill_analysis.py · prompt_kit.py
│  │
│  ├── jobs/                       Job search + matching + applications (agent-driven only).
│  │   ├── search_service.py       JobSearchService — cache → pick providers → fan out → dedup →
│  │   │                           upsert+freshness → rank → paginate. `search()` (sync,
│  │   │                           unchanged, used by every existing sync caller/test) and
│  │   │                           `search_async()` (Phase 6 finalisation) share one deterministic
│  │   │                           tail (`_finish`); only the fan-out differs: `_fan_out`
│  │   │                           (`concurrent.futures`) vs `_fan_out_async` (`asyncio.gather`-
│  │   │                           style over `loop.run_in_executor` on the SAME shared,
│  │   │                           process-wide thread pool — no provider becomes async, no new
│  │   │                           HTTP client, SQLAlchemy stays synchronous).
│  │   ├── preference_service.py   JobPreferenceExtractor — LLM-free `enrich()` the agent calls
│  │   │                           after its one directive call, plus the LLM-backed `extract()`.
│  │   ├── application_service.py apply() (never "submitted") + application history + saved jobs.
│  │   ├── match_service.py        Deterministic offer↔profile fit (score + reasons + missing).
│  │   ├── dedup.py · freshness.py · ranking.py    LLM-free, unit-tested.
│  │   └── ats_board_registry.py   DB overlay ∪ file/env board lists, cached.
│  │
│  ├── conversations/               Conversational memory + the two agents.
│  │   ├── conversation_service.py ConversationService — CRUD, strictly owner-scoped
│  │   │                           (`get_owned` re-checks `user_id`, format-validates the id via
│  │   │                           `id_utils.require_uuid_or_404`). `send_message()` is `async def`
│  │   │                           (Phase 6 finalisation) — persists the user's message, dispatches
│  │   │                           to the EditAgent (sync) or awaits the JobConversationAgent,
│  │   │                           persists the reply.
│  │   ├── agent_service.py        EditAgent — deterministic CV_/LETTER_ document edits.
│  │   └── job_agent_service.py    JobConversationAgent — ONE call_gemini per turn
│  │                               (ConversationDirective), then deterministic Python:
│  │                               search_jobs → `await JobSearchService.search_async()`, persists
│  │                               the ranked order + a 5-per-page browse cursor on
│  │                               `Conversation.job_browse_state`; list_jobs → slices that cursor,
│  │                               never re-searches/re-ranks; apply_jobs → PROPOSE only, freezes
│  │                               the resolved real ids (never a display ordinal) on
│  │                               `pending_action`, and appends a deterministic, language-matched
│  │                               "reply oui/non" or "reply yes/no" instruction; a confirmation is
│  │                               recognised by `_is_confirmation()` (closed FR/EN vocabulary,
│  │                               short message, no caveat word) — 100% Python, the LLM is never
│  │                               consulted on that turn. `_build_prompt` (Phase 7) also embeds a
│  │                               compact CV/letter/application status block (never the raw
│  │                               documents) and asks for 0-3 `recommendations` in the same call;
│  │                               `handle()` passes the LLM's raw candidates through
│  │                               `recommendation_engine.validate_and_rank` before returning them.
│  │   └── recommendation_engine.py  (Phase 7/8) ContextBuilder (`build_context`/
│  │                               `render_context_block` — a handful of COUNT/aggregate queries,
│  │                               never a document dump) + the validator/ranker
│  │                               (`validate_and_rank`): drops a recommendation whose target isn't
│  │                               owned by the caller, whose `action` names a destructive/system
│  │                               verb, or that contradicts known state (a letter that already
│  │                               exists, an application already made); drops one whose own
│  │                               title/message text is confidently in the wrong language for the
│  │                               conversation (Phase 8, `language=` kwarg); collapses two
│  │                               recommendations that concern the SAME document (cv/letter) for
│  │                               the SAME target into the higher-ranked one (Phase 8, doc-category
│  │                               dedup — "generate_cover_letter" + "review_cover_letter" for one
│  │                               job in one turn is a contradiction/doublon, not two suggestions);
│  │                               ranks by priority then confidence; caps to 3; also downgrades a
│  │                               confident claim about a skill absent from the stored profile
│  │                               into a question instead of trusting it (Phase 8 anti-hallucination
│  │                               fix, `_downgrade_unconfirmed_skill_claim`). Pure Python — no
│  │                               `call_gemini`, no execution path for `action` anywhere.
│  │                               `job_agent_service.py` additionally backfills a generic
│  │                               job-focused suggestion with the real top search result right
│  │                               after a search runs (`_backfill_top_job_target`) and lets an
│  │                               EXPLICIT job number in the user's own message
│  │                               (`_extract_explicit_job_ordinal`, accent-insensitive) override
│  │                               whatever target the LLM guessed — see benchmarks/PHASE_6.md §O
│  │                               for the residual, narrower limitation that remains (an implicit
│  │                               reference with no number, on the exact turn a search/apply runs).
│  │
│  ├── documents/document_service.py   Versioned CV/letter resolve/list/get (`reference` ↔ row id).
│  │
│  └── providers/                  ONE FILE PER PLATFORM (20 providers) — base.py (ABC,
│      │                           never-raises `search()`, ProviderState mapping) · browser.py /
│      │                           browser_base.py (single locked headless session, off by
│      │                           default) · ats_common.py (shared multi-board ATS base) ·
│      │                           http.py (the sole outbound-HTTP chokepoint — SSRF guard, size/
│      │                           redirect caps, no anti-bot evasion) · circuit.py · metrics.py ·
│      │                           registry.py · parsing.py, plus one `*_provider.py` per platform
│      │                           (linkedin, indeed, arbeitnow, weworkremotely, hackernews,
│      │                           remotive, jobicy, remoteok, himalayas, adzuna, greenhouse,
│      │                           lever, ashby, workday, oracle_hcm, smartrecruiters, rippling,
│      │                           phenom, talentbrew, career_pages).
│
└── tests/                          pytest suite — LLM mocked, DB on SQLite, MinIO faked.
    ├── conftest.py                 mock_llm · db · fake_minio · clear_cache · test_user ·
    │                               auth_client/anon_client/client · swap_providers.
    ├── _jobs_helpers.py             FakeJobProvider + make_offer() shared by the job-search suite.
    ├── test_architecture_imports.py Domain-package import hygiene + router wiring + removed-
    │                               endpoint guard.
    ├── test_business_ids_are_uuid.py  (Phase 6 finalisation) every model id/FK, the JWT `sub`,
    │                               and the HTTP-facing ids are real UUIDs; a display ordinal is
    │                               never mistaken for one; `id_utils` format-guard unit tests +
    │                               a malformed path id still 404s, never 422.
    ├── test_job_agent.py            search/apply/confirm/degrade turns of the job agent.
    ├── test_intelligent_recommendations.py  (Phase 7) ContextBuilder is compact (a raw document
    │                               never leaks into the prompt) and reflects real state;
    │                               `action` accepts any topic (CV/letter/search/job/profile/
    │                               application/conversation), never a closed enum; ownership +
    │                               forbidden-verb + state-contradiction rejection; a
    │                               recommendation never executes (no dispatcher exists); the
    │                               Phase 6 confirmation flow is unaffected; FR/EN; 0-3 cap,
    │                               ranking, dedup; a malformed LLM payload degrades, never crashes.
    ├── test_phase8_recommendation_quality.py  (Phase 8 audit) full topic coverage matrix
    │                               (CV/letter/job search/matching/applications/profile/career
    │                               change) with real integration through the search → selection →
    │                               apply → confirmation workflows; a real FR/EN topic × language
    │                               matrix; language-mismatch rejection; intra-batch contradiction
    │                               collapse (generate vs. review the same document); anti-
    │                               hallucination boundary (a claimed fact in recommendation text
    │                               never becomes a stored fact); a real-LLM smoke test that skips
    │                               with an explicit reason rather than faking a result when no
    │                               provider is reachable.
    ├── test_job_pagination_and_confirmation.py  (Phase 6 finalisation) explicit FR "oui/non" /
    │                               EN "yes/no" wording + the confirmation matrix (incl. "oui
    │                               mais…" / "yes but…" never confirming); 15-offer pagination in
    │                               5/5/5 batches, no dup/no skip/stable order/no re-search; a
    │                               displayed ordinal resolving to the real UUID before it's frozen.
    ├── test_job_search_async.py     (Phase 6 finalisation) `search_async` parity with `search`,
    │                               a measured concurrency win over N artificially-delayed fake
    │                               providers, per-provider fault isolation, the shared deadline,
    │                               and a guard that no new LLM provider / httpx import was added.
    ├── test_job_search_service.py · test_job_link_integrity.py · test_job_dedup.py ·
    │   test_job_freshness.py · test_job_ranking.py · test_job_match_service.py ·
    │   test_job_apply_matching.py · test_job_applications.py · test_jobs_api.py ·
    │   test_jobs_schemas.py · test_jobs_ats_integration.py · test_ats_*.py ·
    │   test_provider_*.py (one per platform) · test_providers_http.py · test_auto_apply.py
    ├── test_conversations.py · test_conversation_agent.py · test_agent_editing.py ·
    │   test_document_versioning.py
    ├── test_dashboard_jobs.py · test_user_dashboard.py · test_dashboard_cache.py ·
    │   test_recommendations.py
    ├── test_auth.py · test_account_lifecycle.py · test_email_unique.py · test_user_profile.py
    ├── test_admin_*.py · test_llm_*.py · test_security_audit_v25.py · test_security_v26.py
    ├── test_cvs.py · test_letter_*.py · test_generation_*.py · test_pdf_one_page.py ·
    │   test_pipeline_integration.py · test_structured_output.py · test_prompt_quality.py
    ├── (extraction pipeline) test_parsers.py · test_skills_*.py · test_section_splitter.py ·
    │   test_contact_extractor.py · test_date_parser.py · test_local_section_extractor.py ·
    │   test_link_extractor.py · test_project_links.py · test_column_detector.py ·
    │   test_layout_pipeline.py · test_text_cleaner.py · test_extraction_*.py ·
    │   test_file_extraction.py · test_live_extraction.py
    ├── test_cache.py · test_minio.py · test_profiling.py · test_usage_events.py
    ├── test_api_integration.py · test_e2e_workflow.py
    └── test_validator_and_scorer.py · test_additional_skills.py · test_skill_analysis.py
```

---

## Design rules enforced across the codebase (current, non-exhaustive)

| Rule | Where it lives |
|---|---|
| One LLM client, ever | `services/gemini_client.py` (leaf); every other module imports `call_gemini` from it |
| 7 LLM backends, ONE implementation | `services/llm/providers.py` — a single `OpenAICompatibleProvider` class; no business module imports `services/llm/` directly |
| Every business id is a UUID string (`String(36)`, `default=uuid4`) | every `models/*.py`; asserted end-to-end in `tests/test_business_ids_are_uuid.py` |
| A malformed id is rejected before hitting the DB, without changing the existing 404 contract | `services/id_utils.require_uuid_or_404`, used in `ConversationService.get_owned`, `JobApplicationService.{apply,get_application,save_job}`, `api/jobs.py::_offer_in_scope_or_404`, `AdminService.{get_user,get_board}` |
| A display ordinal ("offre 1") is never a real id | `schemas/conversation_directive.ApplyDirective.job_ids` (mixed ordinal/id by design, resolved before freezing) vs `schemas/conversation.PendingConfirmation.job_ids: list[UUID]` (always resolved) |
| The agent PROPOSES an apply; only a deterministic "yes" applies it — the LLM can never trigger one | `job_agent_service._propose_apply` (freezes `pending_action`) + `_is_confirmation()` (pure Python, no LLM call on that turn) |
| The apply confirmation instruction is explicit and in the user's own language, never left to the LLM to phrase, never mixed | `job_agent_service._CONFIRM_INSTRUCTION_FR/_EN` + `_detect_lang()` (local heuristic, no LLM) |
| A caveat ("oui mais…", "yes but…") is never a confirmation | `job_agent_service._CAVEAT_RE` |
| Conversational job listings are paginated 5 at a time, stable, never re-ranked, never re-searched just to page | `Conversation.job_browse_state` + `job_agent_service._search` / `_list_jobs` |
| A later search never changes what an already-frozen id means | `pending_action.job_ids` is resolved to real ids and frozen BEFORE any later `job_browse_state` overwrite |
| The job search the agent runs is real `asyncio`, not `async def` around blocking code | `services/jobs/search_service.py::search_async` / `_fan_out_async` — `loop.run_in_executor` on the shared thread pool, awaited concurrently; measured in `tests/test_job_search_async.py` |
| Recommendations are contextual and any-topic, never a fixed enum, never an extra LLM call | `schemas/recommendation.IntelligentRecommendation` (`action: str`) produced inside the SAME `ConversationDirective` call; `services/conversations/recommendation_engine.py` |
| A recommendation is a suggestion, never an execution — nothing dispatches on `action` | `recommendation_engine.py` (no `getattr`/`eval`/`exec` anywhere); acting on one requires a new user message through the normal intent → confirmation → service path |
| A recommendation can only target a resource the caller owns | `recommendation_engine._owns_target` — checked against `SavedJob`/`GeneratedCV`/`GeneratedLetter`/`JobApplication`, keyed by the caller's `user.id` |
| The recommendation context sent to the LLM is a compact summary, never the raw CV/letter/DB | `recommendation_engine.build_context` — a handful of COUNT/aggregate queries, one row's worth of detail per document type |
| A recommendation whose own text is in the wrong language for the conversation is dropped | `recommendation_engine._detect_text_lang` + `validate_and_rank(..., language=...)` (Phase 8) — never penalises a short label with too little signal |
| Two recommendations about the SAME document for the SAME target collapse to the better one, never shown as two contradictory suggestions | `recommendation_engine._doc_category` + the post-sort collapse pass in `validate_and_rank` (Phase 8) |
| No HTTP call from this backend to its own API | there is no `/api/jobs/search*`; the agent calls `JobSearchService` in Python, sync or async |
| An application is never auto-submitted / never `"submitted"` | `services/jobs/application_service.py`; `schemas.applications.ApplicationStatus` |
| A user never sees another user's conversation / job selection / application / document | `get_owned`, `_offer_in_scope_or_404`, `JobApplicationService.get_application`, `cv.py` ownership checks — all 403/404, never a silent cross-user read |
| Provider fan-out (sync or async) is bounded by ONE shared deadline, not one per provider | `_fan_out` / `_fan_out_async` — a still-running provider at the deadline is abandoned, breaker tripped |
| One platform = one provider file; no platform logic in the search service | `services/providers/*_provider.py`; `search_service.py` only talks to `registry` |
| All provider network I/O goes through one guarded fetcher | `services/providers/http.py` — no provider or service imports `httpx`/`requests` directly |
| `/api/admin/*` is superadmin-only | router-level `Depends(require_superadmin)` |
| No PDF written to disk; every generated document goes straight to MinIO | `pdf_generator.py` / `letter_pdf_generator.py` expose only `render_to_bytes()` |

See `README.md` for the full API reference, environment variables, and
per-workflow request/response examples; see `benchmarks/PHASE_6.md` for the
Phase 6 architecture rationale and its finalisation (UUID hardening,
confirmation wording, pagination, async search), `benchmarks/PHASE_7.md` for
the intelligent-recommendation engine as originally delivered, and
`benchmarks/PHASE_6.md` §O for the Phase 8 audit/fixes on top of it.
