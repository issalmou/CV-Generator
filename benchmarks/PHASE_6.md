# Phase 6 — Agent-orchestrated jobs + clean public API contract

**Objectif** : aligner le backend avec le produit réel. Pas de page « Recherche
d'offres » : la recherche est pilotée par l'**agent conversationnel**, qui
appelle **directement les services Python internes** (jamais `/api/jobs/...`
comme le ferait un frontend). Le frontend ne voit que les offres retenues (via
le dashboard) et un bouton **Apply** par offre. Nettoyage réel du contrat API.

Date : 2026-09-08 · Baseline avant Phase 6 : **1211 tests**.

---

## A. Architecture cible

```
                         FRONTEND
                            │  (API publique uniquement)
   ┌────────────┬───────────┼───────────────┬───────────────┐
 Auth API    CV/Letter   Agent API       Dashboard        Jobs API
 /api/auth   /api/*      /api/convers-   /api/dashboard    /api/jobs/{id}
             (generate)  ations/*        /api/dashboard/   /api/jobs/{id}/apply
                            │             jobs             /api/jobs/applications
                            │                              /api/jobs/{id}/save
                            ▼
              ConversationService.send_message
                ├─ ref CV_/LETTER_ ─► EditAgent           (édition déterministe)
                └─ sinon ───────────► JobConversationAgent
                                       │ 1 call_gemini → ConversationDirective
                                       │
                                       ├─ search_jobs  ─► JobSearchService(db).search()   [Python]
                                       │                  └─ persiste top-N en SavedJob(origin="agent")
                                       ├─ apply_jobs   ─► PROPOSE only : fige job_ids sur
                                       │                  Conversation.pending_action
                                       └─ request_information ─► question (recommended_action)

              confirmation déterministe ("oui"/"confirme"…) sur pending_action
                                       └─► JobApplicationService(db).apply()  par offre  [Python]

                         SERVICES  →  DB / Redis / MinIO / LLM / providers
```

**Principe** : le frontend parle à l'API publique ; les services backend
communiquent **directement en Python** ; l'agent orchestre les capacités
backend mais n'a **jamais** d'accès direct à la DB ; les actions sensibles
(candidature) sont validées de façon **déterministe** par le backend.

---

## B. Endpoints conservés

