# Rapport bake-off providers/modèles — Phase 2a

**Mesure isolée, aucun changement de production.** Outillage : `benchmarks/bakeoff/`
(prompts de production importés en lecture seule, mêmes inputs pour tous les modèles).
Inputs : CV représentatif (`fixtures/sample_cv.txt`, ingénieur backend 4 ans) + offre réelle
Senior Backend Engineer. Température 0 pour les tâches structurées, 0.3 pour la rédaction.
113 runs. Fait le 2026-09-08.

---

## 1. Disponibilité réelle avec tes clés

| Provider | Endpoint models | Modèles réellement UTILISABLES | Vitesse | `response_format` json_schema | Blocage |
|---|---|---|---|---|---|
| **Groq** | 14 | `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | **90–720 tok/s** (ttft 0.2–3s) | ✅ **oui** (strict) | aucun sur ~90 appels |
| **Gemini** | 55 | `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | 40–235 tok/s | ✅ oui | **quota free tier** : `gemini-*-pro`, `gemini-flash-latest`, `gemini-3.x-flash` (non-lite) → **429 "exceeded your current quota"**. `gemini-3.6-flash` (reasoning) → lent (7–20 t/s) + JSON invalide sur 2 tâches |
| **NVIDIA** | 81 | `nvidia/nemotron-3-super-120b-a12b` uniquement | 15–71 tok/s | ❌ (json_object → 404) | `nemotron-3.5-lightning-30b` instable ; `llama-3.1-nemotron-70b`, `palmyra-creative-122b`, `nemotron-nano-3` → **404 "not found for account"** ; appels intermittents en 404 |
| **Mistral** | 46 | **AUCUN** | — | — | **tous les modèles → 429 `code 1300` "Rate limit exceeded"**, même en séquentiel avec backoff 32 s, **et même après changement de clé**. `/models` répond mais **zéro complétion ne passe**. → **à débloquer côté compte Mistral (plan / facturation / activation API)** |

---

## 2. Tableau de comparaison (best json-mode par modèle)

`H:[…]` = chiffre inventé détecté automatiquement dans le résumé. `add_owned` = `additional_skills`
contient une compétence déjà possédée. `cov` = couverture des mots-clés de l'offre.
`jV` = JSON valide.

