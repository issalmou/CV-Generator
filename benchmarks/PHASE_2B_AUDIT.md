# Phase 2b — Audit d'implémentation (QUALITÉ > VITESSE)

État providers (re-ping après nouvelles clés `.env`) — voir §Providers en fin de document.

Règle de travail : chaque lot = analyse + implémentation + tests ciblés + pytest + compileall + résumé.

---

## LOT 1 — Structured output + validation + anti-hallucination (P1/P3/P5)

### Fichiers créés
- `schemas/llm_schemas.py` — pour chaque `request_type` JSON : **schéma JSON strict**
  (`additionalProperties:false`, toutes clés `required`, nullable = `["string","null"]`)
  + **validateur Pydantic lenient** (`extra="ignore"`, coercion). Registre
  `json_schema_for()` / `validator_for()`. Ne fabrique jamais : clé manquante → `None`/`[]`.
- `tests/test_structured_output.py` — 14 tests (voir §tests).

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `services/llm/base.py` | `complete()` / `_complete_one()` acceptent `response_format` + `temperature` par appel (override du défaut provider). |
| `services/llm/providers.py` | `_supported_response_format()` : downgrade selon capacité modèle (`json_schema`→`json_object`→rien) d'après les probes du bake-off ; retry unique **sans** `response_format` si l'API le rejette (jamais un downgrade silencieux de qualité — le caller valide). |
| `services/gemini_client.py` | `call_gemini(..., json_schema=None, temperature=None)` ; **clé de cache** = `hash(prompt + request_type + schema.name + temperature)` (2 sorties différentes ne partagent plus une entrée). |
| `services/parser_common.py` | `request_structured_json(prompt, request_type, validator, json_schema)` : 1 appel `temperature=0` + schéma strict → validation Pydantic → **1 seul retry de réparation** (prompt + erreur concrète + rappel « null, ne pas inventer ») → `StructuredOutputError(raw=…)`. `ANTI_HALLUCINATION_RULES` renforcé : interdiction métrique **dérivée/calculée**, clause COMPLÉTUDE (ne pas omettre une entrée réelle). |
| `services/experience_parser.py`, `education_parser.py`, `project_parser.py`, `skills_parser.py`, `resume_structurer.py` | passent par `request_structured_json` + schéma. Sur `StructuredOutputError` → **salvage lenient des mots du modèle** (`parse_list_response`) puis `[]` ; skills → fallback regex local (sûr, même texte). Aucune fabrication. |
| `services/profile_analyzer.py` (**P3**) | `analyze_profile(profile, *, language="en")` ; prompt : `language_directive`, interdiction métrique dérivée (ex. « 800ms→210ms » ≠ « ~74% »), interdiction années/séniorité inventées, séniorité `""` si non supportée ; structured output ; fallback local conservateur (données candidat only). |
| `services/ats_optimizer.py` (**P1**) | `_build_optimization_prompt` : suppression de « Integrate missing keywords into summary and bullets » + « MISSING KEYWORDS TO INTEGRATE ». Nouveau bloc : mots-clés absents = **interdits dans résumé/objectif/bullets** (« disqualifying fabrication »), autorisés **uniquement** dans `additional_skills` comme suggestions verbatim. Règles universelles : interdiction techno/outil/méthodo/métrique/%, chaque bullet traçable à un fait source, interdiction métrique dérivée. `extract_keywords` + `optimize_content` en structured output. **`_status`** ∈ `applied / local_fallback / skipped / cache` retourné (constraint #10) — plus de fallback silencieux (`StructuredOutputError` → `_local_optimize(status=local_fallback)` + `logger.error`). |
| `services/conversation_service.py` (**P5**) | `_SYSTEM_HINT` : règles anti-fabrication explicites (aucun fait candidat non fourni ; un mot-clé de JD n'est jamais une compétence ; pas de chiffre dérivé ; demander l'info plutôt que combler). |
| `services/letter_generator.py` | prompt durci : anti-fabrication absolu (chaque phrase traçable au CV JSON), JD = contexte de ciblage **pas** source de faits candidat, interdiction chiffre inventé/dérivé, + règles qualité (narratif vs liste, 180–280 mots, éviter le filler, nommer société+poste). |
| `services/job_company_parser.py` | structured output (`letter_job_company`) + repair ; champs nullable ; pas d'inférence société depuis un domaine email. Fallback `_EMPTY` conservé (bloc société manquant acceptable, 500 non). |
| `services/cv_generator.py` | prompt `skill_categorisation` : copie **caractère par caractère**, union sorties = entrée (moins doublons exacts), aucun renommage/traduction/split/merge. |
| `services/generation_service.py` | `_cached_profile_analysis` passe `language` à `analyze_profile`. |
| `tests/conftest.py` | `mock_llm` accepte `json_schema` / `temperature` (fallback si un test installe un routeur étroit). |
| `tests/test_profiling.py`, `test_llm_providers.py`, `test_llm_error_paths.py`, `test_parsers.py`, `test_letter_generation.py` | doubles de test alignés sur la nouvelle signature `complete()` / forme normalisée. |

### Décision d'architecture
- Le helper structuré vit dans `parser_common.py` (déjà « shared helpers for parsers »)
  et non dans un nouveau module — évite de multiplier les modules (constraint #22).
  Import de `call_gemini` **local à la fonction** → pas de cycle avec `gemini_client`.
- Salvage lenient après double échec = **pas** un fallback fabriqué : on ne garde que
  les mots réels du modèle, et c'est loggé explicitement (`logger.warning`).

### Tests
- `tests/test_structured_output.py` : happy path, retry unique (2 appels, `use_cache=False`
  sur la réparation), double échec → `StructuredOutputError.raw`, `repair=False`,
  variation clé de cache, downgrade `_supported_response_format`, P1 (prompt n'injecte
  plus les mots-clés / « disqualifying fabrication »), `_status` surfacé (`applied` vs
  `local_fallback`), P3 (directive langue + interdiction métrique + fallback conservateur),
  P5 (règles dans `_SYSTEM_HINT`), parser salvage sans fabrication, garbage → `[]`.
- **pytest complet : 1084 passed, 1 skipped** (était 1071 ; +13 nouveaux).
- **compileall : OK** (repo entier).

### Risques / points ouverts
- `experience[:4]` / `achievements[:3]` dans `ats_optimizer` **pas encore retirés** →
  traité au LOT 6 (comme demandé, contrainte #7/#21).
- Le downgrade `json_schema`→`json_object` repose sur une liste de modèles connus ;
  un modèle inconnu part en `json_object` (sûr, le caller valide + répare).
- Le vrai gain anti-hallucination des prompts P1/P3/P5 + lettre sera **mesuré au LOT 12**
  (bench AVANT/APRÈS sur le vrai pipeline).

---

## LOT 2 — additional_skills séparé (P2 / contrainte #6)

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `services/cv_generator.py` | `_build_skills` : **ne fusionne plus** `additional_skills` ni `keywords_to_highlight` dans les catégories du CV (supprime les buckets « Additional Skills » / « Key Skills » auto). `generate_cv` retourne `cv_data["additional_skills"]` = suggestions filtrées (`_suggested_skills` : retire tout ce que le candidat possède déjà, dédup, cap 5) + `cv_data["ats_optimization"]` = statut. |
| `services/generation_service.py` | `run_cv_pipeline` retourne `CVPipelineResult` (dataclass : `pdf_bytes, ats_score, ats_analysis, additional_skills, ats_status, cv_data`) au lieu du tuple à 3. `_cached_optimized_content` : sur cache HIT, `_status` → `"cache"`. |
| `cv_router.py` | `_generate_cv` déballe `CVPipelineResult` ; réponse porte `additional_skills` + `ats_optimization`. |
| `schemas/cv.py` | `GenerateCVJsonResponse` += `additional_skills: list[str]`, `ats_optimization: str` (`applied`/`local_fallback`/`skipped`/`cache`). |
| `services/pdf_generator.py` | (inchangé — ne lit que `cv_data["skills"]`, donc `additional_skills` **n'atteint jamais le PDF**, vérifié). |

### Garanties
- `additional_skills` = **calculées**, **retournées séparément**, **jamais dans le PDF**,
  **jamais dans `cv_data["skills"]`**, filtrées de tout ce que le candidat a déjà.
- Un seul consommateur du tuple existait (`cv_router:68`) — mis à jour. Aucun test ne
  déballait le tuple.

### Tests
- `tests/test_additional_skills.py` (3 tests) : pas de fusion dans `skills`, pas de
  bucket « Additional Skills », suggestion déjà possédée retirée, `CVPipelineResult`
  porte suggestions + statut, PDF valide.
- ciblés (`test_generation_pipeline`, `test_api_integration`, `test_cache`, `test_structured_output`) : **OK**.

---

## Providers — re-ping 2026-09-08 (nouvelles clés `.env`)

| Provider / modèle | État | Rôle |
|---|---|---|
| `groq / openai/gpt-oss-120b` | ✅ ~0.5s, json_schema strict | **primaire partout** |
| `gemini / gemini-3.5-flash-lite` | ✅ ~0.6s, json_schema strict | fallback structuré #1 |
| `gemini / gemini-flash-latest` | ✅ ~1.3s | fallback prose #1 |
| `gemini / gemini-3.5-flash` | ✅ ~0.9s | dispo |
| `gemini / gemini-3.1-pro-preview` | ❌ 429 quota | non utilisé |
| `groq / openai/gpt-oss-20b` | ✅ (bake-off) | fallback structuré #2 |
| `mistral / *` | ❌ 429 « Rate limit exceeded » (même avec nouvelle clé) | **`LLM_DISABLED_PROVIDERS=mistral`** — jamais auto, buildable à la demande |
| `nvidia / nemotron-3-super-120b-a12b` | ✅ maintenant (2.0s) mais baseline instable | **jamais fallback auto** (contrainte #5) ; dispo manuel/admin |

---

## LOT 3 — Routing multi-provider centralisé (contraintes #2 / #22)

### Fichiers créés
- `services/llm/routing.py` — `resolve(request_type) -> Route(steps, temperature, timeout)`.
  Fusionne `config.DEFAULT_LLM_ROUTING` + `LLM_ROUTING_OVERRIDES` (JSON env), retire les
  steps sans clé / dans `LLM_DISABLED_PROVIDERS`, cache par `request_type` (invalidé par
  `reset()`). Fallback ultime = `settings.LLM_PROVIDER` global. `as_public_dict()` pour l'admin (sans clés).
- `tests/test_llm_routing.py` — 10 tests.

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `config.py` | `DEFAULT_LLM_ROUTING` (table `request_type → [[provider,model],…]` + temp) ; `LLM_ROUTING_ENABLED` (défaut `True`), `LLM_ROUTING_OVERRIDES`, `LLM_DISABLED_PROVIDERS="mistral"`, `LLM_ROUTING_TIMEOUT_OVERRIDES` ; `provider_has_key()`, `llm_disabled_providers_list` ; `llm_public_config()` += `routing_enabled` + `routing`. |
| `services/llm/providers.py` | `build_for(provider, model, *, timeout)` — 1 client OpenAI par `(provider, model, timeout)`, cache + `reset_instances()`. **UNE seule classe** `OpenAICompatibleProvider` inchangée. |
| `services/llm/__init__.py` | exports `build_for`, `reset_instances`, `Route`, `RouteStep`, `resolve_route`. |
| `services/gemini_client.py` | `call_gemini` : routing ON (défaut) → `_run_route(request_type, …)` marche la chaîne provider/modèle ; routing OFF → chemin legacy `_active_provider()` (inchangé). `reset_client()` vide aussi routes + instances. Profiling enregistre le provider/modèle **gagnant** + `fallback_used`/`attempts`. |
| `schemas/admin.py` | `LlmConfigView` += `routing_enabled`, `routing`, `circuit`. `GET /api/admin/llm` montre la table effective. |
| `tests/conftest.py` | `LLM_ROUTING_ENABLED=false` par défaut dans les tests (le chemin routing est couvert explicitement par `test_llm_routing.py` / `test_llm_fallback.py` ; `mock_llm` patche `call_gemini` donc la plupart des tests ne touchent aucun des deux). Fixture autouse `_reset_llm_routing`. |

### Décision — pas de nouvel endpoint d'édition de routing
Contrainte #17 (« ne pas multiplier les endpoints »). Le knob de config runtime est
`LLM_ROUTING_OVERRIDES` (env / futur runtime_config), et `GET /api/admin/llm` expose la
table effective. Un `PATCH` dédié pourra être ajouté si besoin réel — non demandé.

### Les 11 consommateurs : **inchangés**
`call_gemini(prompt, request_type=…)` — signature intacte. Le routing est 100 % interne
à `gemini_client`.

### Tests — `tests/test_llm_routing.py` (10)
chaîne configurée, drop step sans clé, tout injouable → global, provider désactivé jamais
dans une chaîne, override fusionne, routing off → 1 provider, `as_public_dict` sans clé,
`call_gemini` bascule sur le fallback (+ profiling), tous échouent → `RuntimeError`,
`build_for` cache 1 client/(provider,model).

---

## LOT 4 — Fallback robuste + circuit breaker (contrainte #3)

### Fichiers créés
- `services/llm/circuit.py` — breaker **par (provider, model)** en mémoire process
  (dict + lock). CLOSED → (`LLM_CIRCUIT_FAIL_THRESHOLD` échecs consécutifs) → OPEN →
  (`LLM_CIRCUIT_COOLDOWN_SECONDS`) → HALF-OPEN → 1 succès = CLOSED / 1 échec = OPEN.
  **Sticky last-good** : `_LAST_GOOD[request_type]` = dernier `(provider, model)` qui a
  répondu → tenté en premier au prochain appel. `snapshot()` pour l'admin, `reset()`.
- `tests/test_llm_fallback.py` — 8 tests.

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `services/llm/providers.py` | `OpenAI(..., max_retries=0)` (était `1`) — **plus de double retry SDK** ; le routing possède le fallback. |
| `config.py` | `LLM_CIRCUIT_FAIL_THRESHOLD=3`, `LLM_CIRCUIT_COOLDOWN_SECONDS=60`, `LLM_CIRCUIT_ENABLED=True` ; `_circuit_public()` dans `llm_public_config`. |
| `services/gemini_client.py` | `_ordered_steps(route)` : (1) sticky last-good d'abord, (2) steps circuit CLOSED en ordre de route, (3) steps OPEN en **dernier recours**. `_run_route` : 1 tentative/step (timeout du route), fail-over rapide sur **toute** erreur provider (429/5xx/timeout), `record_failure`/`record_success(request_type=…)`. `reset_client()` vide le circuit. |
| `schemas/admin.py` | `LlmConfigView.circuit`. |
| `tests/conftest.py` | `_reset_llm_routing` vide aussi le circuit. |

### Tests — `tests/test_llm_fallback.py` (8)
`max_retries==0`, 1 tentative/step, breaker ouvre à 3 échecs, step OPEN sauté au 1er
passage, step OPEN = dernier recours si tout le reste échoue, half-open après cooldown +
récupération, sticky last-good (le primaire n'est plus tenté), tous échouent → `RuntimeError`.

### pytest complet après LOT 3+4 : **1105 passed, 1 skipped**. compileall : OK.

### Risque / note
- Circuit **en mémoire process** (pas cache-backed comme le circuit job-search) : choix
  assumé — le routing LLM est court et par worker, et ça garde le hot-path sans I/O.
  En multi-worker chaque process apprend indépendamment (acceptable, convergence rapide).

---

## LOT 5 — Parallélisation `profile_analysis ∥ ats_keyword_extraction` (contrainte #11)

### Fichier modifié : `services/generation_service.py`
- `_analysis_and_keywords_parallel(profile, jd, language)` : `ThreadPoolExecutor(2)`,
  `profiling.bind` réapplique le `RequestProfile` dans chaque worker (spans +
  `call_gemini` rattachés). Chaque tâche garde son span (`profile_analysis`,
  `keyword_extraction`). Les exceptions worker (rate limit, no model) **propagent**
  telles quelles — le fast-path n'avale rien.
- `run_cv_pipeline` : les 2 `with profiling.span(...)` séquentiels remplacés par cet appel.
  Point de jointure = `matching` (`calculate_match_score`), qui est le 1er à avoir besoin des deux.

### Pas de dépendance cachée
`_cached_profile_analysis` lit `profile` ; `_cached_keywords` lit `job_description`.
Aucun ne consomme la sortie de l'autre. Cache thread-safe (déjà utilisé dans le fan-out job).

### Tests — `tests/test_generation_parallel.py` (3)
2 spans présents + résultat identique au séquentiel ; wall-time concurrent (< 0.55s pour
2 appels qui dorment 0.3s chacun) ; `_analysis_and_keywords_parallel` == séquentiel manuel.

---

## LOT 6 — CV une seule page par mise en page adaptative (contrainte #7)

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `config.py` | `CV_MIN_BODY_FONT_PT=8.0`, `CV_MIN_AUX_FONT_PT=6.8`, `CV_MIN_MARGIN_MM=9.0`, `CV_SUMMARY_HARD_CAP=900`, `CV_PROJECT_DESC_HARD_CAP=400`. |
| `services/pdf_generator.py` | **Suppression** de `MAX_BULLETS_PER_EXP=3`, `MAX_PROJECTS=3`, `MAX_SUMMARY_CHARS`. `_compact_cv_data` ne slice **plus jamais** bullets/projets/expériences ; garde seulement un cap de sanité sur la prose LLM (résumé/desc projet) **loggé** quand atteint. Nouveau `_render` : essaie `(marge 14→9mm) × (scale 1.0→plancher)` — **chaque candidat est un vrai rendu**, on lit `doc.page` (l'ancien `_measure_story_height` renvoyait 0 pour un `KeepTogether` → décision « tient sur 1 page » fausse sur les gros CV). 1ère config à 1 page = gagnée. Sinon → config plancher, **le CV pagine** (2+ pages, **rien perdu**), `_layout.fit_one_page=False`. Plancher : police corps ≥ `CV_MIN_BODY_FONT_PT`. |
| `services/ats_optimizer.py` | `_build_optimization_prompt` : **suppression `experience[:4]` et `achievements[:3]`**. Toutes les expériences + toutes les réalisations passent au prompt ; borne = budget **caractères** (7000) avec **marqueur explicite** `[context budget reached — N more role(s) not shown in THIS prompt; they are still in the CV]` (jamais une coupe cachée). |
| `services/generation_service.py` | `CVPipelineResult.layout` ; `run_cv_pipeline` remonte `cv_data["_layout"]`. |
| `schemas/cv.py`, `cv_router.py` | `GenerateCVJsonResponse.layout` = `{scale, margin_mm, body_font_pt, min_body_font_pt, fit_one_page, pages}`. |

### Garantie contrainte #7
Toutes les expériences, tous les bullets, tous les projets sont **toujours rendus**.
Le passage à 2 pages est **signalé** (`fit_one_page=False`, `pages≥2`), jamais silencieux.

### Tests — `tests/test_pdf_one_page.py` (6)
`_compact_cv_data` ne slice pas (9 bullets/7 projets conservés) ; CV normal (4×5 bullets) →
tous les bullets dans le texte du PDF, `body_font_pt ≥ plancher` ; CV énorme (18×10 longs
bullets) → **tous** les bullets présents, `fit_one_page=False`, `pages≥2`, police ≥ plancher ;
CV court → 1 page scale 1.0 ; prompt ATS contient les 6 rôles + `ACH-5-4`/`ACH-0-4`.

### Note perf
Un CV qui tient (cas quasi-général) = **1 seul rendu** (marge 14, scale 1.0). Un CV
qui déborde = jusqu'à ~4 marges × ~5 scales rendus (~coût sub-seconde). Assumé : QUALITÉ > VITESSE.

### pytest complet après LOT 5+6 : **1113 passed, 1 skipped**. compileall : OK.

---

## LOT 7 — Cache lettre (contrainte #12)

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `config.py` | `LETTER_PROMPT_VERSION=2` (bump = invalidation globale), `LETTER_CACHE_TTL=86400`. |
| `services/generation_service.py` | `_letter_cache_key(request)` = `hash(v{LETTER_PROMPT_VERSION} + digest(cv_profile) + digest(job_description) + language + digest(recipient/address overrides))`. `run_letter_pipeline` : lookup cache → sur HIT retourne le PDF (base64 en cache pour compat Redis) + `structured` + `from_cache=True`, **0 appel LLM**. Retourne désormais `LetterPipelineResult(pdf_bytes, structured, from_cache)`. |
| `cv_router.py`, `services/job_application_service.py` | adaptés au nouveau retour. |

### Tests — `tests/test_letter_cache.py` (4)
régé identique = HIT (0 nouvel appel LLM, `structured` identique) ; changement de langue = MISS ;
bump `LETTER_PROMPT_VERSION` change la clé ; override recipient change la clé.

---

## LOT 8 — Versioning documents : référence STABLE + versions (contraintes #13/#14, ajustement utilisateur)

### Règle appliquée (précisée par l'utilisateur)
`reference` **ne change jamais** entre versions : `CV_A8F42K` → v1 → v2 → v3.
Colonne `version` incrémentée, anciennes versions **conservées**, `structured_source` = source de vérité,
historique récupérable par référence, **owner-scoping strict**.

### Fichiers créés
- `services/document_service.py` — `mint_reference`, `next_version`, `versions`, `latest`,
  `get_version`, `resolve` (référence → dernière version, ou row id ; **404** inconnu / **403** autre user),
  `structured(row)`, `record_cv(...)`, `record_letter(...)`. Tout owner-scopé.
- `tests/test_document_versioning.py` — 8 tests.

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `models/generated_cv.py`, `models/generated_letter.py` | += `reference` (indexée), `version` (défaut 1, `server_default`), `parent_id`, `structured_source` (Text JSON), `job_hash`, `conversation_id`. `UniqueConstraint(reference, version)`. Colonnes additives → `_ensure_additive_columns` migre. |
| `database.py` | `_ensure_document_references()` : back-fill `reference`/`version=1` sur les lignes pré-versioning (idempotent, ne réécrit jamais une référence existante). `_mint_reference(prefix)` = `PREFIX_` + 6 chars base32 (sans I/O/0/1). Appelé dans `init_db()`. |
| `cv_models.py` | `GenerateCVRequest.reference` / `GenerateLetterRequest.reference` optionnels — fournir = nouvelle version du même document. |
| `services/generation_service.py` | `CVPipelineResult.cv_data` déjà présent (LOT 2) = `structured_source` du CV ; `LetterPipelineResult.structured` = `{body, company, candidate, language}` ; helpers `render_cv_from_structured` / `render_letter_from_structured` (re-render sans LLM — pour LOT 9). |
| `cv_router.py` | génération → `document_service.record_cv/record_letter` (structured_source, job_hash, `reference` du payload). Réponses += `reference`, `version`. `get_cv`/`download_cv`/`get_letter`/… acceptent **référence ou row id** via `resolve`. `delete` par référence = supprime **toutes** les versions ; par row id = juste celle-là. |
| `schemas/cv.py` | `reference`+`version` sur les réponses/list items ; nouveau `DocumentVersionItem`. |

### Nouveaux endpoints (extension, PAS de namespace — contrainte #17)
- `GET /api/cvs/{ref|id}/versions` — historique complet, `is_latest` par version
- `GET /api/cvs/{ref|id}/versions/{n}/download` — télécharger une version précise
- idem `…/letters/…`

### Tests — `tests/test_document_versioning.py` (8)
1ère génération mint une réf `CV_…` v1 ; régé avec `reference` → v2/v3, **réf inchangée**,
`GET /versions` = [1,2,3] avec `is_latest`, chaque version téléchargeable ; `GET /api/cvs/{ref}` = dernière ;
`structured_source` persisté et parsable ; réf inconnue → 404 ; réf d'un autre user → 403 ;
delete réf → toutes versions supprimées ; `next_version` owner-scopé.

### pytest complet après LOT 7+8 : **1125 passed, 1 skipped** (1 régression `test_auto_apply` corrigée : 3e appelant de `run_letter_pipeline` = `job_application_service`). compileall : OK.

---

## LOT 9 — Agent d'édition par référence (contraintes #13/#16/#18) + LOT 10 (historique conv ↔ document, #15/#16)

### Approche (validée) : intent-JSON déterministe
`message → LLM propose UNE AgentAction (schéma JSON strict, temp 0) → backend valide →
applique le changement minimal sur structured_source → re-render PDF → nouvelle version
(même référence) → retourne nouvelle réf/version`. **Le LLM ne touche jamais la DB ni le PDF.**

### Fichiers créés
- `schemas/agent_schemas.py` — `AgentAction` + `AGENT_ACTION_SCHEMA` (strict). Vocabulaire
  **limité** (#18) : CV = `set_summary`, `set_career_objective`, `add_skill`, `remove_skill`,
  `rename_skill_category`, `accept_suggested_skill`, `add_bullet`, `edit_bullet`,
  `remove_bullet`, `reorder_experience`, `set_language` ; lettre = `set_letter_body`,
  `replace_letter_paragraph`, `set_recipient` ; + `none`. Rien d'autre n'est exprimable.
- `services/agent_service.py` — `EditAgent`, `find_reference(text)`, `_apply_cv`/`_apply_letter`
  (application **déterministe** en Python), `_unsupported_facts(text, ctx)` (garde
  anti-hallucination : nombres/%/tokens capitalisés absents du message user ET du document).
- `tests/test_agent_editing.py` — 9 tests.

### Fichiers modifiés
| Fichier | Changement |
|---|---|
| `models/conversation.py` | `Message.document_reference` (nullable, indexée) — lie le message assistant au document édité (#15). |
| `services/conversation_service.py` | `send_message` retourne `(user_msg, assistant_msg, document|None)`. Si le message contient `CV_…`/`LETTER_…` → `EditAgent`, sinon chat normal. `_doc_context(user, conv)` (**LOT 10**) : injecte l'état structuré (read-only) du dernier document touché par la conversation dans le prompt chat → « mon résumé est-il bon ? » a du contexte **sans ré-upload** (#16). `_append` accepte `document_reference`. |
| `conversations_router.py`, `schemas/conversation.py` | `MessageSendResponse.document` (`EditedDocument{reference, version, kind, download_url}`). **Pas de nouvel endpoint** — `POST /api/conversations/{id}/messages` route vers l'agent si référence détectée (#17). |
| `config.py` `DEFAULT_LLM_ROUTING` | entrée `agent_edit` → chaîne structurée, temp 0. |
| `tests/conftest.py` | `services.agent_service` dans `_LLM_CONSUMER_MODULES` ; routeur mock `agent_edit` (no-op par défaut). |

### Garde anti-hallucination (#5/#13)
Une action `add_skill`/`edit_bullet`/`set_summary`/… qui introduirait un fait candidat
(compétence, techno, société, date, chiffre, %) **absent du message de l'utilisateur ET du
document** → **refus** avec `needs_confirmation` : « I won't add information you haven't
confirmed: Kubernetes. If that is genuinely part of your background, tell me explicitly… ».
« ajoute Docker, je m'en sers tous les jours » → **appliqué** (fourni par l'utilisateur).

### Source de vérité
`EditAgent` charge `structured_source` (jamais le PDF), applique, re-render via
`render_cv_from_structured`/`render_letter_from_structured` (**0 appel LLM au rendu**),
`document_service.record_cv/record_letter(reference=…, conversation_id=…)` → nouvelle version,
**ancienne conservée**.

### Tests — `tests/test_agent_editing.py` (9)
`find_reference` ; `_unsupported_facts` (Kubernetes/40% flaggés, "Python/SQL" déjà connus OK) ;
`_apply_cv` déterministe (add_skill, reorder = permutation complète obligatoire) ; édition par
réf → v2 même référence, v1 conservée, message assistant lié au doc ; **refus** d'inventer
Kubernetes (aucune version créée) ; **application** quand l'utilisateur nomme la compétence ;
message sans référence → chat normal (pas d'appel `agent_edit`).

### pytest complet après LOT 9+10 : **1134 passed, 1 skipped**. compileall : OK.

---

## LOT 11 — Nettoyage des endpoints (contraintes #8 / #17)

### Livrable : `benchmarks/ENDPOINT_MAP.md` (cartographie complète)
Chaque route → appel frontend (`cv_frontend-main/src/app/shared/services/*.ts`) ? test ?
appel interne ? doublon ?

### Conclusion : **aucune suppression**
- Seul doublon fonctionnel = `POST /api/optimize-existing-cv` (≡ `/api/generate-cv`).
  **Mais activement appelé par le frontend** (`cv-generator.service.ts:23`), documenté
  (`API_FRONTEND_GUIDE.tex §503`), testé → le supprimer **casserait le frontend**
  (interdit par #17). Reco : convergence front → `/generate-cv`, puis suppression.
- Tous les autres endpoints : frontend, ou testés, ou sous-systèmes délibérés
  (admin / conversations / profile / dashboard) — leur suppression serait une
  **régression**, pas un nettoyage.
- **Phase 2b n'a créé aucun namespace ni endpoint agent** : `…/versions` étend
  `/api/cvs`·`/api/letters` (4 routes strictement additives), l'agent d'édition réutilise
  `POST /api/conversations/{id}/messages`.

L'API reste **simple et stable en surface** (#22).

---

## LOT 12 — Tests + benchmark AVANT/APRÈS (contrainte #20)

### Livrables
- `benchmarks/bakeoff/pipeline_bench.py` — bench des **7 scénarios** end-to-end à travers
  le pipeline routé réel (Groq live + fallback Gemini), MinIO/DB en mémoire. Mesure
  wall / appels LLM / fallbacks / cache hits / tokens (via `services.profiling`) +
  garde anti-hallucination (techno JD injectée ? chiffre inventé ?).
- `benchmarks/bakeoff/results/pipeline_after.json` — mesures brutes.
- `benchmarks/bakeoff/RAPPORT_2b.md` — tableau AVANT/APRÈS complet.

### Résultats clés (live, moyenne 3 runs)
| | AVANT | APRÈS |
|---|---|---|
| **Génération CV** | **~180 s** (test utilisateur, NVIDIA) | **~5–7.5 s** (Groq + fallback) → **~25–35×** |
| Régénération CV identique | ~180 s | **0.02 s** (3 cache hits) |
| Extraction CV | (part des ~3 min) | ~3.0 s |
| Lettre | — | ~2.5–3 s (échec propre en 11 s si Groq+Gemini 429 même fenêtre) |
| Chat | — | ~5–7 s |
| Modif CV par référence | ❌ inexistant | ~2–3 s (**1 appel LLM**, re-render local) |
| Modif lettre par référence | ❌ inexistant | ~2–2.7 s |
| p50 / p95 | — | **3.0 s / 7.5 s** |
| **Techno JD injectée dans le CV** | ❌ (tous modèles, bake-off) | ✅ **aucune** (`jd_tech_injected_into_cv: []`, live) |
| Chiffres inventés | risque | ✅ `[]` |
| CV 1 page | coupe silencieuse | ✅ toutes expériences, `fit_one_page: true`, police 9pt ≥ plancher |

### Constat live majeur
Le **goulot est maintenant la capacité free-tier** (Groq 8000 tok/min), plus l'architecture.
Le routing bascule Groq→Gemini sous 429 TPM (`fb=1–2` observés). Passer Groq en **Dev Tier**
(ou activer un provider payant) supprime ce plafond. Le circuit breaker + fail-over
transforment un échec provider en **~11 s** au lieu de **~3 min**.

### Verdict #20
Vitesse **~30×** sur la génération CV **sans dégradation de qualité** (structured output
strict + P1/P3/P5 durcis + additional_skills séparées + zéro coupe silencieuse + statut ATS
explicite + fallback maîtrisé). Une amélioration de vitesse acceptée **seulement** parce que
la qualité est égale ou meilleure.

---

## État global des tests

| Après | pytest | compileall |
|---|---|---|
| LOT 1 | 1084 ✓ / 1 skip | OK |
| LOT 3+4 | 1105 ✓ | OK |
| LOT 5+6 | 1113 ✓ | OK |
| LOT 7+8 | 1125 ✓ | OK |
| LOT 9+10 | 1134 ✓ | OK |
| **Final (LOT 1–12)** | **1134 passed, 1 skipped** | **OK** (repo entier) |

Migration versioning : back-fill `reference`/`version` **testé + idempotent** (probe manuel).
Note : la `UniqueConstraint(reference, version)` ne s'applique qu'aux tables créées à neuf
(`_ensure_additive_columns` n'ajoute pas de contraintes) — `document_service.next_version`
garantit l'unicité applicativement ; ajouter la contrainte via migration manuelle si besoin.

## Nouveaux fichiers (récap)
`schemas/llm_schemas.py`, `schemas/agent_schemas.py`,
`services/llm/routing.py`, `services/llm/circuit.py`,
`services/document_service.py`, `services/agent_service.py`,
`benchmarks/bakeoff/pipeline_bench.py`, `benchmarks/ENDPOINT_MAP.md`,
`benchmarks/bakeoff/RAPPORT_2b.md`,
tests : `test_structured_output.py`, `test_additional_skills.py`, `test_llm_routing.py`,
`test_llm_fallback.py`, `test_generation_parallel.py`, `test_pdf_one_page.py`,
`test_letter_cache.py`, `test_document_versioning.py`, `test_agent_editing.py`.

---

## CLÔTURE Phase 2b — Production hardening (round final)

### 1. Versioning DB — vraie contrainte UNIQUE
`database._ensure_document_unique_constraints()` : `CREATE UNIQUE INDEX IF NOT EXISTS`
sur `(reference, version)` pour `generated_cvs` / `generated_letters` — **idempotent**
(SQLite + PostgreSQL), **data-preserving**. Si des doublons pré-existants empêchent la
création → log `ERROR` + skip (pas de crash au boot). Ferme la course concurrente que
`next_version()` seul ne couvrait pas. Test : `test_db_rejects_duplicate_reference_version`
(l'INSERT dupliqué lève `IntegrityError`).

### 2. Circuit breaker — portée documentée
Docstring `services/llm/circuit.py` : **explicitement per process/worker**, en mémoire,
**pas de Redis maintenant**. Évolution vers un breaker partagé (cache-backed, comme
`services/providers/circuit.py`) **seulement si** un déploiement multi-worker montre des
incidents provider répétés/corrélés où la redécouverte par worker devient coûteuse.

### 3. LLM / capacité provider
- `.env.example` : bloc **LLM routing** + **circuit breaker** + **note capacité
  PRODUCTION** (Groq free ≈ 8000 TPM → créer la clé Groq en **Dev Tier**, et/ou projet
  Gemini payant ; routing + fallback + circuit **restent actifs** avec un provider payant,
  ils ne se déclenchent juste presque jamais).
- Chaîne prose : `gemini-flash-latest` (a 503 sous charge) → **`gemini-3.5-flash`**
  (pinné) ; `gemini-3.5-flash-lite` reste le fallback Gemini principal (le plus fiable).

### 4. Mistral — re-testé avec la nouvelle clé (2026-09-08)
`mistral-small-latest` / `mistral-medium-latest` / `mistral-medium-3.5` → **toujours
`429 "Rate limit exceeded"`** malgré la nouvelle clé.
→ **reste dans `LLM_DISABLED_PROVIDERS=mistral`**. Réactiver (`LLM_DISABLED_PROVIDERS=""`)
+ re-probe capacité **quand le compte Mistral aura du quota**.

### 5. Benchmark — cadrage
`RAPPORT_2b.md` : mise en garde en tête — la baseline ~180 s (NVIDIA, Phase 1) vient d'un
**environnement/provider différent** ; le « ~25–35× » est un **résultat observé, pas un
benchmark strictement comparable**. **La référence est le benchmark APRÈS** :
génération CV ~5–7,5 s · régénération cache ~0,02 s · p50 ~3 s · p95 ~7,5 s ·
aucune techno JD injectée · aucun chiffre inventé.

### 6. Nettoyage endpoints — décision revue (frontend pas encore commencé)
Chaque endpoint passé aux **4 critères** (appel interne indispensable / test fonctionnel
indispensable / dépendance backend cachée / dépendance au futur flux frontend).

| Endpoint | 4 critères | Décision |
|---|---|---|
| **`POST /api/optimize-existing-cv`** | tous ✅ (alias pur de `/generate-cv`, tests redondants, 0 dép cachée, front couvert par `/generate-cv`) | **SUPPRIMÉ** |
| `GET /api/dashboard` (user) | ❌ ~25 tests fonctionnels (isolation cache, invalidation e2e) ; ❌ hooks `invalidate` dans `cv_router` + `job_application_service` ; ❌ **documenté comme « point d'entrée après connexion » dans `API_FRONTEND_GUIDE.tex`** | **gardé** (échoue 3/4 critères) |
| `/api/profile` (GET/PUT/DELETE) | ❌ dép backend : `UserProfileService` alimente la recherche job + l'auto-candidature | **gardé** |
| jobs / conversations / admin / auth / extract / generate / documents+versions / health / stats | core produit, ou testés, ou console d'exploitation | **gardés** |

**Suppression effective : 1 endpoint** (`/api/optimize-existing-cv`). C'était le seul à
passer les 4 critères. Le reste = fonctionnalités cœur ou outillage d'exploitation ;
les supprimer serait une régression, pas un nettoyage (objectif explicite : « ne pas
supprimer de fonctionnalités »).

Fichiers touchés : `cv_router.py` (route + handler + `_owned_or_403` mort supprimés,
`_generate_cv` sans param `route`), `tests/test_api_integration.py` (tests remplacés par
`test_optimize_existing_cv_is_gone` → 404), `API_FRONTEND_GUIDE.tex`, `README.md`,
`PROJECT_STRUCTURE.md`.

### Tests après clôture : voir §Final. compileall : OK.

---

## Contraintes NON couvertes / à faire côté opérateur
- **Groq Dev Tier / provider payant** — pour lever le plafond ~8000 tok/min free-tier
  (`.env.example` documente la marche à suivre).
- **Mistral** — toujours 429 avec la nouvelle clé → `LLM_DISABLED_PROVIDERS=mistral`.
  Réactiver + re-probe quand le compte a du quota.
- **Docker** — non testable dans cette session (session Windows interactive requise) ;
  pipeline validé via local + bench live.
- **Circuit breaker partagé** — à envisager seulement si multi-worker + incidents provider fréquents.
- **API_FRONTEND_GUIDE.tex** — à recompiler en PDF ; mentions résiduelles de
  `/optimize-existing-cv` dans `REFACTOR_REPORT.md` (journal historique, laissé tel quel).

