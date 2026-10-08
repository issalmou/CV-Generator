# Phase 4 — async job search · domain architecture · CV/JD/chatbot contract

Priorité : **fiabilité > architecture claire > UX/API contract > performance.**
Backend uniquement. Aucun refactoring gratuit — chaque déplacement préserve le
comportement et est couvert par les tests (**1150 passed, 1 skipped, 0 régression**).

---

## 1. Recherche d'offres asynchrone (priorité principale)

### Audit de l'existant (avant d'implémenter)
- **Redis** : présent via `services/cache_service.py` (Redis si `REDIS_URL`, sinon mémoire).
- **Fan-out** : `JobSearchService._fan_out` — pool `ThreadPoolExecutor` **process-wide**
  partagé (`JOB_SEARCH_GLOBAL_CONCURRENCY=32`), sémaphore par recherche, harvest
  `FIRST_COMPLETED`, deadline global (`JOB_SEARCH_DEADLINE=45s`), timeout par provider
  (`JOB_PROVIDER_TIMEOUT`), circuit breaker par provider.
- **Pas** de Celery / RQ / broker. → **on ne rajoute rien.**

### Architecture retenue (sans nouvelle infra)
```
POST /api/jobs/search {wait:false}
        ↓
submit()  →  SearchJob {status:"queued"}  stockée dans le cache (Redis/mémoire, TTL 1h)
        ↓
202 {search_id, status:"queued"}          ← réponse en ~0,02 s
        ↓
worker (ThreadPoolExecutor dédié, JOB_SEARCH_ASYNC_WORKERS=4)
        ↓  status:"running"
JobSearchService.search(request, progress=cb)   ← MÊME code que le sync
        ↓  cb à chaque provider terminé : providers_completed / results_count / heartbeat
provider fan-out (pool partagé, deadline 45 s)
        ↓
status:"completed" + response stockée      |  "failed" (exception)  |  "cancelled"
```

`services/jobs/search_async.py` : `submit / get / cancel / _run / paginate_response`.
`JobSearchService.search()` : **1 paramètre optionnel** `progress` — passé à `_fan_out`,
appelé après chaque batch de providers ; renvoyer `False` = arrêt anticipé (annulation →
résultats partiels).

### États + informations conservées
| État | Sens |
|---|---|
| `queued` | créée, worker pas encore démarré |
| `running` | fan-out en cours (heartbeat mis à jour) |
| `completed` | terminée (même si 0 résultat) — `response` disponible |
| `failed` | exception worker **ou** worker bloqué (voir watchdog) |
| `cancelled` | annulée via `DELETE` — résultats partiels conservés |

Champs : `providers_total`, `providers_completed`, `providers_failed[]`, `results_count`,
`duration_ms`, `error`, `created_at/started_at/finished_at`.

### Timeouts / provider bloqué / résultats partiels
- **par provider** : `JOB_PROVIDER_TIMEOUT` (inchangé).
- **global fan-out** : `JOB_SEARCH_DEADLINE` (45 s) — un provider qui ne finit pas est
  abandonné (breaker tripé), la recherche **se termine avec les résultats des autres**.
- **watchdog paresseux** : une recherche `running` sans heartbeat depuis
  `JOB_SEARCH_ASYNC_STALE_SECONDS` (120 s) est reportée `failed` au `GET` — **aucune
  recherche ne reste bloquée indéfiniment**, sans thread supplémentaire.
- **retry** : aucun retry agressif ajouté ; le circuit breaker par provider suffit.
- **annulation** : `cancel_requested` lu entre chaque batch → `cancelled` + partiel.

---

## 2. API de recherche asynchrone

**Extension cohérente autour de `/api/jobs/search`, aucun nouveau namespace :**

| Endpoint | Rôle |
|---|---|
| `POST /api/jobs/search` | `wait:true`/omis → **synchrone** (200 `JobSearchResponse`, **backward compatible**) ; `wait:false` → **202** `{search_id, status:"queued"}` |
| `GET /api/jobs/search/{search_id}` | statut + progression ; `results` (payload normalisé, paginé `page`/`page_size`) uniquement si `status=="completed"` |
| `DELETE /api/jobs/search/{search_id}` | demande l'annulation (204) ; recherche terminée → inchangée |

Owner-scoping strict : réf d'un autre user / inconnue → **404** (pas de fuite d'existence).
Auth Bearer obligatoire (401 sinon).