| request_type | provider | model | ok | wall | tok/s | jV | notes qualité (auto) |
|---|---|---|---|---|---|---|---|
| **resume_parse_experience** | groq | gpt-oss-120b | ✅ | 3.2s | 388 | ✅ | 0 halluc, 3 exp, companies OK |
| | groq | qwen3.8-27b | ✅ | 1.4s | 257 | ✅ | 0 halluc |
| | gemini | 3.5-flash-lite | ✅ | 1.7s | 235 | ✅ | 0 halluc |
| | gemini | 3.1-flash-lite | ✅ | 1.8s | 201 | ✅ | 0 halluc |
| | groq | gpt-oss-20b | ✅ | 2.2s | 668 | ✅ | 0 halluc |
| | nvidia | nemotron-3-super-120b | ✅ | **25–41s** | 48–71 | ✅ | 0 halluc mais **très lent** |
| **resume_parse_education** | groq | gpt-oss-120b | ✅ | 1.6s | 404 | ✅ | 0 halluc, 2 entrées |
| | gemini | 3.5 / 3.1-flash-lite | ✅ | 1.1s | 100–112 | ✅ | 0 halluc |
| | nvidia | nemotron-3-super-120b | ✅ | **32–57s** | 29–49 | ✅ | 0 halluc |
| **resume_parse_projects** | groq / gemini | tous candidats | ✅ | 0.5–1.1s | 58–517 | ✅ | 0 halluc, 1 projet |
| **resume_parse_skills** | groq / gemini | tous candidats | ✅ | 0.5–0.9s | 43–371 | ✅ | 0 halluc, 15 skills |
| | nvidia | nemotron-3-super-120b | ✅ | 3.2s | 102 | ✅ | 0 halluc |
| **profile_analysis** | gemini | 3.5-flash-lite (json_schema) | ✅ | 1.4s | 167 | ✅ | **propre** |
| | gemini | 3.1-flash-lite (json_schema) | ✅ | 1.7s | 165 | ✅ | **propre** |
| | groq | gpt-oss-20b | ✅ | 2.0s | 617 | ✅ | **propre** |
| | groq | qwen3.8-27b | ✅ | 0.8s | 268 | ✅ | H:['73%'] (métrique dérivée) |
| | groq | gpt-oss-120b | ✅ | 2.1s | 430 | ✅ | **H:['70%'] + "5+ ans" (CV=4 ans) + sortie en anglais** |
| | nvidia | nemotron-3-super-120b | ✅ | 11.9s | 126 | **❌** | JSON invalide ; json_object → 404 |
| **ats_keyword_extraction** | groq | qwen3.8-27b | ✅ | **0.7s** | 210 | ✅ | **cov=1.0** |
| | groq | gpt-oss-120b | ✅ | 1.7s | 289 | ✅ | cov=1.0 |
| | gemini | 3.5 / 3.1-flash-lite | ✅ | 1.1–1.3s | 108–135 | ✅ | cov=1.0 |
| | nvidia | nemotron-3-super-120b | ✅ | **11–36s** | 33–107 | **❌** | JSON invalide les 2 modes |
| **ats_content_optimization** | groq | gpt-oss-120b | ✅ | 4.2s | 443 | ✅ | *voir §3 — injecte Kubernetes* |
| | gemini | 3.5-flash-lite | ✅ | 1.7s | 215 | ✅ | *voir §3* |
| | gemini | 3.1-flash-lite | ✅ | 1.9s | 201 | ✅ | *voir §3 — le pire* |
| | groq | qwen3.8-27b | ✅ | 1.2s | 324 | ✅ | *voir §3* |
| | groq | gpt-oss-20b | **❌** | 3.8s | 781 | ❌ | **tronqué à 3000 tokens** |
| **cover_letter** | gemini | 3.5-flash-lite | ✅ | 1.9s | 171 | — | **A — fidèle, qualité production** ⭐ |
| | groq | gpt-oss-120b | ✅ | 1.8s | 341 | — | **A — fidèle, qualité production** ⭐ |
| | gemini | 3.1-flash-lite | ✅ | 1.8s | 205 | — | A — fidèle |
| | groq | gpt-oss-20b | ✅ | 1.3s | 545 | — | B+ — bon, un peu générique |
| | groq | qwen3.8-27b | ✅ | 1.3s | 265 | — | B — sur-affirme (K8s "compétence actuelle", CI/CD "expertise") |
| **conversation_agent** | gemini | 3.5-flash-lite | ✅ | 1.5s | 208 | — | **A — 3 options fidèles, 0 invention** ⭐ |
| | gemini | 3.1-flash-lite | ✅ | 2.3s | 188 | — | **A — fidèle** ⭐ |
| | groq | qwen3.8-27b | ✅ | 1.1s | 339 | — | **A — fidèle + honnête (met un avertissement sur "Senior")** ⭐ |
| | groq | gpt-oss-20b | ✅ | 0.7s | 249 | — | B+ — conservateur, fidèle, court |
| | groq | gpt-oss-120b | ✅ | 0.9s | 380 | — | **D — invente EC2/RDS/Lambda/Scrum/pytest quand on demande d'"améliorer"** |
| **job_preference_extraction** | groq | gpt-oss-120b / qwen3.8-27b | ✅ | 0.9–1.8s | 308–354 | ✅ | OK |
| | gemini | 3.5 / 3.1-flash-lite | ✅ | 1.0–1.2s | 84–124 | ✅ | OK |
| | groq | gpt-oss-20b | ✅ | 1.6s | 559 | ⚠️ | json_schema échoue, `none` OK |

---

## 3. Problème de fidélité — lecture humaine des sorties de rédaction

### `ats_content_optimization` — **TOUS les modèles injectent des compétences de l'offre dans l'expérience du candidat**

Le CV source ne mentionne **pas Kubernetes ni Terraform** (stack réelle : Docker + AWS).
L'offre les demande. Résultat, à l'identique sur tous les modèles :

- **gpt-oss-120b** : résumé « spécialisé en Python, FastAPI… **orchestration de conteneurs avec Kubernetes** » ; bullet « …grâce à un cache Redis, optimisation des requêtes **et orchestration Kubernetes** ».
- **gemini-3.5-flash-lite** : bullet Atlas Data « …traitant 2M de transactions/mois, **tout en assurant son déploiement via Kubernetes** ».
- **gemini-3.1-flash-lite** (le pire) : bullet Beta Solutions « microservices… **orchestrés via Kubernetes** pour assurer la scalabilité sur AWS » + « **automatisant l'infrastructure avec Terraform** ». Or Beta Solutions = Django/MySQL/RabbitMQ, ni K8s ni Terraform, ni AWS.
- **qwen3.8-27b** : résumé « Expert en AWS, Terraform et **Kubernetes** ».

**Cause : le prompt de production le demande explicitement.**
`services/ats_optimizer.py::_build_optimization_prompt`, bloc « MODE: Job-targeted optimization » :
> `MISSING KEYWORDS TO INTEGRATE (max 20): {missing_str}`
> `- Integrate missing keywords naturally into summary and bullets.`

