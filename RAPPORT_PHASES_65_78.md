# Rapport final — Cycle v2.7

**Architecture LLM unifiée (anti-redondance) · dashboard par utilisateur · statistiques par période**

Phases 65 → 78. Exécution autonome, sans validation intermédiaire.
Base de départ : v2.6 (phases 43–64), 988 tests.

---

## 0. Résumé exécutif

| Axe | Avant (v2.6) | Après (v2.7) |
|---|---|---|
| Implémentations de provider LLM | **5 classes** (`Nvidia/OpenAI/Gemini/OpenAICompatible/CustomEndpoint`) sous-classant `OpenAILikeProvider` | **1 classe** `OpenAICompatibleProvider` + table de presets `config._LLM_BLOCKS` |
| Providers officiels | 5 (nvidia, openai, gemini, openai_compatible, custom) | **7** (+ `mistral`, `groq`) |
| `if provider == …` dans le code métier | 0 | 0 (inchangé — l'abstraction était déjà propre) |
| Dashboard utilisateur | ❌ aucun | ✅ `GET /api/dashboard`, 1 endpoint agrégé, strictement `current_user.id` |
| Stats superadmin par période | users / applications / documents | + **activité job** (recherches / vues / favoris par fenêtre) |
| Stats provider | runs / success / offers / p95 / health_note | + `offers_per_run`, `empty_result_rate` (dérivées, zéro nouvel état) |
| Tests | 988 | **1010** (+ 1 skip) |

Non-régression : `python -m compileall .` OK · `pytest -q` : 1010 passed, 1 skipped ·
`docker build --build-arg INSTALL_BROWSER=false` OK (586 MB) · smoke conteneur OK.

---

## 1. Audit de l'existant (Phase 65)

Consigné dans `REFACTOR_REPORT.md` §15.1–15.8. Constats clés :

- **15.1 — Redondance LLM (point n°1 de la mission).** `services/llm/providers.py`
  définissait 5 classes qui ne diffèrent **que par leur `__init__`** (base_url /
  key / model par défaut), toutes sous-classant la même implémentation réelle
  (`OpenAILikeProvider`, ~30 lignes). Exactement la redondance à supprimer.
- **15.2 — Code métier.** Vérifié : les 11 modules consommateurs appellent
  `call_gemini(prompt, request_type=…)`. **Zéro** branchement par provider hors
  de `services/llm/`. Rien à corriger côté métier — seule l'implémentation des
  providers était redondante.
- **15.4 — Pas de dashboard utilisateur.** `StatsService` ne fournissait que
  des vues globales (superadmin) ou paginées.
- **15.5 — `UsageEvent`** déjà minimal et suffisant (indexes couvrants présents).
- **15.6 — `_WINDOWS`** (day/week/month/year) déjà là, réutilisable tel quel.

Décisions D14–D17 (tableau en fin de §15).

---

## 2. Architecture LLM unifiée (Phases 66–67)

### 2.1 Une seule implémentation

`services/llm/providers.py` réécrit. **Une** classe :

```python
class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(self, *, name, base_url, api_key, models,
                 temperature, max_tokens, timeout): ...
    def _complete_one(self, model, system, prompt) -> str:
        # un seul appel : self._client.chat.completions.create(...)
```

Un « provider » = une entrée de `config._LLM_BLOCKS` :

```
nom               -> (api_key_attr, model_attr, base_url_attr, default_base_url, default_model)
nvidia            -> NVIDIA_*  , https://integrate.api.nvidia.com/v1        , (chaîne interne)
openai            -> OPENAI_*  , https://api.openai.com/v1                  , gpt-4o-mini
gemini            -> GEMINI_*  , https://generativelanguage.googleapis.com/v1beta/openai, gemini-2.0-flash
mistral           -> MISTRAL_* , https://api.mistral.ai/v1                  , mistral-small-latest
groq              -> GROQ_*    , https://api.groq.com/openai/v1             , llama-3.3-70b-versatile
openai_compatible -> OPENAI_COMPATIBLE_* , (à fournir)                     , (à fournir)
custom            -> CUSTOM_LLM_*        , (à fournir)                      , (à fournir)
```

- `provider_config(name) -> dict` résout `settings` → kwargs. Priorité :
  **bloc spécifique** (`MISTRAL_API_KEY`…) **>** générique (`LLM_API_KEY`…) **>**
  défaut de la table. Nom inconnu → `nvidia` + warning loggé.
- `build_active_provider()` = `OpenAICompatibleProvider(**provider_config(settings.LLM_PROVIDER))`.
- `services/gemini_client.py` : **inchangé** (importe `build_active_provider`,
  `LLMError`, `BaseLLMProvider`). Les 11 modules métier : **zéro diff**.

### 2.2 Config

- `config.LLM_BASE_URL` : défaut passé de l'URL NVIDIA à `""` — sinon tout
  provider non-nvidia héritait de l'URL NVIDIA (bug corrigé). `""` → le provider
  actif utilise sa propre URL par défaut.
- Ajout `MISTRAL_API_KEY/MODEL/BASE_URL`, `GROQ_API_KEY/MODEL/BASE_URL`.
- `LLM_PROVIDER_NAMES = tuple(_LLM_BLOCKS)` (7), exposé par `llm_public_config()`.

### 2.3 Config à chaud (`PATCH /api/admin/llm`)

`services/runtime_config_service.py` : `_ALLOWED` += `MISTRAL_MODEL/BASE_URL`,
`GROQ_MODEL/BASE_URL` ; `_PROVIDERS = LLM_PROVIDER_NAMES`. Le flux reste
identique : valider → appliquer sur `settings` → `healthcheck()` → **rollback +
400** si échec → persister dans `runtime_config`. **Aucune clé** dans le corps
(`LlmConfigUpdate`) ni la réponse (`LlmConfigView`) — pas de champ `api_key`.

Vérifié en conteneur : `GET /api/admin/llm` avec `LLM_PROVIDER=groq` →
`{"provider":"groq","base_url":"https://api.groq.com/openai/v1",
"model":"llama-3.3-70b-versatile","available_providers":[7 noms]}` — **pas de
`api_key`**.

---

## 3. Architecture anti-redondance *(section obligatoire)*

> Démonstration que `nvidia / openai / gemini / mistral / groq /
> openai_compatible / custom` **ne possèdent pas** chacun une implémentation
> métier indépendante.

### 3.1 Une classe, prouvée par un test

`tests/test_llm_providers.py::test_there_is_exactly_one_provider_implementation` :

```python
impls = [v for v in vars(providers).values()
         if isinstance(v, type) and issubclass(v, BaseLLMProvider)
         and v is not BaseLLMProvider]
assert impls == [OpenAICompatibleProvider]
```

`test_every_backend_is_the_same_class` : pour les 7 noms,
`type(build_provider(name)) is OpenAICompatibleProvider`.

### 3.2 Les couches

| Couche | Rôle | Fichier | Dépend du provider ? |
|---|---|---|---|
| Modules métier (×11) | générer CV / lettre / extraire / matcher | `services/*.py` | **non** — `call_gemini(prompt, request_type=…)` |
| Façade | cache SHA-256, rate-limit, compteurs | `services/gemini_client.py` | **non** — délègue à `build_active_provider()` |
| Logique LLM commune | chaîne de fallback de modèles, `healthcheck`, `_sanitize` | `services/llm/base.py` | **non** |
| Transport | 1 appel `chat.completions.create` (SDK `openai`) | `services/llm/providers.py` (1 classe) | **non** — `base_url` est un paramètre |
| Presets | `(base_url, api_key, model)` par nom | `config._LLM_BLOCKS` (données) | c'est *la* définition d'un provider |

Un nouveau backend OpenAI-compatible = **une ligne** dans `_LLM_BLOCKS` (ou
directement `LLM_PROVIDER=openai_compatible` + `OPENAI_COMPATIBLE_BASE_URL`).
Aucune classe, aucun `elif`, aucun test métier à dupliquer.

### 3.3 Grep de preuve

```
$ grep -rn '== "gemini"\|== "nvidia"\|== "mistral"\|== "groq"\|isinstance.*LLM' \
      services/ --include=*.py | grep -v services/llm/ | grep -v test
(aucun résultat)
```

(Un `grep "provider =="` plus large ne remonte que
`services/admin_service.py` — `AtsBoard.provider == provider`, un filtre SQL sur
la table des *boards ATS*, sans rapport avec le LLM.)

Le seul `if` par nom de provider LLM est `provider_config()` :
`if key not in _LLM_BLOCKS: key = "nvidia"` — un garde-fou de résolution de
config, pas une branche métier.

---

## 4. Dashboard par utilisateur (Phases 68–69)

### 4.1 Un seul endpoint agrégé

`GET /api/dashboard` (`profile_router.dashboard_router`, `Depends(get_current_user)`),
`response_model=UserDashboardResponse`. Pas de 5 endifferents — **un** payload.

### 4.2 Isolation stricte

`services/user_dashboard_service.py` — `UserDashboardService(db, user)` :

- `self._uid = user.id` ; **toute** requête : `where(<Model>.user_id == self._uid)`.
- La route **n'a aucun paramètre `user_id`** → un `?user_id=<autre>` est
  simplement ignoré par FastAPI.
- Pour qu'un superadmin regarde un autre compte : `/api/admin/stats/users` /
  `/api/admin/stats/applications/by-user` (routes séparées, `require_superadmin`).

Tests (`tests/test_user_dashboard.py`, 7) :
`test_user_a_never_sees_user_b_data`, `test_no_user_id_query_param_is_honoured`,
`test_unauthenticated_401`, `test_dashboard_never_says_submitted`,
`test_empty_user_dashboard_is_all_zeros_not_an_error`, +2 de contenu.

### 4.3 Sections (agrégats SQL, fenêtres today/week/month/year)

`activity` (member_since, last_active_at, sessions = COUNT `login`) ·
`jobs` (searches, offers_viewed, offers_saved, providers_engaged, last_search_at) ·
`applications` (total, by_status GROUP BY, by_window, duplicates_avoided, failed) ·
`auto_apply` (prepared / cv / letter counts, avg_match_score, `note` explicite
« ne soumet jamais ») ·
`documents` (totaux + utilisés + created_by_window) ·
`matching` (avg/best score, good_matches ≥ 0.6, top_missing_skills — `Counter`
borné aux **500 dernières candidatures de cet utilisateur**) ·
`providers` (GROUP BY source, most_applied_provider).

`schemas/dashboard.py` : `UserDashboardResponse` + 9 sous-modèles.

---

## 5. Dashboard superadmin enrichi (Phase 70)

- **Nouveau** : `StatsService.jobs_activity()` — un `GROUP BY kind` avec
  `COUNT(CASE created_at >= cut …)` par fenêtre + `COUNT(DISTINCT CASE …)` pour
  les utilisateurs distincts, sur `usage_events` (`search` / `job_view` /
  `job_saved`). Route `GET /api/admin/stats/jobs` + section `jobs` dans
  `GET /api/admin/dashboard` (`JobsActivityStats`). Fenêtre vide = 0, jamais
  estimé.
- Déjà présents (v2.5, conservés) : `usage` (active/new users by window),
  `applications.by_window`, `documents.created_by_window`, `boards`
  (total / enabled / disabled / last_test_ok par provider).
- La réponse `dashboard` a désormais 7 sections :
  `usage · jobs · applications · documents · providers · boards · llm`.

---

## 6. Statistiques provider (Phase 72)

`AdminService.provider_stats()` += deux métriques **dérivées** des compteurs
`ProviderMetrics` déjà enregistrés, **sans nouvel état** :

- `offers_per_run` = `total_offers / ok`
- `empty_result_rate` = `(ok − ok_nonempty) / ok`

`timeout_rate` **non ajouté** : impossible sans un compteur par *type* d'erreur
(la mission demande « si dérivable sans multiplier les métriques » — ça ne l'est
pas). `health_note` couvre déjà « 0 résultat sur N runs malgré un passé prouvé ».

---

## 7. Job search / liens / auto-candidature / extraction (Phase 71)

Audit incrémental — **aucune régression, aucun changement nécessaire** :

- **Fan-out** : `job_search_service._executor()` = `ThreadPoolExecutor` partagé
  (`JOB_SEARCH_GLOBAL_CONCURRENCY=32`) + `Semaphore(JOB_SEARCH_MAX_CONCURRENCY=20)`
  par recherche + une deadline monotone `JOB_SEARCH_DEADLINE`. Intact.
- **Liens** : `_usable_offer_url` / `NormalizedOffer._clean_url` — http(s), pas
  d'userinfo, pas d'IP privée/localhost, pas de caractères de contrôle ; offre
  droppée sinon. SSRF (`services/providers/http.py`) intact. Jamais d'URL
  fabriquée.
- **Auto-candidature** : statuts `prepared / manual_required /
  requires_user_action / unavailable / failed / duplicate`. **Jamais**
  `submitted`. Aucun navigateur / cookie / Voyager / CAPTCHA / proxy pour une
  soumission externe.
- **Extraction** : `JobPreferenceExtractor` — 1 `call_gemini`, conversation
  délimitée + marquée non fiable, repli sur le contexte antérieur en cas
  d'échec, fusion message + `UserProfile` persistant avec priorité explicite.

---

## 8. `is_superadmin` & sécurité (Phase 74)

- `is_superadmin` dans **aucun** schéma de requête utilisateur (`signup`,
  `UserProfileIn`, `JobSearchContext`, update profil). Seul `AdminUserUpdate`
  (route `require_superadmin`) le porte.
- Toutes les requêtes du dashboard utilisateur filtrent sur `current_user.id`,
  jamais sur un `user_id` client (tests d'isolation ci-dessus).
- Clé LLM : jamais loggée, jamais retournée. `_sanitize` élargi : `sk-` /
  `nvapi-` / `AIza` / `gsk_`, jetons ≥ 32 hex, valeurs étiquetées
  (`Authorization:`, `api_key=`, `bearer …`), URLs. Tronqué à 300.
- Smoke conteneur : user normal → `403` sur `/api/admin/*` ; `GET /api/admin/llm`
  et `/api/admin/dashboard` ne contiennent aucune clé (provider `groq` testé).

---

## 9. Performance (Phase 73) — mesurée

SQLite en mémoire, mix réaliste par utilisateur (0–15 candidatures, 0–5 CV,
0–3 lettres, 0–8 événements × 4 types).

| n_users | candidatures | événements | `GET /api/dashboard` (1 user) | dashboard superadmin (4 agrégats) |
|---:|---:|---:|---:|---:|
| 100 | 743 | 1 601 | **38 ms** / 35 requêtes | 40 ms / 32 req |
| 1 000 | 7 370 | 16 182 | **35 ms** / 35 requêtes | 129 ms / 32 req |
| 5 000 | 37 238 | 80 124 | **43 ms** / 35 requêtes | 617 ms / 32 req |

- **Dashboard utilisateur : plat (~40 ms) quel que soit le nombre total de
  comptes** — il ne touche que les lignes du propriétaire, tous les filtres
  `user_id` sont indexés. Nombre de requêtes **constant** (35, pas de N+1 :
  aucune requête par ligne).
- **Dashboard superadmin** : croît avec le volume global (agrégats plein-table,
  attendu) — 617 ms à 5 000 users / 80 k événements, **mis en cache 60 s**
  (`GET /api/admin/dashboard`, `?refresh=true` pour forcer). Nombre de requêtes
  constant (32).
- Indexes utilisés : `ix_usage_events_time_user (created_at, user_id)`,
  `ix_usage_events_user_kind_time`, `ix_usage_events_kind_time`,
  `job_applications(user_id)` + `(status)` + `(created_at)`, `generated_cvs(user_id)`,
  `saved_jobs(user_id)`. Aucun nouvel index nécessaire.
- Redis : `services/cache_service.py` le supporte déjà (via `REDIS_URL`) ; pas
  de nouvelle dépendance ajoutée pour un gain marginal.

---

## 10. `UsageEvent` — audit (Phase 68)

`EVENT_KINDS` = `login / search / job_view / job_saved / application_prepared /
document_created / document_downloaded`. Stocke **uniquement** `user_id`,
`kind`, `created_at`, `meta` (objet minuscule, primitif). **Jamais** de contenu
de CV / lettre, de prompt LLM complet, de clé, de jeton. `record()` best-effort
(un échec d'écriture ne bloque jamais une requête). Rétention :
`USAGE_EVENT_RETENTION_DAYS=400`, `prune()` au démarrage. `login` = session
(pas d'événement `session` distinct). Suffisant pour les deux dashboards.

---

## 11. ATS boards (inchangé, vérifié)

Fichiers `data/ats_boards/*.txt` + env `<X>_BOARDS` + surcouche DB `AtsBoard`
(`resolved_tokens` = `∪ enabled ∖ disabled`, best-effort, cache
`ATS_BOARD_OVERLAY_TTL`). `ATS_MAX_BOARDS`, SSRF, fallback (404 board → skip ;
seul *tout* échouer marque le provider) préservés. Dashboard `boards` :
provider / total / enabled / disabled / last_test_ok.

---

## 12. Tests (Phase 75)

| Fichier | Objet | Δ |
|---|---|---|
| `tests/test_user_dashboard.py` | **nouveau** — sections, comptes, isolation A/B, `?user_id` ignoré, 401, jamais « submitted », compte vide | +7 |
| `tests/test_admin_stats.py` | + `jobs_activity` par fenêtre, route superadmin-only, section `jobs` du dashboard, `offers_per_run`/`empty_result_rate` | +4 |
| `tests/test_llm_providers.py` | déjà réécrit Phase 66 : 1 seule classe, 7 backends même type, URLs par défaut (nvidia/openai/gemini/mistral/groq), bloc > générique, inconnu → nvidia, config publique sans clé, `_sanitize` (`gsk_`, hex 32) | (réécrit) |

**Total : 1010 passed, 1 skipped** (`pytest -q`, mono-processus).
`python -m compileall .` : OK.

---

## 13. Documentation (Phase 76)

- `REFACTOR_REPORT.md` §15 : audit + décisions D14–D17 + journal Phases 66–72.
- `README.md` : tableau LLM (7 providers, « une implémentation »), ligne
  `GET /api/dashboard` (isolation).
- `PROJECT_STRUCTURE.md` : `services/user_dashboard_service.py`,
  `schemas/dashboard.py`, `dashboard_router`, `llm/` réécrit (1 classe),
  `stats_service.jobs_activity()`, 3 lignes « règles de conception ».
- `.env.example` : `MISTRAL_*` / `GROQ_*`, sémantique `LLM_BASE_URL` vide,
  `LLM_PROVIDER` = 7 valeurs.
- `API_FRONTEND_GUIDE.tex` (+ PDF recompilé, 153 KiB) : section
  `GET /api/dashboard`, `/api/admin/stats/jobs`, `available_providers` = 7,
  `offers_per_run` / `empty_result_rate`.

---

## 14. Validation de production (Phase 77)

| Étape | Résultat |
|---|---|
| `python -m compileall .` | ✅ exit 0 |
| `pytest -q` (mono-processus) | ✅ 1010 passed, 1 skipped |
| `docker build --build-arg INSTALL_BROWSER=false -t cv-assistant:v27 .` | ✅ exit 0 — image 586 MB |
| conteneur `GET /api/health` | ✅ `{"status":"ok",…}` |
| conteneur `GET /api/dashboard` (user normal) | ✅ `200`, données propres |
| conteneur `GET /api/admin/dashboard` (user normal) | ✅ `403` |
| conteneur `GET /api/admin/stats/jobs` (superadmin) | ✅ `200` |
| conteneur `GET /api/admin/llm` (provider=groq) | ✅ `200`, `available_providers`=7, **aucune clé** |
| bootstrap `SUPERADMIN_EMAILS` au redémarrage | ✅ promotion effective |
| scan de secrets dans les logs / réponses | ✅ aucune clé (`sk-`/`nvapi-`/`gsk_`/`AIza`) |

---

## 15. Compatibilité — rien retiré ni affaibli

http.py chokepoint · SSRF · HTTPS-only · CGNAT · validation des redirections ·
plafonds de taille · circuit breaker · `ProviderMetrics` · `health_note` ·
dedup · `source_url` · isolation utilisateur · `is_superadmin` + anti-escalade ·
protections documents · sécurité LLM (jamais de clé en réponse) · pas de
LinkedIn authentifié · pas de JobSpy · pas de stealth / rotation de proxy · pas
de navigateur pour LinkedIn. **Toutes les modifications sont additives**, sauf
`LLM_BASE_URL` (défaut `""` au lieu de l'URL NVIDIA) — correction d'un bug qui
faisait hériter tous les providers de l'URL NVIDIA, et la fusion des 5 classes
de provider en 1 (demande explicite de la mission, `gemini_client` et les 11
appelants inchangés).

---

## 16. Limites connues & suites possibles

- `timeout_rate` par provider : nécessiterait un compteur par type d'erreur
  dans `ProviderMetrics` — écarté (multiplierait les métriques).
- Dashboard superadmin à très grand volume (> 100 k événements) : ~0,6 s,
  atténué par le cache 60 s ; un pré-agrégat quotidien (table de rollup) serait
  la prochaine étape si le volume décuple.
- Le dashboard utilisateur fait 35 requêtes indexées (constant, sub-50 ms) ;
  regroupables en ~10 si le besoin s'en fait sentir.
- `providers` dans le dashboard utilisateur = candidatures par `source`
  (pas l'activité de recherche par provider, qui n'est pas tracée par
  utilisateur — seul l'agrégat superadmin l'est via `ProviderMetrics`).

---

*Fin du rapport — Cycle v2.7.*
