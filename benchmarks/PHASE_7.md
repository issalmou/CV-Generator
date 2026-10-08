# Phase 7 — Recommandations intelligentes (2026-09-13)

**Contexte** : construite sur l'état exact de la Phase 6 (voir
`benchmarks/PHASE_6.md`, en particulier §M pour sa finalisation) — cette
phase ne concerne que **la couche de recommandations** du chemin
conversationnel (`JobConversationAgent`). Aucun invariant Phase 6 n'est
modifié (agent orchestré, `ConversationService`, `ConversationDirective`,
confirmation FR/EN déterministe, `pending_action`, pagination déterministe,
`JobSearchService.search_async()`, appels Python internes, aucun HTTP
backend→backend, owner scoping, UUID, dashboard jobs, `JobApplicationService`).

Pour l'audit qui suit et corrige cette phase, voir `benchmarks/PHASE_6.md` §O
(« Phase 8 — Audit qualité des recommandations »).

---

## 1. Objectif

Remplacer le moteur de recommandations à liste fermée (`services/recommendations.py`
— 12 types fixes : `review_match`, `confirm_skill`, …) par un système
**contextuel, transversal et extensible**, pour le chemin conversationnel
(`JobConversationAgent`) uniquement.

## 2. Audit préalable

- `services/recommendations.py::build(ctx: RecommendationContext)` est un moteur
  déterministe à 12 types fixes, testé par **15 tests directs**
  (`tests/test_recommendations.py`) qui appellent `build()` avec le dataclass et
  vérifient des `.type` précis. Il n'est appelé qu'à **deux endroits** :
  `JobConversationAgent._search()` (chemin conversationnel) et
  `api/cv.py::_augment_cv_recommendations` (après `/api/generate-cv`, endpoint
  **stateless**, sans conversation ni directive LLM disponible).