en contradiction directe avec, plus bas :
> `- Do NOT invent skills, technologies, metrics, or achievements.`

→ **Ce n'est pas un problème de modèle. Aucun choix de modèle ne le corrige.** Correction Phase 2b (voir §5).

### `profile_analysis` — métrique de latence inventée

Le CV dit « latence p95 de 800 ms à 210 ms » (≈ 74 %). Presque tous les modèles écrivent une
affirmation « **réduction de la latence de ~70-73 %** » dans le résumé — chiffre non présent
littéralement. `gpt-oss-120b` va plus loin : « **5+ ans** » (CV = 4 ans) et **rédige en anglais**.
Le prompt de prod (`services/profile_analyzer.py::_build_analysis_prompt`) n'a **aucune règle
anti-invention de chiffres** ni **directive de langue**. En mode `json_schema`, gemini flash-lite
redevient propre.

### `cover_letter` — bon dans l'ensemble

`gemini-3.5-flash-lite` et `gpt-oss-120b` produisent des lettres **fidèles, en français naturel,
qualité production** : uniquement des faits du CV, mentionnent la vraie entreprise/le vrai poste,
ne revendiquent pas Kubernetes. `qwen3.8-27b` sur-affirme légèrement.

### `conversation_agent` — attention à `gpt-oss-120b`

Quand on demande « réécris mon résumé, sans rien inventer », `gpt-oss-120b` **fabrique une liste
entière de services AWS (EC2, RDS, S3, Lambda, CloudFormation), des frameworks de test (pytest),
des méthodes agiles (Scrum/Kanban)** absents du profil. `gemini-3.5/3.1-flash-lite` et
`qwen3.8-27b` restent strictement fidèles (et `qwen` ajoute même un avertissement utile).

---

## 4. TABLE DE ROUTING CANDIDATE (proposition — NON implémentée)

Règle appliquée : **qualité/fidélité d'abord**, vitesse ensuite à qualité égale.
Plusieurs modèles/providers volontairement, un par type de tâche.

| request_type | provider | model | temp | max_tokens | timeout | Pourquoi |
|---|---|---|---|---|---|---|
| `resume_parse_experience` | groq | `openai/gpt-oss-120b` | 0.0 | 3000 | 30 s | 0 halluc, JSON strict, 3–4 s (vs 25–41 s NVIDIA) |
| `resume_parse_education` | groq | `openai/gpt-oss-120b` | 0.0 | 2000 | 25 s | idem |
| `resume_parse_projects` | groq | `openai/gpt-oss-120b` | 0.0 | 2000 | 25 s | idem |
| `resume_parse_skills` | groq | `openai/gpt-oss-120b` | 0.0 | 1500 | 20 s | idem |
| `resume_parse_fallback` | groq | `openai/gpt-oss-120b` | 0.0 | 4000 | 40 s | CV entier — marge tokens |
| `profile_analysis` | gemini | `gemini-3.5-flash-lite` | 0.0 | 1500 | 25 s | le plus fidèle en mode `json_schema` (+ durcir le prompt, §5) |
| `ats_keyword_extraction` | groq | `openai/gpt-oss-120b` | 0.0 | 1500 | 20 s | couverture 1.0, 1.7 s |
| `ats_content_optimization` | groq | `openai/gpt-oss-120b` | 0.2 | 3500 | 45 s | meilleur rédacteur — **exige le fix prompt §5 d'abord** |
| `skill_categorisation` | groq | `qwen/qwen3.8-27b` | 0.0 | 800 | 15 s | tâche triviale, le plus rapide |
| `cover_letter` | gemini | `gemini-3.5-flash-lite` | 0.3 | 2500 | 45 s | lettre la plus fidèle à la lecture humaine |
| `letter_job_company` | groq | `openai/gpt-oss-120b` | 0.0 | 800 | 15 s | petite extraction |
| `conversation_agent` | gemini | `gemini-3.5-flash-lite` | 0.3 | 2000 | 40 s | fidèle + utile ; **surtout PAS gpt-oss-120b** (brode) |
| `job_preference_extraction` | groq | `openai/gpt-oss-120b` | 0.0 | 1200 | 20 s | json_schema, rapide |
| *défaut / `generic`* | groq | `openai/gpt-oss-120b` | 0.3 | 4096 | 45 s | |

**Chaîne de fallback par tâche** (alternatives **équivalentes en qualité**, pas « la plus rapide ») :
`primaire → gemini-3.1-flash-lite → l'autre modèle groq → nvidia/nemotron-3-super-120b (dernier recours)`.
Mistral **exclu de la chaîne** tant que la clé n'a pas de quota.