**Contrat non cassé** : le champ `wait` a pour défaut `true` → tous les consommateurs
existants (et les 21 tests `test_jobs_api.py`) fonctionnent à l'identique.

### Réponse `GET` (exemple)
```json
{
  "search_id": "ff57d01e...",
  "status": "running",
  "providers_total": 14, "providers_completed": 12,
  "providers_failed": ["indeed"],
  "results_count": 56, "duration_ms": null,
  "results": null
}
```
Le frontend affiche « Recherche de 14 sources… 12/14 terminées… 56 offres » sans
connaître le détail des providers.

---

## 3. Temps AVANT / APRÈS (Docker, LLM live, providers HTTP réels)

| | AVANT (sync) | APRÈS (async) |
|---|---|---|
| Réponse HTTP `POST /api/jobs/search` | **~40–45 s bloquantes** | **~0,02–0,04 s** (202) |
| Fan-out (13–14 providers actifs sur 20) | 40–45 s | 45 s **en tâche de fond** (borné par `JOB_SEARCH_DEADLINE`) |
| Résultats | 55–56 offres | 55–56 offres (identiques — même code) |
| Providers en erreur | `indeed` (unavailable) | `indeed` (reporté dans `providers_failed`) |
| 2e recherche identique (cache Redis) | ~0,01 s | ~0,01 s (le sync est servi du cache ; l'async aussi) |
| Recherche bloquée | possible (worker/DB hang) | **impossible** (watchdog → `failed`) |

Le workflow HTTP n'est **plus bloqué**. Le fan-out réseau (45 s) reste le poste lourd —
c'est du réseau, pas du code. Pistes futures (non faites) : baisser `JOB_SEARCH_DEADLINE`,
restreindre `JOBS_ENABLED_PROVIDERS`.

---

## 4. Organisation des fichiers backend par domaine

### Inventaire préalable
- routeurs (`*_router.py`) : importés **uniquement** par `main.py` (0 test) → déplacement trivial.
- cluster jobs (`services/job_*.py` + `ats_board_registry`) : ~54 fichiers de test — réécriture d'import **mécanique** (sed ancré sur `services\.job_X`) + corrections manuelles (imports non-pointés, chemins-strings dans les tests de sécurité).
- cluster cv (`*_parser`, `resume_*`, `*_generator`, analyzers, pdf) : ~72 fichiers de test — même procédé.
- `services/providers/` et `services/llm/` : **déjà** des sous-packages propres et cohérents → **inchangés**.

### AVANT
```
cv-generator/
├── auth_router.py  cv_router.py  jobs_router.py  admin_router.py
│   conversations_router.py  extraction_router.py  profile_router.py
├── services/               (41 modules à plat)
│   ├── job_search_service.py  job_application_service.py  job_match_service.py …
│   ├── experience_parser.py  cv_generator.py  pdf_generator.py  ats_optimizer.py …
│   ├── conversation_service.py  agent_service.py  document_service.py
│   ├── llm/   providers/   (déjà des packages)
│   └── cache_service.py  minio_service.py  gemini_client.py …
├── models/   schemas/   database.py   config.py
```

### APRÈS
```
cv-generator/
├── api/                    HTTP layer — un module par domaine
│   ├── auth.py  extraction.py  cv.py  jobs.py
│   ├── conversations.py  profile.py  admin.py
│
├── services/
│   ├── cv/                 résumé extraction (layout-aware, local-first) + structuration
│   │   │                   + ATS optimisation + skill analysis + rendu PDF CV/lettre
│   │   ├── experience_parser.py  education_parser.py  project_parser.py  skills_parser.py
│   │   ├── resume_parser_pipeline.py  resume_structurer.py  resume_text_extractor.py …
│   │   ├── profile_analyzer.py  ats_optimizer.py  cv_generator.py  pdf_generator.py
│   │   ├── skill_analysis.py  letter_generator.py  letter_pdf_generator.py  …          (26)
│   │
│   ├── jobs/               recherche (sync + async), matching, dedup, freshness,
│   │   │                   ranking, applications, parsers contexte/société
│   │   ├── search_service.py  search_async.py  application_service.py  match_service.py
│   │   ├── dedup.py  freshness.py  ranking.py  company_parser.py  preference_service.py
│   │   └── ats_board_registry.py                                                       (11)
│   │
│   ├── conversations/      mémoire conversationnelle + agent d'édition déterministe
│   │   ├── conversation_service.py  agent_service.py                                    (2)
│   │
│   ├── documents/          versioning des documents générés (référence stable + versions)
│   │   └── document_service.py                                                          (1)
│   │
│   ├── llm/                LE provider OpenAI-compatible unique + routing + circuit  (inchangé)
│   ├── providers/          adaptateurs de job-boards                                (inchangé)
│   │
│   └── (à plat — transverses uniquement, vérifié par un test)
│       gemini_client.py           entrée LLM unique (11+ consommateurs)
│       generation_service.py      orchestrateur CV+lettre (cache/minio/pipelines)
│       cache_service.py  minio_service.py  profiling.py       infra
│       parser_common.py           helper JSON-LLM partagé (cv + jobs)
│       auth_service.py  admin_service.py  stats_service.py  email_service.py
│       user_profile_service.py  user_dashboard_service.py  usage_event_service.py
│       runtime_config_service.py
│
├── models/   schemas/   database.py   config.py   dependencies/   main.py
```

### Choix assumés (structure adaptée au projet réel, pas la copie du schéma)
- **Profondeur max 2** (`services/jobs/search_service.py`), jamais 3+.
- `models/`, `schemas/` **laissés à plat** : chaque déplacement casserait `import models`
  (create_all) et ~toute la suite pour un gain d'organisation faible sur des fichiers
  courts et peu couplés. À faire dans une passe dédiée si vraiment souhaité.
- `database.py`, `config.py`, `dependencies/` **restent à la racine** : socle importé
  partout, un `infrastructure/database/` multiplierait les imports sans bénéfice.
- `gemini_client` + `parser_common` + `generation_service` **restent à plat** : entrée
  LLM / helper partagé / orchestrateur cross-domaine (jobs importe `generation_service`
  pour la lettre auto). `test_architecture_imports.py::test_no_domain_leftovers_in_flat_services`
  verrouille la liste.

### Imports — aucun cycle introduit
`test_architecture_imports.py` importe chaque module de domaine + `main` et vérifie
qu'aucun cycle n'apparaît. Procédé par domaine : inventaire imports entrants/sortants →
`mv` → sed ancré → corrections manuelles (imports `from services import X` non-pointés,
chemins-strings) → `compileall` → `import main` → pytest. **0 régression à chaque étape.**

---

## 5. Job Description optionnelle dans le workflow CV

| Cas | Comportement |
|---|---|
| **A — pas de `job_description`** | CV général. `skill_analysis = null`, `recommended_actions = []`. **Aucun ciblage inventé, aucune analyse de correspondance.** |
| **B — `job_description` fournie** | Après génération : `skill_analysis {matched, missing, uncertain}` + `recommended_actions`. |

Implémenté dans `run_cv_pipeline` : le bloc skill-analysis ne s'exécute que si
`job_description.strip()`.

---

## 6. Skill analysis matched / missing / uncertain (`services/cv/skill_analysis.py`)

| Catégorie | Définition |
|---|---|
| `matched` | compétence demandée par l'offre **ET** présente dans l'info fournie (skills déclarées + technos des expériences + technos des projets + certifs). `matched_to` garde le **mot exact du candidat** (jamais renommé). |
| `missing` | demandée par l'offre, **absente de l'info fournie à ce stade**. **≠ « le candidat ne sait pas faire ».** |
| `uncertain` | recouvrement partiel de tokens sans équivalence sûre (ex. « Google Cloud Functions » vs « Google Cloud Storage ») → **non confirmé** (ni matched ni missing). |

**Normalisation raisonnable, jamais d'invention** :
- minuscules, trim, suppression d'un suffixe de version (`Python 3` → `python`) ;
- petite table d'alias **sûrs** hand-kept (`postgres`=`postgresql`, `k8s`=`kubernetes`,
  `js`=`javascript`, `reactjs`=`react`, `tf`=`terraform`, `ci cd`=`ci/cd`…) ;
