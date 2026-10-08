# Cartographie des endpoints — LOT 11 (contraintes #8 / #17)

Méthode : pour chaque route → appel frontend (`cv_frontend-main/src`) ? test ? appel interne ? doublon ?
Suppression **uniquement** si inutilisé ET non-frontend ET non-doublon-nécessaire.

Frontend inspecté : `cv_frontend-main/src/app/shared/services/*.ts` +
`API_FRONTEND_GUIDE.tex`. Aucun nouveau namespace créé en Phase 2b (les routes
`.../versions` sont des extensions de `/api/cvs` · `/api/letters`).

## Auth — `/api/auth`
| Endpoint | Frontend | Tests | Verdict |
|---|---|---|---|
| POST `/signup` `/signin` `/forgot-password` `/reset-password` · GET `/me` | ✅ `auth.service.ts` | ✅ | **garder** |

## Extraction
| POST `/api/extract-cv` | ✅ `cv-extraction.service.ts` | ✅ | **garder** |

## Génération / documents — `/api`
| Endpoint | Frontend | Tests | Verdict |
|---|---|---|---|
| POST `/generate-cv` | ✅ `cv-generator.service.ts` | ✅ | **garder** |
| POST `/optimize-existing-cv` | ✅ `cv-generator.service.ts` L23 | ✅ | **doublon fonctionnel de `/generate-cv`** (même `_generate_cv`) MAIS **frontend-wired + documenté (guide §503) + testé** → **garder**. Reco : le frontend converge vers `/generate-cv` dans une future itération front, puis suppression. Supprimer maintenant **casserait le frontend** (interdit par #17). |
| POST `/generate-letter` | ✅ | ✅ | **garder** |
| GET `/cvs` · `/cvs/{id}` · `/cvs/{id}/download` · DELETE `/cvs/{id}` | ✅ `cv-storage.service.ts` | ✅ | **garder** |
| GET `/cvs/{id}/versions` · `/cvs/{id}/versions/{n}/download` | ➕ LOT 8 (nouveau) | ✅ | **garder** — extension, pas de namespace |
| idem `/letters/*` | ✅ / ➕ | ✅ | **garder** |

## Jobs — `/api/jobs`
| POST `/context` `/search` · GET `/sources` `/{id}` · POST `/{id}/apply` · GET `/applications` `/applications/{id}` · POST+DELETE `/{id}/save` · GET `/saved` | ✅ `job-search.service.ts` | ✅ (589 tests job) | **garder** |

## Profil / dashboard utilisateur
| GET/PUT/DELETE `/api/profile` · GET `/api/dashboard` | ⚠️ pas encore câblé côté front (services présents, non appelés) | ✅ | **garder** — sous-système v2.8 délibéré (`REFACTOR_REPORT §13-16`), Redis-cached, référencé par le guide front. Suppression = régression fonctionnelle, pas un nettoyage. |

## Conversations / agent d'édition — `/api/conversations`
| POST `` · GET `` · GET/DELETE `/{id}` · GET `/{id}/messages` · **POST `/{id}/messages`** | ⚠️ module front `module/agent` en place, appels à finaliser | ✅ | **garder** — c'est **la** surface de l'agent d'édition (LOT 9). `POST /{id}/messages` route vers l'agent si le message contient `CV_…`/`LETTER_…`. **Aucun endpoint agent séparé créé** (#17/#18). |

## Admin — `/api/admin` (superadmin only)
| users, ats-boards, providers, stats/*, profiling/*, llm, dashboard | ⚠️ module front admin partiel | ✅ (llm/admin/stats tests) | **garder** — sous-système platform-hardening v2.5→2.8. `GET /api/admin/llm` étendu Phase 2b (routing + circuit). |

## Monitoring
| GET `/api/health` | interne (Docker healthcheck) + guide | ✅ | **garder** |
| GET `/api/stats` | ops / guide | ✅ | **garder** |

---

## Conclusion LOT 11 — MISE À JOUR (décision revue, frontend pas encore commencé)

Chaque endpoint a été passé aux **4 critères** : (a) aucun appel interne indispensable,
(b) aucun test fonctionnel indispensable, (c) aucune dépendance backend cachée,
(d) aucune dépendance au futur flux frontend.

### SUPPRIMÉ : `POST /api/optimize-existing-cv` (1 endpoint)
Seul endpoint à passer **les 4 critères** : alias byte-for-byte de `/api/generate-cv`
(`_generate_cv` avec un simple label de log), tests uniquement redondants, zéro
dépendance cachée, le flux « re-cibler un profil » est couvert par `/api/generate-cv`
(+ `reference` pour une nouvelle version).
Retrait : route + handler + helper mort `_owned_or_403` ; tests → `test_optimize_existing_cv_is_gone`
(assert 404) ; docs (`API_FRONTEND_GUIDE.tex`, `README.md`, `PROJECT_STRUCTURE.md`).

### GARDÉS (échouent au moins un critère)
- **`GET /api/dashboard`** — ~25 tests fonctionnels, hooks `invalidate` internes,
  documenté comme « point d'entrée après connexion » dans le guide front (dépendance au
  futur flux frontend). → garder.
- **`/api/profile`** — `UserProfileService` alimente la recherche job + l'auto-candidature
  (dépendance backend). → garder.
- **jobs / conversations / admin / auth / extraction / génération / documents+versions /
  health / stats** — fonctionnalités cœur du produit ou console d'exploitation. Les
  supprimer serait une **régression**, pas un nettoyage (objectif explicite : réduire la
  surface **sans supprimer de fonctionnalités**).

### Surface API après Phase 2b
- **−1** endpoint (`/api/optimize-existing-cv`).
- **+4** routes d'extension strictement additives (`.../versions`, `.../versions/{n}/download`
  pour CV et lettres) — **aucun nouveau namespace**, l'agent d'édition réutilise
  `POST /api/conversations/{id}/messages`.
- API **plus petite et cohérente**, prête pour le démarrage frontend sur un contrat stable.