- Décision : **`services/recommendations.py` reste intact** (15 tests
  préservés tels quels) et continue de servir **uniquement**
  `/api/generate-cv` — un endpoint synchrone hors conversation, pour lequel
  ajouter un appel LLM dédié violerait la contrainte « pas d'appel LLM
  inutile » sans aucun contexte conversationnel à exploiter. Le nouveau
  moteur intelligent remplace **uniquement** l'appel fait dans
  `_search()` (le seul point du chemin conversationnel qui utilisait
  l'ancien moteur).

## 3. Architecture

```
User message
     │
     ▼
ConversationService.send_message  (async, inchangé — Phase 6)
     │
     ▼
JobConversationAgent.handle
     │
     ├─ ContextBuilder (recommendation_engine.build_context) — résumé compact :
     │    CV STATUS / COVER LETTER STATUS / APPLICATIONS STATUS (COUNT/aggregate
     │    SQL, jamais le document brut, jamais toute la DB)
     │
     ▼
UN SEUL call_gemini → ConversationDirective
     ├── reply, intent, search_patch, apply, question   (Phase 6, inchangé)
     └── recommendations: 0-10 IntelligentRecommendation bruts
             (action = chaîne libre — jamais un enum fermé)
     │
     ▼
recommendation_engine.validate_and_rank   (déterministe, Python pur)
     ├── ownership (target_id appartient à l'utilisateur : SavedJob/GeneratedCV/
     │    GeneratedLetter/JobApplication)
     ├── liste noire de sécurité (delete/drop_table/shell/exec_sql/sudo/…)
     ├── anti-contradiction (candidature déjà faite → drop "apply" ; lettre déjà
     │    attachée → drop "generate_letter")
     ├── déduplication (action, target_id)
     └── classement (priority desc, confidence desc) → cap à 3
     │
     ▼
MessageSendResponse.recommendations   (additif — recommended_actions inchangé)
     │
     ▼
Frontend affiche des suggestions — RIEN n'est jamais exécuté automatiquement.
```

**Appels LLM par message : toujours 1** (inchangé depuis Phase 6). Les
recommandations voyagent dans la **même** sortie structurée que
`intent`/`reply`/`search_patch` — zéro appel supplémentaire.

## 4. `IntelligentRecommendation` (`schemas/recommendation.py`)

`action: str` libre (jamais `Literal`/enum) — testé explicitement
(`test_action_field_has_no_enum_or_literal_constraint`). Champs :
`title`, `message`, `reason`, `priority` (low/medium/high), `confidence`
(0-1), `requires_confirmation`, `requires_information`, `question`,
`target_type` (job/cv/letter/application/search_preferences/conversation/
None), `target_id`, `parameters`.

## 5. Sécurité : la recommandation ne s'exécute jamais

Aucun code, nulle part, ne fait `getattr(obj, recommendation.action)` ou
équivalent — vérifié par un test de régression
(`test_there_is_no_dispatcher_that_executes_a_recommendation_action`).
Agir sur une recommandation passe **obligatoirement** par un nouveau message
utilisateur, qui repasse par le pipeline `intent` habituel (donc par la
confirmation déterministe pour toute action sensible — Phase 6 intacte).
`validate_and_rank` force `requires_confirmation=True` sur toute
recommandation dont l'action évoque "apply"/"postul"/"delete"/"supprim",
même si le LLM a mis `False` — défense en profondeur, sans effet sur
l'exécution puisqu'aucune recommandation n'exécute quoi que ce soit.

## 6. Compromis techniques assumés

1. **`services/recommendations.py` non refactorisé, conservé pour
   `/api/generate-cv` uniquement.** Justification : endpoint stateless, pas
   de conversation, pas de `ConversationDirective` à enrichir — y ajouter un
   appel LLM dédié violerait la contrainte de minimisation des appels LLM
   sans bénéfice contextuel réel (l'utilisateur n'est pas en train de
   dialoguer à ce moment). Les 15 tests existants restent verts sans
   modification.
2. **`EditAgent` (édition de CV/lettre dans la conversation) non modifié.**
   Le diagramme fourni dans la mission ne mentionne que
   `JobConversationAgent` ; `EditAgent` a son propre contrat d'action
   déterministe (`agent_schemas.py`), hors périmètre explicite de cette phase.
3. **Pas de recommandations après une candidature exécutée
   (`_execute_frozen_apply`) ni après une confirmation.** Ces tours
   n'appellent pas le LLM (Phase 6, intact) ; ajouter un appel dédié pour
   générer des recommandations post-exécution romprait l'invariant "1 appel
   LLM par message contenant une confirmation = 0 appel". Le tour suivant de
   l'utilisateur obtient des recommandations à jour normalement.
4. **`recommended_actions` (Phase 6, vocabulaire fermé) conservé tel quel**,
   toujours peuplé directement par `_propose_apply`/`_ask` — additif avec le
   nouveau champ `recommendations`, aucune rupture de contrat API.

## 7. Tests

Nouveau fichier `tests/test_intelligent_recommendations.py` (57 tests) :
contextualité (ContextBuilder compact + reflète l'état réel, jamais le
document brut), diversité des sujets (22 actions non-enum différentes
acceptées), non-staticité (plus d'import de l'ancien moteur dans
`job_agent_service.py`, deux directives différentes → recommandations
différentes), sécurité (ownership cross-utilisateur, id malformé, actions
interdites), recommandation ≠ exécution, confirmation Phase 6 intacte,
FR/EN, limites (0-3, classement, déduplication), fallback (payload invalide
→ dégradation propre, jamais un crash).

**Note** : cette phase et ses tests ont ensuite été audités et corrigés en
Phase 8 (`benchmarks/PHASE_6.md` §O) — `validate_and_rank` a gagné un
paramètre `language`, une passe de collapse intra-lot par catégorie de
document, une détection étendue des lettres autonomes déjà générées, un
déclassement des affirmations de compétence non confirmées, et
`job_agent_service.py` a gagné `_backfill_top_job_target` et
`_extract_explicit_job_ordinal`. Ce fichier documente l'état **tel que livré
en Phase 7**, avant ces corrections.

## 8. Fichiers

**Ajoutés** : `schemas/recommendation.py`,
`services/conversations/recommendation_engine.py`,
`tests/test_intelligent_recommendations.py`.

**Modifiés** : `schemas/conversation_directive.py` (+`recommendations`),
`schemas/conversation.py` (+`MessageSendResponse.recommendations`, additif),
`services/conversations/job_agent_service.py` (contexte compact dans le
prompt, appel à `validate_and_rank`, retrait de l'appel à l'ancien moteur
dans `_search()`, remplacé par un seul `RecommendedAction` déterministe
ciblé pour la clarification de recherche manquante),
`services/conversations/conversation_service.py` (+`SendResult.recommendations`),
`api/conversations.py` (+construction `IntelligentRecommendation`),
`README.md`, `PROJECT_STRUCTURE.md`.

**Supprimés** : aucun. `services/recommendations.py` et ses 15 tests
restent intacts.

## 9. Résultat des tests (état à la livraison de la Phase 7)

```
PYTHONIOENCODING=utf-8 .venv_test/Scripts/python.exe -m pytest -q
```
→ **1331 passed, 3 skipped, 0 failed, 0 error** (run complet, mono-processus).
`python -m compileall .` OK · `import main` OK (68 routes) · OpenAPI généré
sans régression, endpoints Phase 6 supprimés toujours absents.

*(Pour le résultat après les corrections de la Phase 8, voir
`benchmarks/PHASE_6.md` §O.5.)*
