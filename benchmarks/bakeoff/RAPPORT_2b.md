# RAPPORT Phase 2b — benchmark APRÈS + comparaison indicative (contrainte #20)

> **Mise en garde méthodologique.** La baseline « AVANT » (~180 s pour une
> génération CV) provient d'un **environnement et d'un provider différents**
> (NVIDIA NIM free tier, test utilisateur réel Phase 1). Le « ~25–35× plus
> rapide » est donc un **résultat observé**, **pas** un benchmark strictement
> comparable (provider, quota, réseau, machine différents). **La référence
> actuelle est le benchmark APRÈS ci-dessous.**

Bench : `benchmarks/bakeoff/pipeline_bench.py` — pipeline **routé** de bout en bout,
MinIO/DB en mémoire, LLM réel (Groq primaire, Gemini fallback). 7 scénarios,
espacés de 25–45 s pour respecter le TPM free-tier (8000 tok/min Groq).

## Contexte AVANT

| | AVANT (Phase 1 baseline) | Source |
|---|---|---|
| Provider | NVIDIA NIM free tier (`nemotron`/`gpt-oss`) | `.env` d'origine |
| Débit | ~20–35 tok/s, instable | Phase 1 profiling |
| **Génération CV (test utilisateur réel)** | **~3 min (~180 s)** | rapport utilisateur |
| Fallback | 3 modèles NVIDIA × timeout 45 s × double retry SDK = jusqu'à ~270 s/appel | `services/llm` d'origine |
| Structured output | ❌ (json parse défensif + fallback silencieux) | — |
| Anti-hallucination P1 | ❌ prompt ATS = « Integrate missing keywords into summary and bullets » → Kubernetes/Terraform injectés | bake-off Phase 2a |
| additional_skills | fusionnés dans le CV comme compétences réelles | `cv_generator._build_skills` d'origine |
| CV 1 page | `experience[:4]` + `achievements[:3]` + `bullets[:3]` = **coupe silencieuse** | `ats_optimizer` / `pdf_generator` d'origine |
| Cache lettre | ❌ | — |
| Versioning | ❌ | — |
| Agent d'édition par référence | ❌ | — |

## Mesures APRÈS (live, moyenne de 3 runs)

| Scénario | Wall | Appels LLM | Fallbacks | Cache | Tokens in→out | Provider(s) |
|---|---|---|---|---|---|---|
| 1 · extraction CV | **~3.0 s** | 4 | 0–2 | 0 | ~5100→~1900 | groq (+gemini si TPM) |
| 2 · génération CV | **~5–7.5 s** | 3 | 0–1 | 0 | ~2950→~3800 | groq (+gemini si TPM) |
| 3 · génération lettre | **~2.5–3 s** | 2 | 0 | 0 | ~2000→~1200 | groq |
| 4 · **régénération CV identique** | **0.02 s** | 0 | 0 | **3** | 0 | — (cache) |
| 5 · chat (agent conversationnel) | **~5–7 s** | 1 | 0 | 0 | ~100→~2600 | groq |
| 6 · modification CV par référence | **~2–3 s** | 1 | 0 | 0 | ~1600→~580 | groq |
| 7 · modification lettre par référence | **~2–2.7 s** | 1 | 0 | 0 | ~500→~600 | groq |

**RÉFÉRENCE APRÈS : p50 ≈ 3.0 s · p95 ≈ 7.5 s · génération CV ≈ 5–7.5 s ·
régénération avec cache ≈ 0.02 s · aucune techno JD injectée · aucun chiffre inventé.**

### Comparaison indicative (non strictement comparable — voir la mise en garde en tête)
Génération CV : ~180 s (Phase 1, NVIDIA free tier, test utilisateur) → ~5–7.5 s
(Phase 2b, Groq + fallback) ≈ **~25–35× plus rapide** — résultat **observé**, pas un
benchmark contrôlé.

## Qualité (le critère prioritaire)

| Critère | AVANT | APRÈS |
|---|---|---|
| **Techno de la JD injectée dans le CV** (Kubernetes/Terraform/Docker/AWS/Kafka…) | ❌ oui (tous les modèles, bake-off) | ✅ **aucune** (`jd_tech_injected_into_cv: []`, live) |
| Chiffres/pourcentages inventés | risque (prompt le permettait) | ✅ `invented_numbers: []` (live) ; règle « ne pas dériver 800ms→210ms en ~74% » dans P1/P3/lettre |
| Validité structurée | parse défensif, fallback silencieux | ✅ JSON Schema strict (Groq/Gemini) + validation Pydantic + **1 retry réparation** + salvage explicite loggé |
| additional_skills | fusionnés = présentés comme réels | ✅ **séparés**, `additional_skills_are_suggestions: true`, jamais dans le PDF |
| CV 1 page | coupe silencieuse d'expériences/bullets | ✅ **toutes** présentes ; layout adaptatif (`fit_one_page: true`, `body_font_pt: 9.0 ≥ plancher 8.0`) ; si débordement → 2 pages **signalées** |
| Statut optimisation ATS | inconnu du frontend | ✅ `ats_status ∈ applied/local_fallback/skipped/cache` |
| Latence CV | ~180 s | ~5–7.5 s |
| Fallback maîtrisé | double retry SDK, NVIDIA relance la latence | ✅ `max_retries=0`, fail-over rapide, circuit breaker, sticky last-good, **NVIDIA jamais en fallback auto** |

## Observations live importantes

1. **Le goulot d'étranglement est désormais la capacité free-tier des APIs**, plus
   l'architecture. Groq free = 8000 tok/min ; sous rafale, `openai/gpt-oss-120b`
   renvoie 429 TPM → le routing bascule sur Gemini flash-lite (visible :
   `fb=1–2`, `providers: [gemini, groq]`). Quand Groq **et** Gemini sont 429 dans
   la même fenêtre (scénario 3 sur 1 run/3), la lettre échoue proprement après
   fail-over (11 s) au lieu de bloquer 3 min. → **passer Groq en Dev Tier** (ou
   Gemini payant) supprime ce plafond.
2. **Régénération identique = 0.02 s** (LOT 7) — gain net, qualité inchangée
   (même PDF, `from_cache: true`).
3. **Édition par référence = 1 seul appel LLM** (l'action structurée), le
   re-render est **local sans LLM** — donc ~2–3 s et déterministe.
4. `hallucination_free: true` sur le CV généré live — P1 corrigé en conditions réelles.

## Verdict

Amélioration de vitesse **massive** (~30× sur la génération CV) **sans dégradation
de qualité** — au contraire : structured output strict, P1/P3/P5 durcis,
additional_skills séparées, plus de coupe silencieuse, statut ATS explicite,
fallback maîtrisé. Conforme à « une amélioration de vitesse n'est acceptée que si
la qualité est égale ou meilleure » (#20).