| METHOD /path | Purpose | Frontend consumer | Auth | Owner scoping | Service |
|---|---|---|---|---|---|
| POST /api/auth/{signup,signin,forgot-password,reset-password,deactivate,request-reactivation,reactivate}, GET /api/auth/me | cycle de vie du compte | Register / Login / Profile | mixte | — | `AuthService` |
| POST /api/extract-cv | CV → profil structuré | Extraction CV | Bearer | — | `run_extraction` |
| POST /api/generate-cv, /api/generate-letter | génération (+ `reference` = nouvelle version) | CV / Lettre | Bearer | ✔ | `run_*_pipeline` |
| GET /api/cvs · /api/cvs/{id} · …/versions · …/versions/{n}/download · …/download, DELETE /api/cvs/{id} | documents & versions CV | Dashboard | Bearer | ✔ (`document_service.resolve` → 403) | `document_service` |
| GET/DELETE /api/letters/* (miroir CV) | documents & versions lettres | Dashboard | Bearer | ✔ | `document_service` |
| POST /api/conversations · GET (liste) · GET/{id} · DELETE/{id} · GET/{id}/messages · **POST /{id}/messages** | la surface de l'agent | Conversation | Bearer | ✔ (`get_owned`) | `ConversationService` → `EditAgent` / `JobConversationAgent` |
| GET/PUT/DELETE /api/profile | préférences de recherche (consulter / modifier / effacer) | Profile | Bearer | ✔ (PK = user id) | `UserProfileService` |
| GET /api/dashboard | stats agrégées de l'utilisateur | Dashboard | Bearer | ✔ | `UserDashboardService` |
| **GET /api/dashboard/jobs** *(NOUVEAU)* | offres retenues par l'agent + statut de candidature + cible Apply | Dashboard | Bearer | ✔ | `UserDashboardService.selected_jobs` |
| GET /api/jobs/{job_id} | détail d'une offre retenue | Dashboard | Bearer | ✔ **scopé à la sélection / candidatures de l'utilisateur (404 sinon)** | freshness + registry |
| POST /api/jobs/{job_id}/apply | le bouton **Apply** | Dashboard | Bearer | ✔ (`_check_ownership` CV/lettre) | `JobApplicationService.apply` |
| GET /api/jobs/applications · /api/jobs/applications/{id} | historique + statut des candidatures | Dashboard | Bearer | ✔ | `JobApplicationService` |
| POST/DELETE /api/jobs/{job_id}/save, GET /api/jobs/saved | gérer sa sélection (épingler / retirer) | Dashboard | Bearer | ✔ | `JobApplicationService` (`SavedJob`) |
| GET /api/health | healthcheck Docker / ops | — | public | — | — |
| /api/admin/* (24 routes) | dashboard admin / users / stats / documents / jobs / applications / LLM / providers / config runtime / profil admin (via `/api/auth/me` + reset) | Admin | Bearer **+ `require_superadmin`** | — | `AdminService` / `StatsService` / `runtime_config_service` |

**Profil admin** : l'admin **est** un `User` → `GET /api/auth/me`,
`POST /api/auth/reset-password`, `POST /api/auth/deactivate` couvrent
« consulter / modifier ses propres infos ». Aucun endpoint dédié nécessaire.

**Config runtime vs secrets** : inchangé — `runtime_config_service._ALLOWED`
est une liste blanche, toute `*_API_KEY` est rejetée, healthcheck +
rollback automatique sur `PATCH /api/admin/llm`.

---

## C. Endpoints supprimés

| METHOD /path | Reason | Replacement | Internal callers updated | Tests updated |
|---|---|---|---|---|
| POST /api/jobs/context | remplissage LLM d'un formulaire de recherche — aucun formulaire n'existe | l'agent appelle `JobPreferenceExtractor().enrich(...)` en interne | `api/jobs.py` (route + `_save_context_to_profile` supprimés) | `test_jobs_api.py` (3 cas retirés), `test_job_preference_extraction.py` (cas HTTP → cas service `enrich`) |
| POST /api/jobs/search (sync + `wait:false` → 202) | recherche manuelle — pas de page | `JobConversationAgent._search` → `JobSearchService(db).search(request)` [Python] | `api/jobs.py`, `main.py` lifespan | `test_jobs_api.py` réécrit ; `test_e2e_workflow.py` : la recherche passe par un tour de conversation |
| GET /api/jobs/search/{id}, DELETE /api/jobs/search/{id} | polling / annulation d'une recherche async — uniquement pour une page de recherche | néant (la recherche agent est synchrone, bornée par `JOB_SEARCH_DEADLINE`) | `api/jobs.py`, `main.py` | **`tests/test_job_search_async.py` supprimé (12 tests)** |
| GET /api/jobs/sources | liste du registre de providers = info interne / ops | `GET /api/admin/providers` (déjà existant) | `api/jobs.py` | `test_jobs_api.py` (cas `sources` retirés) ; `registry.all_info()` toujours utilisé par l'admin + `search_service` |
| GET /api/stats | **public, non authentifié** — compteurs internes du client LLM ; valeur frontend nulle ; même donnée dans `/api/admin/*` | `GET /api/admin/dashboard` / `GET /api/admin/llm` (derrière `require_superadmin`) | `main.py` | `test_architecture_imports.py`, `test_jobs_api.py` |

**Fichier mort supprimé** : `services/jobs/search_async.py` (+ son hook de
`lifespan` dans `main.py` + la fixture `_sync_job_search_async` de conftest).

**Schémas morts supprimés** (`schemas/jobs.py`) : `ConversationTurn`,
`JobContextRequest`, `JobContextResponse`, `SearchJobAccepted`,
`SearchJobStatus`, et le champ `JobSearchRequest.wait`.

**Bilan** : **−6 routes publiques** (`/jobs/context`, `/jobs/search`,
`/jobs/search/{id}` GET+DELETE, `/jobs/sources`, `/api/stats`),
**+1** (`GET /api/dashboard/jobs`). Aucune capacité métier perdue :
tout est atteignable via l'agent + le dashboard.

---

## D. Communications internes (appels Python directs, jamais HTTP)

| Appelant | Appelé | Comment |
|---|---|---|
| `JobConversationAgent._search` | `JobPreferenceExtractor().enrich(...)` | méthode Python **sans LLM** (l'agent a déjà fait son unique appel LLM pour la directive) — merge profil + patch de la directive + enrich local déterministe |
| `JobConversationAgent._search` | `JobSearchService(db).search(JobSearchRequest)` | appel Python direct ; persiste les top `AGENT_JOB_SELECTION_LIMIT` en `SavedJob(origin="agent", conversation_id, match_score)` (idempotent) |
| `JobConversationAgent._search` | `UserDashboardService(db, user).selected_jobs()` | pour renvoyer `jobs_found` au frontend |
| `JobConversationAgent._execute_frozen_apply` | `JobApplicationService(db).apply(user, offer_id, ApplyRequest(prepare=True))` | par offre, isolé (`try/except HTTPException`) — une offre en échec n'annule pas les autres |
| `ConversationService.send_message` | `EditAgent` / `JobConversationAgent` | dispatch déterministe (ref doc / confirmation en attente / sinon) |
| `run_cv_pipeline` + `api/cv._augment_cv_recommendations` | `services/recommendations.build(RecommendationContext)` | recommandations contextuelles ; la partie « existe-t-il une lettre pour cette offre ? » est ajoutée par la couche API (qui a la session) |
| `api/jobs.py` | `JobApplicationService`, `document_service`, freshness | façade HTTP fine — valide auth / ownership puis délègue |

Aucun `httpx`/`requests` backend→backend. `search_async.py` (le seul endroit qui
avait conceptuellement une forme « un service appelle l'API ») est supprimé.

---

## E. Workflow de l'agent

```
USER
 │  "Je cherche des postes Backend Python à distance en France, 3 ans d'xp"
 ▼
JobConversationAgent.handle
 │  1 call_gemini(request_type="conversation_agent", json_schema=ConversationDirective)
 │  → { reply, intent:"search_jobs", search_patch:{query,remote_type,country,...} }
 ▼
merge (profil persistant ⊕ search_patch ⊕ enrich local déterministe)
 ▼
JobSearchService(db).search(...)            [Python interne]
 ▼
top-N → SavedJob(origin="agent")  +  usage_event "search"
 ▼
MessageSendResponse { assistant_message, jobs_found:[SelectedJob], recommended_actions }
 ▼
DASHBOARD  (GET /api/dashboard/jobs)
```

### Frontière de confirmation (constante)

```
"Quels jobs as-tu trouvés ?"        → lecture seule (chat)
"Le job X est intéressant"          → discussion / recommandation
"Postule au job X" / "à tout" / "aux offres 1 et 3"
        → intent=apply_jobs : l'agent PROPOSE
          → Conversation.pending_action = {kind:"apply", job_ids:[...], proposed_at}
          → reply "Je vais préparer A, B, C — confirme ?" + recommended_action
          → pending_confirmation dans la réponse ; RIEN n'est appliqué
"oui" / "confirme" / "vas-y"  (message suivant, détecté DÉTERMINISTIQUEMENT)
        → JobApplicationService.apply() pour EXACTEMENT le set figé
          → applications:[ApplyOutcome] ; pending_action effacé
tout autre message              → pending_action effacé (proposition abandonnée)
```

- Le LLM ne peut **jamais** déclencher une candidature. `intent=apply_jobs`
  n'est qu'une proposition.
- Le set de `job_ids` est **figé au moment de la proposition** par le LLM ;
  le tour de confirmation exécute ce set exact, sans reconsulter le LLM.
- `_is_confirmation()` : liste fermée FR+EN, message court (≤ 40 car.), rejeté
  s'il contient une réserve (« mais », « avant », « pas »…).
- Une proposition périmée (> `AGENT_PENDING_ACTION_TTL_MIN` = 30 min) est ignorée.
- `request_information` : une vraie question (`recommended_action` de type
  `request_information` avec `question`/`reason`) — **jamais** une mutation.
  Le CV/la sélection ne change qu'après une information claire de l'utilisateur.

### Exemples de questions de l'agent

> « L'offre demande 5 ans d'expérience en Kubernetes, mais cette information
> n'apparaît pas dans votre profil. Utilisez-vous Kubernetes ? Si oui, dans
> quel contexte et depuis quelle période ? »

---

## F. Workflow du dashboard

```
Agent cherche ─► JobSearchService ─► offres retenues (SavedJob origin="agent")
        │
        ▼
GET /api/dashboard/jobs
  → [ SelectedJob { titre, entreprise, localisation, source_url, résumé,
                    skills, freshness, match_score, selected_at,
                    application: {status, cv_id, letter_id, url} | null } ]
        │
   application == null ──► bouton [ Apply ]
        ▼
POST /api/jobs/{job_id}/apply   (façade publique)
        ▼
API : auth + ownership CV/lettre
        ▼
JobApplicationService.apply(...)   ──►  JobApplication enregistrée
        ▼
résultat exploitable par le frontend (application_status, application_url, …)
```

Et via l'agent :

```
USER → Agent : "postule à toutes les offres sélectionnées"
        ▼
Agent : PROPOSE (pending_confirmation) — rien n'est envoyé
        ▼
USER : "oui"
        ▼
validation déterministe (pending_action figé, message = confirmation)
        ▼
JobApplicationService.apply()  × N   (isolé par offre)
        ▼
applications:[ApplyOutcome]  (partiel géré : une offre KO n'annule pas les autres)
```

`SavedJob` (table de sélection unifiée) a gagné 3 colonnes additives :
`origin` (`"agent"|"manual"`, `server_default "agent"`), `match_score`,
`conversation_id`. `Conversation` a gagné `pending_action` (JSON, nullable).
Migration : `database._ensure_additive_columns` (ALTER additif) — **vérifié sur
PostgreSQL 16**.

---

## G. Recommandations (correction de la Phase 5)

Phase 5 avait sur-corrigé : recommandations **limitées aux missing skills**.
Phase 6 rétablit un vocabulaire complet, mais **contextuel** — une
recommandation n'apparaît que si elle a une vraie valeur.
`services/recommendations.py` (NOUVEAU, déterministe, sans effet de bord) :

| type | émis quand |
|---|---|
| `review_match` | JD présente ET ATS < 50 |
| `improve_summary` | JD présente ET ATS < 60 ET résumé court (< 25 mots) |
| `review_experience` | JD présente ET (une expérience a 0 bullet OU ATS < 55) |
| `confirm_skill` (par skill) | `skill_analysis.missing` non vide — garde la question intelligente de la Phase 5 |
| `review_uncertain_skill` | `skill_analysis.uncertain` non vide |
| `generate_targeted_cv` | JD présente ET aucun CV ciblé sur cette JD |
| `generate_cover_letter` | JD / offre cible sans lettre |
| `review_cover_letter` | lettre existante mais CV plus récent depuis |
| `review_selected_jobs` | ≥ 3 offres sélectionnées, 0 candidature |
| `apply_to_selected_jobs` | ≥ 1 offre non postulée ET l'utilisateur n'a pas déjà commandé |
| `review_job` | offre sélectionnée `stale` / match faible |
| `request_information` | l'agent a besoin d'un fait pour continuer (porte `question`) |

- ATS 91 / 0 missing / pas de lettre → **uniquement** `generate_cover_letter`.
- ATS 42 / missing Kubernetes / résumé faible → `review_match` +
  `confirm_skill` + `improve_summary` (+ `review_experience`) — rien d'autre.
- Jamais la liste fixe `review_match / generate_cv / generate_letter / download_cv`.
- Une recommandation reste une **guidance** rendue comme bouton/prompt —
  jamais une action exécutée.

`services/cv/skill_analysis.py::recommended_actions` **supprimée** (déplacée).

---

## H. Tests

| | Avant P6 | Après P6 |
|---|---|---|
| Total | 1211 | **1228 passed, 1 skipped** (0 échec, 0 erreur, run mono-processus) |
| Nouveaux fichiers | — | `tests/test_recommendations.py` (15), `tests/test_dashboard_jobs.py` (8), `tests/test_job_agent.py` (13) |
| Supprimé | — | `tests/test_job_search_async.py` (12) |
| Réécrits | — | `tests/test_jobs_api.py`, `tests/test_e2e_workflow.py` (recherche via l'agent), `tests/test_job_preference_extraction.py`, `tests/test_conversation_agent.py`, `tests/test_structured_output.py`, `tests/test_architecture_imports.py`, `tests/test_security_v26.py` |

### Agent / jobs
- l'agent lance `JobSearchService` en interne (asserté via l'effet : offres
  persistées + présentes dans `/api/dashboard/jobs`), **pas** via HTTP.
- la recherche n'est plus atteignable par `POST /api/jobs/search` (404).
- `POST /api/jobs/{id}/apply` appelle réellement `JobApplicationService.apply`
  (asserté via `JobApplication` en base).
- un utilisateur ne peut pas postuler au job d'un autre (403 CV/lettre ;
  `GET /api/jobs/{id}` hors sélection → 404).
- l'agent ne peut pas appliquer sans la confirmation déterministe
  (`intent=apply_jobs` seul ⇒ 0 `JobApplication`).
- « postule à tout » et « postule aux offres 1 et 3 » fonctionnent **après**
  confirmation ; le tour de confirmation n'appelle pas le LLM.
- candidature partielle : une offre inexistante dans le set figé → `failed`,
  les autres passent.
- un utilisateur B ne peut pas confirmer la proposition de A (403 sur la
  conversation de A).

### Recommandations
- réellement contextuelles ; aucune liste générique.
- missing skill → `confirm_skill` avec la question intelligente ;
  `missing` ≠ « ne sait pas faire ».
- information insuffisante → `request_information` (avec une vraie question).
- recommandation ≠ exécution (`build` ne prend qu'un dataclass, aucun effet).

### API cleanup
- toutes les routes conservées répondent ; toutes les routes supprimées → 404 ;
  aucune n'apparaît dans l'OpenAPI (`test_architecture_imports.py`).
- aucun import mort, aucun `search_async` restant, aucun appel HTTP interne.

### E2E
- `test_e2e_workflow.py::test_full_candidate_workflow` : signup → conversation
  « trouve-moi des postes… » → recherche agent → `/api/dashboard/jobs` →
  détail → CV ciblé → lettre → anti-hallucination → skill analysis →
  `confirm_skill` → apply → dashboard.
- `test_e2e_workflow.py::test_e2e_agent_jobs_search_propose_confirm_apply` :
  recherche → dashboard → « postule aux offres 1 et 2 » → **proposition**
  (rien appliqué) → « oui » → apply du set figé → dashboard reflète les
  candidatures → utilisateur B ne voit rien / ne peut pas confirmer.
- owner-scoping A vs B, versioning, atomicité lettre : inchangés (offres
  seedées directement).

### Docker
`docker compose build && up` (API + PostgreSQL 16 + Redis 7 + MinIO) :
`_ensure_additive_columns` ajoute proprement
`saved_jobs.{origin,match_score,conversation_id}` + `conversations.pending_action` ;
`GET /api/health` 200 ; E2E curl conversation→recherche→`/api/dashboard/jobs`→
propose→confirm→apply ; les routes supprimées renvoient 404 dans l'OpenAPI live.
*(résultats détaillés : voir §I)*

---

## I. Résultats Docker / E2E

`docker compose build` OK · `docker compose up -d` (API + PostgreSQL 16 +
Redis 7 + MinIO), port hôte 8020 (override temporaire, supprimé après) :

**Migration (Postgres 16, `_ensure_additive_columns`)** — au démarrage, sans erreur :
```
[Database] Added column conversations.pending_action
[Database] Added column saved_jobs.origin
[Database] Added column saved_jobs.match_score
[Database] Added column saved_jobs.conversation_id
```
`GET /api/health` → 200.

**Contrat API (OpenAPI live)** — `/api/jobs/search`, `/api/jobs/search/{search_id}`,
`/api/jobs/context`, `/api/jobs/sources`, `/api/stats` **absents** ;
`/api/dashboard/jobs` **présent**. En live, `POST /api/jobs/{search,context,sources}`
→ 405 (collision avec `GET /api/jobs/{job_id}`), `/api/stats` → 404.

**E2E agent (LLM réel + providers réels)** :
1. `POST /api/conversations/{id}/messages` « Trouve-moi des postes backend
   Python en remote, junior » → `intent=search_jobs`, `JobSearchService` exécuté
   en interne, **10 offres réelles retenues** (`jobs_found: 10`), persistées en
   `SavedJob(origin="agent")`.
2. `GET /api/dashboard/jobs` → 10 offres, `application: null` (bouton Apply).
   `recommended_actions` = `review_selected_jobs`, `apply_to_selected_jobs`,
   `request_information` (contextuel : ≥ 3 sélectionnées, 0 candidature).
3. « postule aux offres 1 et 2 » → **proposition uniquement** :
   `pending_confirmation.job_ids` = 2 ids figés, `applications: []`,
   **0 candidature en base**.
4. « oui » → confirmation déterministe → 2 candidatures créées
   (`requires_user_action`, `prepared`), **2 candidatures en base**.
5. `GET /api/dashboard/jobs` → les 2 offres portent désormais leur
   `application.status`.

`docker compose down` propre.

---

## J. Performance

- **Aucune dégradation.** Le tour de conversation fait **un seul** `call_gemini`
  (la directive), comme avant. `JobPreferenceExtractor.enrich` est **sans LLM**.
- `JobSearchService` est appelé en Python direct : on économise le round-trip
  HTTP + la sérialisation JSON que faisait `POST /api/jobs/search`.
- `Conversation.pending_action` : 0 requête supplémentaire (la conversation est
  déjà chargée par `get_owned`).
- `SavedJob` +3 colonnes : lecture/écriture inchangées en coût.
- `GET /api/dashboard/jobs` : 3 SELECT (sélection paginée + offres + dernières
  candidatures), bornés par `limit`.
- Recommandations : purement déterministes, en mémoire, ~µs.

---

## K. Fichiers

**Nouveaux** : `schemas/conversation_directive.py`, `schemas/dashboard_jobs.py`,
`services/conversations/job_agent_service.py`, `services/recommendations.py`,
`tests/{test_job_agent,test_recommendations,test_dashboard_jobs}.py`,
`benchmarks/PHASE_6.md`.

**Modifiés** : `api/jobs.py` (−5 routes, scope `GET /{id}`), `api/profile.py`
(+`GET /api/dashboard/jobs`), `api/conversations.py`, `api/cv.py`
(`_augment_cv_recommendations`), `main.py` (−`/api/stats`, −hook lifespan),
`services/conversations/conversation_service.py` (dispatch + `SendResult`),
`services/user_dashboard_service.py` (`selected_jobs`),
`services/generation_service.py` (recommendations),
`services/cv/skill_analysis.py` (−`recommended_actions`),
`services/jobs/preference_service.py` (+`enrich` sans LLM),
`models/{conversation,saved_job}.py`, `schemas/{conversation,jobs,skill_analysis}.py`,
`config.py` (`AGENT_JOB_SELECTION_LIMIT`, `AGENT_PENDING_ACTION_TTL_MIN`),
`tests/conftest.py`, plusieurs fichiers de tests, `API_FRONTEND_GUIDE.tex`,
`PROJECT_STRUCTURE.md`.

**Supprimés** : `services/jobs/search_async.py`, `tests/test_job_search_async.py`.

---

## L. Points restant à traiter (état à la fin de Phase 6)

- ~~`PROJECT_STRUCTURE.md` est globalement pré-Phase-4~~ → **résolu en
  finalisation** (§M) : réécriture complète, arborescence réelle.
- `gemini_client.get_stats()` est conservée (utilitaire interne) même si plus
  exposée en HTTP — utilisée par l'admin dashboard.

---

## M. Finalisation de Phase 6 (2026-09-13)

Quatre objectifs, appliqués sur l'état exact de la Phase 6 ci-dessus, sans
rien retirer des invariants A→L.

### M.1 — IDs métier en UUID

**Audit d'abord** : tous les modèles (`User`, `Conversation`, `Message`,
`JobOffer`, `JobApplication`, `SavedJob`, `GeneratedCV`, `GeneratedLetter`,
`AtsBoard`, `PasswordResetCode`, `ReactivationCode`, `UsageEvent`)
utilisaient **déjà** `id: Mapped[str] = mapped_column(String(36),
primary_key=True, default=lambda: str(uuid4()))` — aucun entier
auto-incrémenté, aucune migration de données nécessaire. Le vrai travail :

- `services/id_utils.py` (nouveau) — `is_valid_uuid()` / `require_uuid_or_404()`,
  posé aux points de passage où un id de propriété est résolu :
  `ConversationService.get_owned`, `JobApplicationService.{apply,
  get_application, save_job}`, `api/jobs.py::_offer_in_scope_or_404`,
  `AdminService.{get_user, get_board}`. Un id mal formé est rejeté **avant**
  toute requête DB, avec **exactement le même** `404` qu'avant (jamais un
  `422` — un changement de contrat d'API non sollicité aurait cassé
  `test_cv_unknown_id_is_404`, `test_get_missing_conversation_404`,
  `test_get_unknown_job_404`, etc.).
- Schémas Pydantic : les champs id **terminaux** (jamais réutilisés comme
  donnée de travail après construction — `UserPublic.id`, `ConversationOut.id`,
  `MessageOut.id`, `MessageSendResponse.conversation_id`,
  `PendingConfirmation.job_ids`, `CVListItem/LetterListItem/
  DocumentVersionItem.id`, `GenerateCV/LetterJsonResponse.*_id`, les lignes
  admin, `UserProfileOut.user_id`, `UserDashboardResponse.user_id`) sont
  typés `UUID`. **Exception documentée et testée** : `JobOfferOut.id`,
  `SelectedJobOut`/`SelectedJobApplication`/`ApplyOutcome`,
  `ApplicationOut`/`ApplicationResponse`/`SavedJobOut`/`ApplyRequest`
  restent `str` — ces objets sont réutilisés comme **données de travail
  internes** dans `job_agent_service.py` (clés de dict comparées à des
  colonnes SQLAlchemy `String(36)`, valeurs écrites dans une colonne JSON) ;
  un `uuid.UUID` y casse la sérialisation JSON et le binding SQLite/Postgres
  (constaté en cours de développement, corrigé en revenant à `str` — exactement
  le risque contre lequel l'énoncé de l'objectif prévenait : « ne fais pas un
  remplacement global »).
- `tests/test_business_ids_are_uuid.py` (nouveau) : chaque modèle + ses FK,
  le `sub` du JWT, les ids renvoyés par l'API (signup, conversation, recherche
  agent) sont UUID-parsables ; un ordinal d'affichage ("1") n'est jamais un id
  valide ; un id de chemin mal formé reste `404`, jamais `422`.

### M.2 — Confirmation stricte et localisée (oui/non · yes/no)

- `job_agent_service._propose_apply` ajoute désormais **toujours**, en code
  (jamais laissé au LLM), la phrase :
  - FR : « … Voulez-vous continuer ? Répondez uniquement par « oui » ou « non ». »
  - EN : « … Would you like to continue? Please reply only with "yes" or "no". »
  La langue est choisie par `_detect_lang()` — heuristique locale à base de
  marqueurs FR/EN sur le dernier message utilisateur, **sans appel LLM** (donc
  ni contournable, ni un second call_gemini).
- `_is_confirmation()` durci : `_CAVEAT_RE` reconnaît maintenant aussi
  "attends"/"wait"/"stop"/"non"/"no" en plus de "mais/but/avant/before/…" — 
  "oui attends" (sans virgule) n'est plus confondu avec une confirmation.
- La décision reste 100 % Python : sur un tour de confirmation, le LLM
  **n'est pas appelé** (`test_confirmation_turn_applies_exactly_the_frozen_set`
  fait littéralement planter le test si le LLM est sollicité).

### M.3 / M.5 / M.6 — Pagination 5→5→5 + résolution d'indice → UUID réel

- Nouvelle colonne additive `Conversation.job_browse_state` (JSON, nullable —
  `{"ordered_job_ids": [...], "offset": int, "page_size": 5}`), migrée comme
  toutes les colonnes Phase 6 via `database._ensure_additive_columns` (zéro
  Alembic).
- `AGENT_JOB_SELECTION_LIMIT` relevé de `10` à `25` (nouveau
  `AGENT_JOB_PAGE_SIZE = 5`) — assez pour couvrir 5 lots de 5 sans perdre la
  capacité de sélection/candidature existante.
- Nouvel intent fermé `list_jobs` sur `ConversationDirective` : le LLM se
  contente de reconnaître « l'utilisateur veut (re)voir des jobs » (dans
  n'importe quelle formulation FR/EN) — il ne voit jamais la liste et ne
  calcule rien. Le backend (`_search` / `_list_jobs`) fait tout le travail
  déterministe : `_search` persiste l'ordre complet du classement dans
  `job_browse_state`, renvoie la 1ʳᵉ tranche de 5 (jamais la liste complète,
  même si la recherche brute en a trouvé 50) et avance déjà l'offset ;
  `_list_jobs` tranche `ordered_job_ids[offset:offset+page_size]`, avance
  l'offset, **sans jamais** rappeler `JobSearchService` ni recalculer
  `job_ranking.score`. Un lot vide au-delà de la fin est géré sans erreur.
- `_resolve_apply_targets` / `_propose_apply` résolvent désormais les
  ordinaux ("offre 1") contre `job_browse_state.ordered_job_ids` (l'ordre
  **réellement montré**) quand il existe, au lieu de l'ordre
  `SavedJob.created_at.desc()` — qui pouvait diverger de ce que l'utilisateur
  voyait. Le prompt de l'agent (`_build_prompt`) utilise la même source pour
  que la numérotation vue par le LLM corresponde à celle que le backend
  résout. Le set résolu (UUID réels) est figé dans `pending_action.job_ids`
  **avant** toute confirmation ; une recherche ultérieure écrase
  `job_browse_state` mais ne change jamais le sens d'un `pending_action` déjà
  figé.
- `tests/test_job_pagination_and_confirmation.py` (nouveau) : 15 offres → 3
  lots de 5 sans doublon/sans saut/ordre stable/aucune re-recherche ; < 5
  offres ; "postule à l'offre 1" → `pending_action.job_ids` contient le vrai
  UUID (jamais `"1"`) → "oui" applique exactement cet UUID.

### M.4 — Recherche agent réellement asynchrone

Avant (déjà en place depuis Phase 6, inchangé) :
```
JobSearchService.search()  (sync)
  └─ _fan_out()  — ThreadPoolExecutor partagé, concurrent.futures.wait(),
                   UNE deadline partagée, 20 providers en parallèle par thread
```
Après (ajouté, additif) :
```
JobConversationAgent._search()  (async def)
  └─ await JobSearchService.search_async()  (async def, nouveau)
       └─ await _fan_out_async()  — MÊME pool de threads partagé, mais chaque
                                     appel provider passe par
                                     loop.run_in_executor(pool, provider.search)
                                     et est attendu via asyncio.wait(...,
                                     return_when=FIRST_COMPLETED)
```
- `search()` (sync) reste **strictement inchangée** — c'est ce qu'utilisent
  encore tous les autres appelants et les ~15 fichiers de tests existants qui
  appellent `JobSearchService(db).search(...)` directement. `search_async()`
  est purement additive.
- Le tour de calcul déterministe (dédoublonnage, upsert DB, fraîcheur,
  classement, cache, pagination) est **factorisé** dans une seule méthode
  privée `_finish()` partagée par `search()` et `search_async()` — zéro
  duplication de la logique métier entre les deux chemins.
- Aucun nouveau provider n'est devenu async, aucun `httpx.AsyncClient`,
  `services/providers/http.py` reste l'unique chokepoint HTTP sortant,
  SQLAlchemy reste synchrone. `JobConversationAgent.handle` /
  `ConversationService.send_message` / `POST /api/conversations/{id}/messages`
  deviennent `async def` pour porter le `await` jusqu'à la route — l'agent
  d'édition de document (`EditAgent`, synchrone) est appelé tel quel à
  l'intérieur, inchangé.
- Aucun appel HTTP interne réintroduit : `JobConversationAgent` appelle
  toujours `JobSearchService` en Python direct, jamais `/api/jobs/search`
  (route toujours absente — `test_import_main_wires_every_router`).
- Aucun nouveau provider LLM ; `services/llm/providers.py` /
  `OpenAICompatibleProvider` inchangés ; aucun import de `services/llm/`
  depuis `job_agent_service.py` ou `search_service.py` (asserté par
  `tests/test_job_search_async.py::test_no_new_llm_provider_was_introduced`).
- **Mesure** (`tests/test_job_search_async.py`) : 5 providers fictifs à
  0,25 s de latence chacun → séquentiel ≈ 1,25 s, chemin async mesuré < 0,625 s
  (< 2,5× la latence d'un seul provider) — la concurrence est réelle, pas
  cosmétique. Un provider qui crashe ou dépasse la deadline partagée reste
  isolé (comportement identique au chemin sync).

### M — Fichiers (finalisation)

**Ajoutés** : `services/id_utils.py`, `tests/test_business_ids_are_uuid.py`,
`tests/test_job_pagination_and_confirmation.py`, `tests/test_job_search_async.py`
(nouveau fichier de même nom que l'ancien supprimé en §C — contenu totalement
différent : teste `search_async`, pas l'ancienne API HTTP de polling).

**Modifiés** : `models/conversation.py` (+`job_browse_state`),
`config.py` (`AGENT_JOB_SELECTION_LIMIT` 10→25, +`AGENT_JOB_PAGE_SIZE`),
`schemas/{auth,profile,dashboard,conversation,conversation_directive,cv,admin}.py`
(durcissement `UUID` ciblé), `schemas/{jobs,dashboard_jobs,applications}.py`
(commentaires explicites sur le choix de garder `str`),
`services/conversations/job_agent_service.py` (confirmation FR/EN, `list_jobs`,
pagination, résolution d'ordinal, `async def`),
`services/conversations/conversation_service.py` (`send_message` → `async def`,
`get_owned` → validation UUID), `services/jobs/search_service.py`
(`search_async` + `_fan_out_async` + `_finish` factorisé),
`services/jobs/application_service.py` (validation UUID sur `apply`/
`get_application`/`save_job`), `services/admin_service.py` (validation UUID sur
`get_user`/`get_board`), `api/jobs.py` (validation UUID + import),
`api/conversations.py` (route `async def`), `tests/test_architecture_imports.py`
(+`id_utils` dans la liste blanche), `README.md`, `PROJECT_STRUCTURE.md`
(réécriture complète — arborescence réelle).

**Supprimés** : aucun.

---

## N. Phase 7 — Recommandations intelligentes

**Déplacée dans son propre fichier : voir `benchmarks/PHASE_7.md`.**
Résumé : remplacement du moteur de recommandations à liste fermée par un
système contextuel/extensible pour le chemin conversationnel uniquement
(`JobConversationAgent`), 0 appel LLM supplémentaire (les recommandations
voyagent dans le même `ConversationDirective`), `services/recommendations.py`
conservé intact pour `/api/generate-cv`. L'audit qui suit et corrige cette
phase reste ci-dessous, en §O.

---

## O. Phase 8 — Audit qualité des recommandations (2026-09-13)

Audit critique du moteur de recommandations Phase 7 : couverture multi-sujets
réelle, matrice FR/EN, cohérence, anti-hallucination, faux positifs de test,
et **un vrai appel LLM** en complément des tests mockés.

### O.1 — Corrections apportées (trouvées puis corrigées)

1. **Aucune vérification de cohérence linguistique** entre le texte d'une
   recommandation et la langue de la conversation. **Corrigé** :
   `recommendation_engine._detect_text_lang()` (heuristique locale, aucun
   appel LLM) + `validate_and_rank(..., language=...)` — une recommandation
   dont le texte est détecté avec confiance dans l'autre langue est rejetée ;
   un libellé court (signal trop faible) n'est jamais pénalisé.
2. **Aucune détection de contradiction/doublon intra-lot** : le LLM pouvait
   renvoyer à la fois `generate_cover_letter` et `review_cover_letter` pour
   la même offre dans le même tour. **Corrigé** : une passe de
   déduplication par catégorie de document (`_doc_category` — cv/lettre)
   après tri, qui ne garde que la recommandation la mieux classée par
   `(target_type, target_id, catégorie)`.
3. **Trois tests à faux positif corrigés** (auto-détectés, §32) :
   - `test_there_is_no_dispatcher_...` (Phase 7) échouait sur lui-même : sa
     propre regex de vérification contenait la sous-chaîne `"exec("` en tant
     que texte de motif, pas en tant qu'appel réel — corrigé pour vérifier
     un appel réel (début de ligne) plutôt qu'une sous-chaîne brute.
   - `test_recommendation_engine_signature_takes_no_db_write_capability...`
     figeait la signature de `validate_and_rank` sans anticiper l'ajout
     (additif, mot-clé, valeur par défaut `None`) du paramètre `language` —
     mis à jour pour refléter l'extension volontaire.
   - `test_recommendation_engine_has_no_ats_score_threshold_logic` vérifiait
     l'absence totale de la sous-chaîne `"ats_score"` alors que
     `build_context` l'affiche légitimement (ce n'est pas une règle à seuil) —
     corrigé pour vérifier l'absence d'une **comparaison** (`<`, `>=`, …) sur
     `ats_score`, pas l'absence du nom du champ.

### O.2 — Suivi des 4 constats initiaux (tous traités)

Les 4 points relevés lors du premier audit ont chacun reçu soit une
correction complète, soit une correction partielle assumée avec son
périmètre résiduel documenté explicitement (aucun n'est resté sans action).

1. **[MEDIUM] Recommandations calculées avant l'exécution de la branche
   d'intent → corrigé pour le cas le plus fréquent (backfill).**
   `handle()` calcule `recs` à partir de `directive.recommendations` avant
   `_search()`/`_propose_apply()`/`_list_jobs()`, donc une suggestion
   générique orientée « offre » (ex. `review_top_match`) revient forcément
   avec `target_id=None` — le LLM ne pouvait pas connaître l'UUID d'une
   offre pas encore trouvée. **Correction** : `_backfill_top_job_target()`
   résout ce pointeur vers la vraie meilleure offre juste après la
   recherche, sans second appel LLM et sans inventer de texte (uniquement
   la résolution d'un id vers une donnée déjà réelle). Reste non couvert :
   une recommandation qui viserait spécifiquement une offre autre que la
   meilleure (offre n°3 par exemple) sur ce même tour — cas marginal, le
   rattrapage au tour suivant reste la voie normale.
2. **[LOW] `_contradicts_existing_state` ne couvrait que les lettres déjà
   attachées à une `JobApplication` → corrigé.** Ajout d'une vérification
   par `job_hash` (même digest que celui écrit par
   `application_service._prepare_letter`, calculé sur
   `offer.description or offer.title`) pour détecter une `GeneratedLetter`
   autonome existante, non encore liée à une candidature. Affiné pour ne
   bloquer que les actions de type CRÉATION (`generate`/`create`/`write`/
   `draft`/`rédiger`/`créer`/`écrire`) — une action de type REVUE
   (`review_cover_letter_tone`, etc.) reste légitime même si une lettre
   existe déjà, et n'est plus supprimée à tort.
3. **[LOW] Aucune détection de « mauvais contexte » → corrigé pour le cas
   explicite, résiduel documenté pour le cas implicite.** Quand le message
   de l'utilisateur nomme explicitement une offre par un numéro (« l'offre
   2 », « job #3 », « la deuxième »/« la deuxieme »), c'est une vérité
   terrain que le backend peut résoudre déterministiquement — exactement
   comme `_propose_apply` le fait déjà pour « postule à l'offre 2 ». Ajout
   de `_extract_explicit_job_ordinal()` (insensible aux accents) qui
   **remplace** le `target_id` deviné par le LLM par l'id réellement désigné
   par le chiffre/mot ordinal de l'utilisateur. **Reste non détectable** :
   une référence implicite sans numéro (« cette offre », « celle dont on
   parlait tout à l'heure ») — nécessiterait un suivi d'état de « focus
   courant » que l'architecture actuelle ne maintient pas ; documenté comme
   limite résiduelle, pas comme un bug ignoré.
4. **[LOW] Anti-hallucination du texte libre → corrigé pour le cas
   structuré, résiduel documenté pour le texte libre.** Le contenu de
   `message`/`reason`/`title` reste, par nature, impossible à fact-checker
   déterministiquement (texte libre généré par un LLM — identique à
   `reply` depuis la Phase 4). **Correction apportée** : quand le LLM
   fournit `parameters.skill` et présente la recommandation comme un fait
   acquis (`requires_information=False`) sur une compétence absente du
   `UserProfile.skills` réel, `_downgrade_unconfirmed_skill_claim()` la
   requalifie automatiquement en question (`requires_information=True` +
   une vraie question) plutôt que de la laisser passer comme une
   affirmation non vérifiée. Une compétence confirmée par le profil, ou une
   recommandation déjà posée comme question, n'est jamais modifiée.

### O.3 — Preuve avec un vrai LLM (hors suite automatisée)

Deux appels réels (NVIDIA, routage actif), en dehors de pytest :

1. Message FR : « Cette offre me semble intéressante mais je n'ai jamais
   travaillé avec Kubernetes. » → le modèle a renvoyé **3 recommandations
   authentiquement différentes**, jamais vues dans l'ancien vocabulaire fermé :
   `ask_kubernetes_experience` (question réelle, `requires_information=true`),
   `adapt_cv_to_job`, `generate_cover_letter` — toutes acceptées par
   `validate_and_rank`.
2. Message FR, CV + lettre déjà présents, utilisateur satisfait : → **0
   recommandation** — le modèle n'a pas rempli artificiellement les 3 slots.

Ces deux sondes sont manuelles (script ponctuel), pas dans la suite pytest —
la suite reste 100 % déterministe (LLM mocké) par conception (§12 de la
mission : ne jamais dépendre d'un LLM réel pour la suite automatisée).

### O.4 — Fichiers

**Ajoutés** : `tests/test_phase8_recommendation_quality.py` (couverture
multi-sujets A→G, matrice FR/EN, cohérence, anti-hallucination, intégration
bout-en-bout, régression Phase 6, sonde LLM réel avec skip explicite si
indisponible, + les tests des 4 corrections ci-dessus).

**Modifiés** : `services/conversations/recommendation_engine.py`
(`_detect_text_lang`, `_doc_category`, `validate_and_rank(..., language=)`,
passe de collapse intra-lot, `_contradicts_existing_state` étendu au
`job_hash` d'une lettre autonome + distinction génération/revue,
`_downgrade_unconfirmed_skill_claim`), `services/conversations/job_agent_service.py`
(`_build_prompt` accepte `lang` en paramètre au lieu de le recalculer,
`handle()` calcule la langue une fois et la propage, `_backfill_top_job_target`,
`_extract_explicit_job_ordinal` + son câblage dans `handle()`),
`tests/test_intelligent_recommendations.py` (3 corrections de faux
positifs), `README.md`, `PROJECT_STRUCTURE.md`.

**Supprimés** : aucun.

### O.5 — Résultat des tests (état final)

```
PYTHONIOENCODING=utf-8 .venv_test/Scripts/python.exe -m pytest -q
```
Voir le rapport de conversation pour le résultat exact du run final —
exécuté après toutes les corrections ci-dessus, sans modification
concurrente du code pendant le run (leçon retenue des phases précédentes).