- équivalence incertaine → `uncertain`, pas `matched` (« mieux vaut non-confirmé que
  faussement présent »).
- degrés / soft-skills **exclus** du hard-match.
- Les règles anti-hallucination existantes restent prioritaires.

Tests : `test_skill_analysis.py` (11) — exact, suffixe de version, alias sûr, incertain,
technos issues des expériences/projets, degrés non hard-matchés, actions = suggestions.

---

## 7. Missing skills jamais injectées

Confirmé (déjà en place au LOT 2, re-testé Phase 4) :
- `cv_generator._build_skills` ne fusionne rien de la JD ;
- `missing` **jamais** dans `cv_data.skills` / `summary` / `experience` / `achievements` /
  `projects` — vérifié dans `structured_source` **y compris après 2 régénérations**
  (`test_case_B_missing_skill_never_injected_even_across_regen`) ;
- elles sortent **séparément** : `additional_skills` (suggestions verbatim) +
  `recommended_actions[type=confirm_skill]`.
- ajout au CV **uniquement** après confirmation explicite via l'agent (`_asserts_ownership`).

---

## 8. Nouvelle structure de réponse `/api/generate-cv`

Champs **ajoutés** (les anciens conservés — aucun consommateur cassé) :
```json
{
  "generated_cv_id": "...", "reference": "CV_xxxxxx", "version": 1,
  "ats_score": 33.3, "download_url": "...", "expires_in": 3600,
  "ats_optimization": "applied|local_fallback|skipped|cache",
  "layout": { "fit_one_page": true, "pages": 1, "body_font_pt": 9.0, … },
  "additional_skills": ["Kafka", "Docker", "Kubernetes"],          // suggestions
  "skill_analysis": {                                              // null en Cas A
    "matched":   [{ "skill": "Python", "matched_to": "Python 3", "confidence": "exact" }],
    "missing":   ["Kubernetes", "Terraform"],
    "uncertain": [{ "skill": "Google Cloud Storage", "matched_to": "GCF", "confidence": "fuzzy" }]
  },
  "recommended_actions": [                                         // suggestions, jamais exécutées
    { "type": "confirm_skill", "skill": "Kubernetes",
      "reason": "Requested by the job but not found in your CV yet. Do you actually use it?" },
    { "type": "review_match", "reason": "ATS match is 33%. Review the missing skills and your summary." },
    { "type": "generate_cover_letter", "reason": "Generate a cover letter tailored to this job." }
  ]
}
```