### Réserves à valider avec toi
1. **Quota Gemini free tier** : aujourd'hui seuls les `flash-lite` passent ; sous charge réelle même eux
   pourraient plafonner. Groq n'a montré **aucune limite** sur ~90 appels. → Option : **tout router vers Groq**
   (`gpt-oss-120b` extraction/analyse, `qwen3.8-27b` chat, `gpt-oss-120b` rédaction) et garder Gemini flash-lite
   en fallback — à re-tester après le durcissement des prompts §5.
2. **Mistral** : la clé renvoie encore `429 code 1300` sur **toutes** les complétions → à débloquer côté compte.
3. **`gpt-oss-120b` en chat** = à éviter (brode). **`gpt-oss-120b` en `profile_analysis`** = à éviter (invente séniorité).

---

## 5. Corrections de PROMPT nécessaires (Phase 2b — NON faites)

Emplacements exacts pour plus tard :

| # | Fichier / fonction | Problème | Correction |
|---|---|---|---|
| P1 | `services/ats_optimizer.py::_build_optimization_prompt` (bloc « MODE: Job-targeted », ~L279-292) | « Integrate missing keywords **into summary and bullets** » → injecte K8s/Terraform dans l'expérience | Retirer cette instruction + `MISSING KEYWORDS TO INTEGRATE`. Ne reformuler/réordonner QUE ce qui est déjà dans le profil. Les mots-clés manquants → **uniquement** dans `additional_skills` (suggestions). |
| P2 | `services/cv_generator.py::_build_skills` (~L161-166) | `additional_skills` du LLM **fusionnés automatiquement** dans la section Skills du PDF | Ne plus fusionner. Retourner `additional_skills` séparément dans la réponse de génération (`{cv, additional_skills}`) pour choix utilisateur. |
| P3 | `services/profile_analyzer.py::_build_analysis_prompt` | Aucune règle anti-chiffres inventés ; aucune directive de langue → « 70 % », « 5+ ans », sortie EN | Ajouter : « ne jamais énoncer une métrique dérivée/calculée (pourcentage, durée) absente **verbatim** du CV » + directive de langue (fr/en selon la demande). |
| P4 | `services/ats_optimizer.py::optimize_content` (~L162-170) | Sur échec de parsing JSON → `_local_optimize` **silencieux** (CV sans optimisation ATS, aucune erreur remontée) | Rare avec Groq (JSON fiable) mais : signaler au frontend « optimisation ATS indisponible » quand le fallback local se déclenche. |
| P5 | `services/conversation_service.py::_build_prompt` | Pas de garde anti-invention forte → un modèle bavard fabrique des technos | Ajouter la contrainte anti-hallucination explicite (comme `parser_common.ANTI_HALLUCINATION_RULES`) au prompt de l'agent. |

Ces 5 corrections sont **indépendantes du choix de modèle** et **prioritaires** (elles touchent la fidélité).

---

## 6. Impact attendu (à MESURER après implémentation)

| Tâche | Baseline NVIDIA (mesuré Phase 1) | Candidat routing (mesuré ici) | Gain latence | Qualité |
|---|---|---|---|---|
| Extraction (4 appels //) | **123 s** (3/4 fallback) | ~3–4 s (le plus lent des 4) | **~35×** | ≥ (0 halluc les deux) |
| `profile_analysis` | 115 s (fallback) | ~1.4 s | **~80×** | **>** (moins d'invention en json_schema + prompt P3) |
| `ats_keyword_extraction` | 99 s (fallback) | ~1.7 s | **~58×** | > (NVIDIA rendait du JSON invalide) |
| `ats_content_optimization` | **272 s → échec total → fallback local** | ~4 s | **immense** | **>** (produit un vrai résultat ; + fix P1 pour la fidélité) |
| Génération CV complète | **180–486 s** | **~10–15 s** | **~15–30×** | ≥ / > avec P1-P4 |
| `cover_letter` | ~30–60 s (estimé) | ~2 s | ~15–30× | ≥ (lettres fidèles) |
| `conversation_agent` | ~30 s | ~1.5 s | **~20×** | ≥ (Gemini flash-lite / qwen fidèles) |

---

## 7. Décisions à valider avant Phase 2b

1. **Table de routing** ci-dessus (§4) — valider, ou ajuster (ex. « tout Groq »).
2. **5 corrections de prompt** (§5) — valider le principe.
3. **`additional_skills` → réponse séparée `{cv, additional_skills}`** (P2) — valider le contrat.
4. **Mistral** : le laisser de côté pour l'instant (clé sans quota) — OK ?
5. **Gemini free tier** : accepter le risque de quota, ou basculer chat/lettre sur Groq aussi ?
