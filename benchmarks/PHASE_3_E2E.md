# Phase 3 — E2E, production hardening & Docker validation

Priorité : **E2E réel > sécurité/intégrité > fiabilité > observabilité > performance > refactoring.**
Backend uniquement — le frontend est développé séparément.

---

## 1. Environnement

| | |
|---|---|
| Tests unitaires/E2E | conda `cv-api`, SQLite jetable, MinIO en mémoire, LLM mocké (déterministe) |
| **E2E Docker** | `docker compose` — **PostgreSQL 16 + Redis 7 + MinIO** réels, **LLM live** (routing Groq→Gemini) |
| Image | `cv-assistant:latest` build OK ; API `python:3.11` non-root (uid 10001) |
| Ports | API mappée sur `8010` (le `8000` de l'hôte est pris par un autre projet — override temporaire) |
| Providers LLM (re-ping 2026-09-08) | Groq `gpt-oss-120b` ✅ 0.5s · Gemini `3.5-flash-lite` ✅ 0.7s · `gemini-flash-latest` ❌ 503 sous charge · NVIDIA ❌ 503 · **Mistral (nouvelle clé) ❌ 429 "Rate limit exceeded" sur les 3 modèles** |

---

## 2. Workflow candidat testé (le critère principal)

```
LOGIN → PROFIL → UPLOAD CV → EXTRACTION → PROFIL STRUCTURÉ → JOB SEARCH →
JOB SELECTION → MATCHING → TARGETED CV → COVER LETTER → ANTI-HALLUCINATION →
APPLICATION → ASSOCIATION → APPLICATION RETRIEVED → APPLICATION VERIFIED
```

**Résultat : le candidat traverse toute la chaîne. Aucun point de rupture.**

### 2a. En test (`tests/test_e2e_workflow.py::test_full_candidate_workflow`) — 16 étapes vertes

| # | Étape | Vérifié |
|---|---|---|
| 1 | signup + JWT + `GET /me` | token réel, email correct |
| 2 | `GET /api/profile` puis `PUT` | profil vide → rempli |
| 3-4 | `POST /api/extract-cv` (multipart .txt) | 200, pipeline local + LLM |
| 5 | profil structuré | nom/email/skills/adresse extraits du **vrai** texte, rien d'inventé |
| 6 | `POST /api/jobs/search` (fan-out réel, provider faké) | 200, ≥1 offre, offre persistée |
| 7 | `GET /api/jobs/{id}` | l'offre sélectionnée = celle utilisée ensuite |
| 8 | matching (`job_match_service.evaluate`) | score ∈ [0,1], raisons citent "Python" |
| 9 | `POST /api/generate-cv` (JD = description de l'offre) | `CV_xxxxxx` v1, `ats_optimization` renseigné |
| 10 | `POST /api/generate-letter` | `LETTER_xxxxxx` v1 |
| 11 | **anti-hallucination** | Docker/Kubernetes/Kafka de la JD **absents du corps du CV** ; présents comme `additional_skills` séparés |
| 12-14 | `POST /api/jobs/{id}/apply` (cv_id + letter_id + prepare) | 200 |
| 13 | association | `cv_id` == CV généré, `letter_id` == lettre générée, `job_id` == offre |
| 13c | jamais "submitted" | `application_status != "submitted"` |
| 15 | `GET /api/jobs/applications` | la candidature apparaît |
| 16 | `GET /api/jobs/applications/{id}` | statut, cv_id, letter_id, job_offer_id, match_score cohérents ; `row.user_id == user` (owner-scoping au niveau DB) |

### 2b. En Docker (Postgres + Redis + MinIO + LLM live) — 16 appels HTTP, tous 200

| Étape | Cold run 1 | Cold run 2 | Notes |
|---|---|---|---|
| signup | 0.27s | 0.23s | |
| me / profile | 0.03s | 0.02s | |
| **extract-cv** | 2.32s | 1.74s | pipeline local + LLM |
| **generate CV v1** | 3.96s | 5.07s | `ats=37.5`, `status=applied`, suggestions=`[Kafka, Docker, Kubernetes, FastAPI, English]` **séparées** |
| generate CV v2 (même réf) | 1.57s | 1.74s | |
| list versions / download v1 | 0.01s | 0.03s | |
| **generate letter** | 2.06s | 2.41s | |
| conversation | 0.02s | 0.02s | |
| **agent edit** (→ CV v3, PDF re-rendu) | 1.14s | 1.03s | 1 seul appel LLM, re-render local |
| **job search** (20 providers, HTTP réel) | 39.3s | 45.1s | 55 offres, 13 providers `success` — capé par `JOB_SEARCH_DEADLINE=45s` |
| job detail / apply / retrieve | ~0.04s | ~0.03s | `status=requires_user_action` (l'offre linkedin exige un login externe — comportement correct) |
| **2e run, tout en cache Redis** | — | **total 2.85s** | régé identique = 0.02s (extraction + CV + lettre `status=cache`, search `0.01s`) |

**Total workflow cold ≈ 50–58s**, dont **~40–45s de job search** (fan-out 20 providers, HTTP réel).
**Core hors job-search ≈ 11–14s.** Régénération complète en cache ≈ **3s**.

---

## 3. Sécurité / owner-scoping E2E (`test_e2e_owner_scoping_user_a_vs_user_b`)

User B (authentifié) tente d'atteindre les données de User A :

| Tentative | Réponse |
|---|---|
| `GET /api/cvs/{A.id}` · `/{A.ref}` | **403** |
| `GET /api/cvs/{A.id}/download` | **403** |
| `GET /api/cvs/{A.ref}/versions` · `/versions/1/download` | **403** |
| `DELETE /api/cvs/{A.id}` | **403** |
| `GET /api/letters/{A.id}/download` · `/{A.ref}/versions` | **403** |
| `POST /api/jobs/{id}/apply` avec `cv_id`/`letter_id` de A | **403** (avant toute création) |
| `GET /api/jobs/applications/{A.app}` | **403** |
| `GET /api/jobs/applications` | la candidature de A **absente** de la liste de B |
| édition par référence de A via l'agent | réponse générique « I couldn't find a document … you can edit » — **pas de fuite** (403↔404 indistinguables), **aucune version créée** |
| référence inconnue `CV_ZZZZZZ` | **404** |
| version inconnue `…/versions/99/download` | **404** |

Contrat 403/404 respecté partout.

---

## 4. Versioning dans le workflow (`test_e2e_versioning_in_workflow`)

`CV_xxxxxx` v1 → v2 (avec `job_description`) → v3 (langue `fr`) :
- **référence stable** sur les 3 versions ; `version` = 1, 2, 3 ;
- `GET /api/cvs/{ref}/versions` = `[1, 2, 3]`, `is_latest` sur v3 uniquement ;
- **chaque ancienne version téléchargeable** (`/versions/{n}/download` → 200) ;
- `structured_source` correct par version (`v3.language == "fr"`, `v1.language == "en"`) ;
- **la bonne version (v3, par son row id) est associée à la candidature** (`apply` → `cv_id == v3.id`).
- **DB : `UNIQUE (reference, version)`** — vérifié en Postgres (`test_db_rejects_duplicate_reference_version` → `IntegrityError`).

---

## 5. Agent d'édition (`test_e2e_agent_edit_and_anti_hallucination`)

```
message → POST /api/conversations/{id}/messages → AgentAction (JSON strict) →
validation backend → nouvelle version → PDF re-rendu
```

- **anti-hallucination** : « add Kubernetes to my skills » (Kubernetes ni dans le doc ni assumé
  comme réellement maîtrisé par l'utilisateur) → **refus** (`needs_confirmation`), **aucune v2** ;
- **modification légitime** : « add Go, I use it daily at work » (signal de possession explicite)
  → **appliquée**, `CV_xxxxxx` v2, PDF re-rendu, `structured_source` de v2 contient `Go` et **pas** `Kubernetes`.
- Le garde-fou distingue **« add X » (une demande)** de **« add X, je l'utilise… »
  (une assertion de possession)** — nouvelle logique `_asserts_ownership()`.

---

## 6. Fallback LLM (`test_e2e_llm_provider_failure_then_fallback`)

```
Groq (primaire) → 429 rate limit → fallback Gemini → résultat JSON valide
```

- **1 seule tentative par step** (`calls["groq"] == 1`) — pas de double retry caché (SDK `max_retries=0`) ;
- le circuit breaker enregistre l'échec, le provider gagnant (`gemini`) est loggé dans le profiling
  (`by_call[-1].provider == "gemini"`, `fallback == True`) ;
- **sortie non corrompue** (`"{}"`) ;
- tous les providers KO → **`RuntimeError("No LLM model available…")` propre**, pas de crash.
- Observé aussi **en Docker live** : `generate-cv` a basculé Groq→Gemini sous 429 TPM et a quand même produit un CV valide.

---

## 7. Mistral — re-ping avec la NOUVELLE clé (2026-09-08)

`mistral-small-latest`, `mistral-medium-latest`, `mistral-medium-3.5` →
**tous `429 {"type":"rate_limited","message":"Rate limit exceeded"}`**, malgré la clé mise à jour.

**Décision (inchangée)** : `LLM_DISABLED_PROVIDERS=mistral` conservé. Mistral reste dans
l'abstraction (buildable à la demande) mais **jamais en fallback automatique**. Aucun retry
agressif introduit. `test_mistral_is_still_disabled_in_routing` garantit qu'aucune chaîne de
`DEFAULT_LLM_ROUTING` ne contient `mistral`.
À réévaluer (`LLM_DISABLED_PROVIDERS=""` + re-probe capacité/latence/JSON) quand le compte
Mistral aura réellement du quota.

---

## 8. Production hardening (corrections justifiées par un risque/bug réel)

| # | Problème réel | Correction | Fichier |
|---|---|---|---|
| 1 | **Bug trouvé par Docker/Postgres** : `HAVING c > 1` (alias SELECT) → `psycopg2 UndefinedColumn`, **startup échoue** | `HAVING COUNT(*) > 1` | `database.py` |
| 2 | **Presigned URL = `http://minio:9000/…`** (hostname interne Docker) → **un navigateur ne peut pas télécharger le PDF** | `MINIO_PUBLIC_ENDPOINT` + client de signature séparé (`_url_client`, `region` fixée → pas de `get_bucket_location` réseau) ; fallback sur `MINIO_ENDPOINT` si non défini | `services/minio_service.py`, `config.py`, `docker-compose.yml`, `.env.example` |
| 3 | **Création partielle** : `_prepare_letter` faisait `db.commit()` **avant** la candidature → une lettre pouvait être committée sans candidature associée | lettre **plus committée dans `_prepare_letter`** → commit atomique avec la `JobApplication` dans `_persist` ; `db.rollback()` sur échec (drop de la ligne à demi-ajoutée). Test : `test_e2e_application_letter_failure_is_atomic` (aucune ligne orpheline). | `services/job_application_service.py` |
| 4 | `job_description` **non borné** → prompt/coût/timeout non maîtrisés | `max_length=20000` sur `GenerateCVRequest` + `GenerateLetterRequest` | `cv_models.py` |
| 5 | `get_db()` ne rollback pas explicitement sur exception de requête | `except Exception: db.rollback(); raise` | `database.py` |
| 6 | Pas de garde DB contre une requête pathologique (Postgres) ; connexions idle non recyclées | `pool_recycle=1800` + `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`, défaut 15s, PG only) | `database.py`, `config.py` |
| 7 | Anti-hallucination agent : « add Kubernetes » passait car « Kubernetes » figurait dans le message | garde `_asserts_ownership()` — le mot doit être **dans le document** OU accompagné d'un signal de possession (`I use`, `j'utilise`, `daily`, `at Acme`…) | `services/agent_service.py` |
| 8 | Agent : accès cross-user à une référence → `HTTPException 403` remontait au client depuis `POST /messages` | l'agent **capture** l'exception et répond génériquement (pas de fuite d'existence) | `services/agent_service.py` |

**Audit — points examinés, jugés OK, non modifiés** (pas de refactoring gratuit) :
CORS (`allow_origins` configurable, **pas** de `allow_credentials` → `*` sans risque, le front
envoie un Bearer header) · `/api/health` public (nécessaire au healthcheck Docker) ·
`/api/stats` public (compteurs agrégés, aucun contenu) · owner-scoping conversations/documents/
applications (403/404 cohérents, vérifiés E2E) · profiling ne logge jamais prompt/réponse/document ·
circuit breaker (per-process, documenté) · validation Pydantic sur toutes les entrées ·
extraction : taille max 10 MB + type de fichier validé (415).

---

## 8bis. API contract — gel Phase 3 (pour le développeur frontend)

Endpoints conservés après le nettoyage Phase 2b. Aucun nouvel endpoint créé en Phase 3.
Auth = `Authorization: Bearer <jwt>` sur tout sauf `/api/auth/*`, `/api/health`, `/api/stats`.

| Endpoint | Entrée | Sortie | Codes | Notes contrat |
|---|---|---|---|---|
| `POST /api/auth/signup` `/signin` | `{email, password}` | `{access_token, user:{id,email,is_superadmin}}` | 201 / 200 · 409 email pris · 401 mauvais identifiants | |
| `GET /api/auth/me` | — | `{id, email, is_superadmin}` | 200 · 401 | |
| `POST /api/auth/forgot-password` `/reset-password` | `{email}` / `{email,code,new_password}` | `{message}` | 200 (jamais 404 : pas d'énumération d'emails) | |
| `POST /api/extract-cv` | multipart `file` (PDF/DOCX/TXT ≤10 MB) + `language?` | `ExtractCVResponse{cv_profile(flat skills), confidence_scores, validation_issues, detected_language, …}` | 200 · 400 vide · 413 trop gros · 415 type · 422 langue/extraction · 500 | `cv_profile` = brouillon éditable, **jamais fabriqué** |
| `POST /api/generate-cv` | `GenerateCVRequest{cv_profile:CVProfile, job_description?≤20k, language, reference?}` | `{generated_cv_id, reference:"CV_xxxxxx", version, ats_score, download_url, expires_in, additional_skills[], ats_optimization:"applied\|local_fallback\|skipped\|cache", layout{fit_one_page,pages,…}}` | 200 · 401 · 422 · 429 LLM indispo · 503 LLM/MinIO absent | `reference` → nouvelle **version** du même document ; `additional_skills` = **suggestions séparées**, jamais dans le PDF |
| `POST /api/generate-letter` | `GenerateLetterRequest{cv_profile, job_description≤20k, language, recipient_name?, company_address?, reference?}` | `{generated_letter_id, reference:"LETTER_xxxxxx", version, download_url, expires_in}` | idem | régé identique servie du cache |
| `GET /api/cvs` · `/api/letters` | — | `[CVListItem{id,reference,version,filename,language,ats_score?,created_at}]` | 200 · 401 | tri par date desc |
| `GET /api/cvs/{ref\|id}` | — | `CVListItem` (dernière version si `ref`) | 200 · 403 autre user · 404 inconnu | |
| `GET /api/cvs/{ref\|id}/download` | — | `{download_url, expires_in}` | 200 · 403 · 404 | URL présignée **navigateur-résolvable** (voir §8 #2) |
| `GET /api/cvs/{ref\|id}/versions` | — | `[DocumentVersionItem{id,reference,version,…,is_latest}]` oldest→newest | 200 · 403 · 404 | |
| `GET /api/cvs/{ref\|id}/versions/{n}/download` | — | `{download_url, expires_in}` | 200 · 403 · 404 version inconnue | |
| `DELETE /api/cvs/{ref\|id}` | — | — | 204 · 403 · 404 | `ref` supprime **toutes** les versions ; `id` seulement celle-là |
| _(idem `…/letters/…`)_ | | | | |
| `POST /api/jobs/context` | `{messages:[{role,content}], context?, save_to_profile?}` | `JobContextResponse{context, missing_fields, clarifying_question, ready}` | 200 · 401 · 503 LLM | |
| `POST /api/jobs/search` | `JobSearchRequest{context, sources?, page, page_size≤50, sort, max_age_days?}` | `JobSearchResponse{results:[JobOfferOut], total, page, sources:{provider:status}, provider_states, from_cache}` | **toujours 200** (même 0 résultat / tous providers down) · 401 | ~40s cold (fan-out réseau) |
| `GET /api/jobs/sources` | — | `[ProviderInfo{name, state, …}]` | 200 · 401 | |
| `GET /api/jobs/{id}` | — | `JobDetailResponse{job:JobOfferOut, application:{method,apply_url,can_be_assisted:false}, freshness}` | 200 · 401 · 404 | revalide une offre stale |
| `POST /api/jobs/{id}/apply` | `ApplyRequest{cv_id?, letter_id?, context?≤4k, answers?, prepare?, generate_letter?, cv_profile?, letter_language?}` | `ApplicationResponse{application_status, application_id, application_url, cv_id, letter_id, match_score, match_reasons[], missing_skills[], is_duplicate, message}` | 200 · 401 · 403 cv/letter d'un autre user · 404 offre inconnue | **jamais `submitted`** ; `cv_id`/`letter_id` d'un autre user → **403 avant toute création** ; échec lettre → application créée sans lettre (message explicite), **pas de ligne orpheline** |
| `GET /api/jobs/applications` | — | `[ApplicationOut]` (les siennes) | 200 · 401 | |
| `GET /api/jobs/applications/{id}` | — | `ApplicationOut` | 200 · 403 autre user · 404 | |
| `GET /api/jobs/saved` · `POST/DELETE /api/jobs/{id}/save` | — | `[JobOfferOut]` / — | 200 / 204 · 401 · 404 | |
| `GET /api/profile` · `PUT` · `DELETE` | `UserProfileIn` | `UserProfileOut` | 200 / 204 · 401 | alimente le matching job |
| `GET /api/dashboard` | `refresh?` | `UserDashboardResponse` (agrégat des documents/candidatures/jobs de l'utilisateur) | 200 · 401 | cache Redis, invalidé aux écritures |
| `POST /api/conversations` · `GET` · `GET/DELETE /{id}` · `GET /{id}/messages` | | `ConversationOut` / `ConversationList` / `MessageList` | 200/201/204 · 401 · 403 · 404 | owner-scoped |
| **`POST /api/conversations/{id}/messages`** | `{content ≤8000}` | `MessageSendResponse{user_message, assistant_message, document?:{reference,version,kind,download_url}}` | 200 · 401 · 403/404 conv · 429/503 LLM | **si le message contient `CV_xxxxxx`/`LETTER_xxxxxx` → agent d'édition** : `document` non-null = nouvelle version créée ; sinon chat normal (`document` null). Réf d'un autre user / inconnue → réponse générique, aucune version. |
| `GET /api/admin/*` (superadmin) | | users / stats / providers / profiling / **`llm` (routing + circuit)** / dashboard | 200 · 401 · 403 non-superadmin | exploitation |
| `GET /api/health` · `GET /api/stats` | — | statut / compteurs LLM agrégés | 200 (public) | |

**Contrat de sécurité gelé** : accès à la ressource d'un autre utilisateur → **403** ;
ressource inexistante → **404** ; l'agent d'édition ne distingue pas 403/404 dans sa réponse
(pas de fuite d'existence). Provider LLM indisponible → **429** (`{"status":"error","message":…}`)
sur les routes de génération, **503** si la clé LLM n'est pas configurée du tout.

---

## 9. Docker / environnement réel — CE QUI A ÉTÉ VALIDÉ

| Élément | Résultat |
|---|---|
| `docker compose build api` | ✅ image construite |
| Startup | ✅ après correction du bug SQL Postgres (#1 ci-dessus) |
| **PostgreSQL 16** | ✅ 13 tables créées ; colonnes versioning présentes ; **`UNIQUE (reference, version)`** créé sur `generated_cvs` + `generated_letters` ; `_ensure_additive_columns` / `_ensure_document_references` / `_ensure_document_unique_constraints` idempotents |
| **Redis 7** | ✅ cache actif (2e run E2E entièrement servi du cache, total 2.85s) |
| **MinIO** | ✅ upload + download + **presigned URL navigateur-résolvable** (après correctif #2) — PDF téléchargé (`%PDF`, 1651 o) |
| Healthcheck | ✅ `GET /api/health` → 200, container `healthy` |
| Migrations | ✅ (pas d'Alembic — `init_db()` idempotent, testé sur PG) |
| Variables d'env | ✅ `env_file: .env` + overrides compose |
| API | ✅ 16/16 appels du workflow → 200 |
| Génération PDF | ✅ en container (`reportlab`) |
| **Workflow E2E complet en Docker** | ✅ (voir §2b) |

**Note** : port `8000` de l'hôte occupé par un autre projet → API testée sur `8010`
(`docker-compose.override.yml` temporaire, à supprimer). Rien d'autre n'a empêché la validation.

---

## 10. Performance E2E (mesure fiable, pas d'optimisation prématurée)

**Docker, LLM live, 2 cold runs** :

| Bloc | Temps |
|---|---|
| Auth (signup + me + profile) | ~0.3s |
| Extraction CV | 1.7–2.3s |
| Génération CV ciblée (cold) | **4–5s** |
| Génération lettre | 2–2.4s |
| Édition agent (1 tour) | ~1s |
| **Job search (20 providers, HTTP réel)** | **39–45s** (borne `JOB_SEARCH_DEADLINE`) |
| Apply + retrieve + verify | <0.15s cumulé |
| **Core hors job-search** | **~11–14s** |
| **Régénération identique (cache Redis chaud)** | **~3s** (workflow entier) |

- Appels LLM par génération CV : 3 (profile_analysis ∥ keywords, puis ats_optimization) ;
  lettre : 2 ; extraction : 3–4 ; édition agent : 1.
- Fallbacks observés en Docker : `generate-cv` a basculé Groq→Gemini sous 429 TPM (free tier).
- p50/p95 : trop peu de runs pour un chiffre stable ; **le job search domine** (fan-out réseau, pas le code).

**Recommandation perf (non bloquant)** : le job search est le seul poste lourd. Pistes futures —
réduire `JOB_SEARCH_DEADLINE`, désactiver les providers lents/inutiles via `JOBS_ENABLED_PROVIDERS`,
ou rendre le search asynchrone (job + polling) côté frontend. **Ne pas optimiser maintenant.**

---

## 11. Tests de régression

| | |
|---|---|
| pytest | **voir §12** — cible : 0 régression vs 1133+1skip fin Phase 2b |
| compileall | OK (repo entier) |
| `import main` | OK |
| **Nouveaux tests Phase 3** | `tests/test_e2e_workflow.py` (**7**) + `test_document_versioning.py::test_db_rejects_duplicate_reference_version` (**1**) + `test_minio.py` (**+2** presigned public/fallback) = **+10** |

---

## 12. Résultat final

| | Fin Phase 2b | **Fin Phase 3** |
|---|---|---|
| `pytest` | 1132 passed, 1 skipped | **1142 passed, 1 skipped** (**0 régression**, **+10** nouveaux tests) |
| `python -m compileall .` | OK | **OK** |
| `python -c "import main"` | OK | **OK** |
| Docker (build + startup + PG + Redis + MinIO + healthcheck + E2E) | non validé | **✅ validé** |
| Workflow candidat E2E (16 étapes) | — | **✅ vert en test ET en Docker live** |

**+10 tests Phase 3** :
- `tests/test_e2e_workflow.py` : `test_full_candidate_workflow`,
  `test_e2e_owner_scoping_user_a_vs_user_b`, `test_e2e_versioning_in_workflow`,
  `test_e2e_agent_edit_and_anti_hallucination`, `test_e2e_llm_provider_failure_then_fallback`,
  `test_e2e_application_letter_failure_is_atomic`, `test_mistral_is_still_disabled_in_routing` (7)
- `tests/test_document_versioning.py::test_db_rejects_duplicate_reference_version` (1)
- `tests/test_minio.py` : `test_presigned_url_is_signed_against_the_public_endpoint`,
  `test_presigned_url_falls_back_to_main_endpoint_when_no_public` (2)

---

## 13. Problèmes rencontrés / recommandations

### Corrigés dans cette phase
- Bug SQL Postgres au startup (`HAVING` alias) — **aurait bloqué tout déploiement prod**.
- Presigned URL non résolvable par un navigateur en Docker — **aurait cassé tous les téléchargements côté frontend**.
- Création partielle possible d'une lettre sans candidature.

### Encore ouverts (opérateur)
- **Groq Dev Tier / provider payant** — le free tier (~8000 TPM) provoque des 429 sous charge ;
  le routing bascule sur Gemini mais Gemini free 503 aussi sous pointe. Pour la prod : Groq Dev Tier.
- **Mistral** — toujours 429 avec la nouvelle clé ; reste désactivé.
- **Job search 40–45s** — acceptable pour un MVP, à rendre async côté produit plus tard.
- **`docker-compose.override.yml`** — temporaire (port 8010), à retirer ; en prod l'API prend le 8000.
- **`ALLOWED_ORIGINS`** — mettre le domaine réel du frontend en prod (défaut `*`).
- **`JWT_SECRET_KEY` / `MINIO_*` secrets** — via un gestionnaire de secrets en prod, pas `.env`.

### Le critère principal
> LOGIN → CV → EXTRACTION → PROFIL → JOB SEARCH → JOB SELECTION → MATCHING →
> TARGETED CV → COVER LETTER → APPLICATION → APPLICATION RETRIEVED → APPLICATION VERIFIED

**✅ Un vrai candidat traverse toute la chaîne avec le backend actuel** — validé en test ET en
Docker (Postgres + Redis + MinIO + LLM live). Aucun point de rupture ; les seules frictions sont
la latence du job search (réseau) et les 429 du free tier LLM (mitigés par le fallback).