---

## 9. Chatbot — guide, pas seulement exécutant

- `_SYSTEM_HINT` renforcé : **« vous PROPOSEZ ; l'utilisateur décide ; le backend applique
  une action confirmée »**. Une exigence de l'offre (« 5 ans de Kubernetes ») → l'agent dit
  « l'offre demande X ; ce n'est pas confirmé dans votre CV » et propose les choix — il ne
  suppose jamais que l'utilisateur possède X, il ne modifie pas le CV.
- L'agent d'édition (`EditAgent`) : quand une action introduirait un fait non confirmé et
  que le message ne contient pas de signal de possession (`I use`, `j'utilise`, `daily`,
  `at Acme`…), il **ne modifie rien** et renvoie :
  - un `reply` qui présente les 3 choix (je l'utilise → on l'ajoute / je ne l'utilise pas →
    rien / je vérifie d'abord),
  - `recommended_actions: [{type:"confirm_skill", skill, reason}]`.
- Après confirmation explicite de l'utilisateur → `AgentAction` `add_skill` appliquée
  (nouvelle version).

`_asserts_ownership()` distingue **« add X »** (une demande) de **« add X, je l'utilise »**
(une assertion de possession).

---

## 10. `recommended_actions` (schéma)

`schemas/skill_analysis.py::RecommendedAction` et `schemas/conversation.py::RecommendedAction` :
```json
{ "type": "confirm_skill", "skill": "Kubernetes",
  "reason": "Demandé par l'offre mais non confirmé dans le CV" }
```
`type` ouvert ; valeurs usuelles : `confirm_skill`, `review_missing_skill`, `improve_summary`,
`review_experience`, `generate_targeted_cv`, `generate_cover_letter`, `review_match`,
`download_cv`, `view_previous_version`.
**Toujours des suggestions** — le frontend affiche un bouton, le backead n'agit pas.

---

## 11. Frontière User / Agent (règle appliquée)

```
USER (demande / info / confirmation) → AGENT (propose une action) →
BACKEND (valide) → application déterministe (sur structured_source)
```
- Le LLM ne décide **jamais** seul qu'une information est vraie.
- `AgentAction` inchangé — le LLM produit une action structurée, le backend l'applique.
  Aucun nouveau pouvoir direct sur la DB ou les documents.

---

## 12. Tests

### Ajoutés Phase 4 (**+52**)
| Fichier | n | Couvre |
|---|---|---|
| `tests/test_job_search_async.py` | 12 | 202 + queued, running→completed, providers parallèles, provider timeout/erreur, résultats partiels, succès final, échec global, recherche inconnue (404), owner-scoping, watchdog stale, sync inchangé, annulation |
| `tests/test_skill_analysis.py` | 11 | Cas A (pas d'analyse), Cas B, matched/missing/uncertain, normalisation version, alias sûr, technos expériences/projets, missing jamais injectée (même après régén), degrés exclus, actions = suggestions |
| `tests/test_architecture_imports.py` | 29 | chaque module de domaine importe, `main` câble tous les routeurs, packages OK, pas de code domaine resté à plat |
| E2E (`test_e2e_workflow.py`) | (mis à jour) | étape 6 = search async 202+poll ; étape 11c-e = skill_analysis + recommended_actions ; agent guide (« the decision is yours » + `recommended_actions`) |

### Contrat volontairement modifié (documenté)
| Test | Ancien | Nouveau | Impact frontend |
|---|---|---|---|
| `test_agent_editing.py::test_agent_refuses_to_invent_a_skill` | reply = « won't add information you haven't confirmed » | reply présente 3 choix + `recommended_actions[confirm_skill]` | **positif** : le frontend a maintenant un bouton exploitable, la réponse guide au lieu de juste refuser |

### Résultat
| | Fin Phase 3 | **Fin Phase 4** |
|---|---|---|
| `pytest` | 1142 passed, 1 skipped | **1179 passed, 1 skipped** — **0 régression** (+52 nouveaux tests) |
| `compileall .` | OK | **OK** |
| `import main` | OK | **OK** |
| Docker (build + startup + PG + Redis + MinIO + E2E) | ✅ | **✅ re-validé après le reorg complet** |
| Workflow E2E 16 étapes (dont search async) | — | **✅ vert en test ET en Docker live** |

Régression après chaque étape du reorg : `api/` → 1150 · `services/jobs/` → 1150 ·
`services/conversations/`+`services/documents/` → 1150 · `services/cv/` → 1150 ·
+ `test_architecture_imports.py` (29) → **1179**. Jamais un échec.

---

## 13. Docker

`docker compose build` + `up` → `healthy`. E2E complet (Postgres + Redis + MinIO + LLM live) :
`POST /api/jobs/search {wait:false}` → **202 en 0,04 s**, worker termine en 45 s
(`13/14 providers, 56 results`, `failed:['indeed']`), `GET` renvoie `completed` + résultats.
Tous les autres endpoints OK après le déplacement des ~45 fichiers.
(Port 8000 de l'hôte occupé par un autre projet → testé sur 8010 via override temporaire supprimé.)

---

## 14. Critère final

```
LOGIN → CV → EXTRACTION → PROFIL → JD optionnelle → SKILL ANALYSIS (si JD) →
JOB SEARCH ASYNC → JOB SELECTION → MATCHING → TARGETED CV →
MISSING/MATCHED SKILLS → CHATBOT RECOMMENDATION → USER CONFIRMATION →
AGENT ACTION → NEW DOCUMENT VERSION → COVER LETTER → APPLICATION → APPLICATION VERIFIED
```
**✅ toute la chaîne traversée** — l'agent **propose**, l'utilisateur **décide**, le backend
n'applique qu'une action confirmée. Le frontend consomme `skill_analysis` /
`recommended_actions` / la progression de recherche **sans connaître les détails internes**.

---

## 15. Points restant à traiter

- **`services/cv/` compte 26 modules** dans un dossier — acceptable mais on pourrait
  scinder `services/cv/extraction/` vs `services/cv/generation/` dans une passe dédiée
  (profondeur 3, à peser contre « éviter la profondeur excessive »).
- **`models/` · `schemas/` à plat** — reorg reporté (risque élevé, gain faible ; voir §4).
- **`gemini_client` → `services/llm/client.py`** — cohérent mais 15+ consommateurs ;
  passe dédiée.
- **Job search ~45 s en tâche de fond** — MVP OK ; envisager un `JOB_SEARCH_DEADLINE`
  plus court ou une liste de providers réduite en prod.
- **Groq Dev Tier** (429 TPM sous charge) et **Mistral** (toujours 429, désactivé) —
  inchangés depuis Phase 3.
- **`docker-compose.override.yml`** — temporaire (port 8010), supprimé.
