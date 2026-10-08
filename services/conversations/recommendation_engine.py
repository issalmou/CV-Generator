"""Service: the intelligent-recommendation engine (Phase 7).

Splits the recommendation system exactly the way the mission asks:

    ContextBuilder   → build_context() : a COMPACT, curated summary of the
                       user's CV/letter/application state — never the raw
                       rows, never the whole DB. Rendered into the SAME
                       prompt that produces the ConversationDirective (see
                       job_agent_service._build_prompt), so recommendations
                       cost ZERO extra call_gemini calls.

    LLM              → produces 0-3 raw IntelligentRecommendation candidates
                       as part of that one ConversationDirective call. The
                       LLM understands context and proposes; it never
                       executes anything (there is no code path anywhere
                       that dispatches on `action` to run something).

    Validator/Ranker → validate_and_rank() : deterministic Python. Drops a
                       recommendation whose target does not belong to the
                       caller, whose action matches a destructive/system
                       verb (defense in depth — even though nothing ever
                       executes it), or that contradicts state the backend
                       already knows about (e.g. "generate a cover letter"
                       for a job that already has one attached). Dedupes,
                       ranks by priority then confidence, caps to 3.

Nothing here ever touches call_gemini, services/llm/, or executes a
recommendation — it only shapes what the LLM is told and what survives to
the API response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    GeneratedCV, GeneratedLetter, JobApplication, JobOffer, SavedJob, User, UserProfile,
)
from schemas.recommendation import IntelligentRecommendation
from services.cache_service import cache
from services.id_utils import is_valid_uuid

# Phase 8/9 fix — a "generate a cover letter"/"generate a CV" recommendation
# is only ever a contradiction if it means CREATE; "review"/"improve"/"adapt"
# a document that already exists is a perfectly legitimate, different
# suggestion and must not be dropped just because one exists.
_GENERATE_KEYWORDS = ("generate", "create", "write", "draft",
                      "rédig", "créer", "écrire", "redig")
_LETTER_GENERATE_KEYWORDS = _GENERATE_KEYWORDS  # kept as an alias for callers

_MAX_RECOMMENDATIONS = 3
_PRIORITY_WEIGHT = {"high": 2, "medium": 1, "low": 0}
_OWNERSHIP_TARGET_TYPES = {"job", "cv", "letter", "application"}

# Defense in depth: a recommendation is only ever a displayed suggestion (see
# module docstring — nothing dispatches on `action`), but one naming a
# destructive/system verb is dropped outright rather than ever reaching the
# frontend.
_FORBIDDEN_ACTION_RE = re.compile(
    # Audit fix (§5/§6 niveau B): the previous version matched only the bare
    # infinitive/imperative form of each verb ("delete", "supprim(er)?",
    # "exec(ute)?", literal "shutdown") — a conjugated or differently-spaced
    # variant slipped straight through: "deleting_all_data", "executing_sql",
    # "shut_down_server", "supprimez_le_compte" (vous), "supprimons_tout"
    # (nous), "supprimé_le_compte" (past participle) all bypassed it. Widened
    # each verb to its stem + `\w*` (delet/exec/remov/format/truncat/
    # supprim/effa[cç]) so any conjugation/suffix is covered, and "shutdown"
    # now tolerates a separator ("shut down", "shut-down"). Verified against
    # both the original catch-list and these new probes — see
    # tests/test_intelligent_recommendations.py.
    r"delet\w*[_ ]?(?:the|my|all|every)?[_ ]?(all|account|user|profile|document|data)|"
    r"drop[_ ]?table|shell|"
    r"exec\w*[_ ]?sql|remov\w*[_ ]?account|format\w*[_ ]?disk|sudo|rm[_ ]?-rf|"
    r"\brm\b|truncat\w*|grant|revoke|shut[_ -]?down|"
    r"(?<![a-zA-Z])kill(?:ed|ing|s)?(?![a-zA-Z])|"
    r"supprim\w*[_ ]?(?:le|la|les|du|de|des)?[_ ]?(tout|compte|utilisateur|profil|donn[eé]es)|"
    r"effa[cç]\w*[_ ]?(?:le|la|les|du|de|des)?[_ ]?(tout|compte|utilisateur|profil|donn[eé]es)",
    re.I,
)

# Phase 8 — a coarse "what document is this about" bucket, used only to
# collapse two recommendations that both concern the SAME document for the
# SAME target into one (§17 "doublon" / "contradiction": e.g. the LLM
# returning both "generate_cover_letter" and "review_cover_letter" for the
# same job in the same turn). Best-effort text matching, not a vocabulary —
# it never rejects an action, only deduplicates within one turn's batch.
_DOC_CATEGORY_KEYWORDS = {
    "letter": ("letter", "lettre"),
    "cv": ("cv", "resume", "résumé", "curriculum"),
}

# Phase 8 — same local, no-LLM heuristic style as job_agent_service._detect_lang,
# duplicated (not imported) to avoid coupling this module to that one — used
# to catch a recommendation whose own text is in the wrong language for the
# conversation (§10/§17 "incohérence multilingue").
_FR_MARKERS = (
    " le ", " la ", " les ", " des ", " une ", " un ", " je ", " vous ",
    " êtes ", " et ", " avec ", " pour ", " votre ", " vos ", " êtes ",
    "é", "è", "ê", "à", "ç",
)
_EN_MARKERS = (
    " the ", " a ", " an ", " you ", " are ", " and ", " with ", " for ",
    " your ", " is ", " it ", " this ",
)
_MIN_WORDS_FOR_LANG_CHECK = 4   # too short a string -> heuristic is unreliable


def _detect_text_lang(text: str) -> str | None:
    low = f" {(text or '').lower()} "
    if len(low.split()) < _MIN_WORDS_FOR_LANG_CHECK:
        return None   # not enough signal — never penalise a short label
    fr = sum(low.count(m) for m in _FR_MARKERS)
    en = sum(low.count(m) for m in _EN_MARKERS)
    if fr == en:
        return None
    return "fr" if fr > en else "en"


def _doc_category(action: str) -> str | None:
    for category, keywords in _DOC_CATEGORY_KEYWORDS.items():
        if any(k in action for k in keywords):
            return category
    return None


@dataclass
class RecommendationDbContext:
    """The compact, already-summarised strings a prompt embeds — never the
    raw ORM rows. Every field is a short human-readable line, not a document
    dump."""
    cv_summary: str
    letter_summary: str
    application_summary: str


def build_context(db: Session, user: User) -> RecommendationDbContext:
    """ContextBuilder — a handful of cheap COUNT/aggregate queries, never a
    full document, never more than one row's worth of detail per document
    type. This is what keeps the prompt compact per the mission's explicit
    warning against sending the whole DB to the LLM."""
    cv_count = db.scalar(
        select(func.count()).select_from(GeneratedCV).where(GeneratedCV.user_id == user.id)
    ) or 0
    latest_cv = db.scalar(
        select(GeneratedCV).where(GeneratedCV.user_id == user.id)
        .order_by(GeneratedCV.created_at.desc()).limit(1)
    )
    letter_count = db.scalar(
        select(func.count()).select_from(GeneratedLetter).where(GeneratedLetter.user_id == user.id)
    ) or 0
    latest_letter = db.scalar(
        select(GeneratedLetter).where(GeneratedLetter.user_id == user.id)
        .order_by(GeneratedLetter.created_at.desc()).limit(1)
    )
    app_rows = db.execute(
        select(JobApplication.status, func.count())
        .where(JobApplication.user_id == user.id)
        .group_by(JobApplication.status)
    ).all()
    app_by_status = {status: n for status, n in app_rows}

    cv_summary = (
        f"{cv_count} CV(s) on file; most recent: ats_score="
        f"{latest_cv.ats_score if latest_cv and latest_cv.ats_score is not None else 'n/a'}, "
        f"language={latest_cv.language if latest_cv else 'n/a'}"
        if cv_count else "no CV generated yet"
    )
    letter_summary = (
        f"{letter_count} cover letter(s) on file; most recent language="
        f"{latest_letter.language if latest_letter else 'n/a'}"
        if letter_count else "no cover letter generated yet"
    )
    application_summary = (
        f"applications by status: {app_by_status}" if app_by_status
        else "no applications prepared yet"
    )
    return RecommendationDbContext(
        cv_summary=cv_summary, letter_summary=letter_summary,
        application_summary=application_summary,
    )


def render_context_block(ctx: RecommendationDbContext) -> str:
    """The compact text block embedded in the directive prompt."""
    return (
        f"CV STATUS: {ctx.cv_summary}\n"
        f"COVER LETTER STATUS: {ctx.letter_summary}\n"
        f"APPLICATIONS STATUS: {ctx.application_summary}"
    )


# ---------------------------------------------------------------------------
# Validator / Ranker — deterministic, no LLM, no execution
# ---------------------------------------------------------------------------

def validate_and_rank(
    db: Session, user: User, raw: list[IntelligentRecommendation],
    *, language: str | None = None,
) -> list[IntelligentRecommendation]:
    """Convenience wrapper: filter/validate, THEN rank+cap, in one call — what
    every direct (non-conversational-agent) caller and test wants.

    ``job_agent_service.handle()`` calls the two halves (:func:`_validate_candidates`
    then :func:`rank_and_cap`) separately instead, because job-target
    resolution (explicit ordinal/company override, ambiguity clarification,
    post-search backfill) must happen to the FILTERED candidates BEFORE
    ranking/capping — otherwise a still-ambiguous "which job?" placeholder
    could rank into the top 3 on raw confidence alone and crowd out a
    perfectly good, already-resolved suggestion, and the ranking could never
    take into account whether a candidate ended up needing clarification."""
    return rank_and_cap(_validate_candidates(db, user, raw, language=language))


def _validate_candidates(
    db: Session, user: User, raw: list[IntelligentRecommendation],
    *, language: str | None = None,
) -> list[IntelligentRecommendation]:
    """Ownership / safety / state-consistency / language / dedup filtering.
    Never raises on bad LLM output — a hallucinated target, an unknown
    target_type, or a forbidden action simply gets dropped, never executed
    (there is nothing here that *could* execute one).

    ``language`` (Phase 8, e.g. "fr"/"en" from the caller's own
    `_detect_lang(message)`) is optional and additive: when given, a
    recommendation whose own title/message text is confidently detected in
    the OTHER language is dropped (§10/§17 "incohérence multilingue") —
    never applied when the signal is too weak (short strings) to avoid
    penalising a perfectly good short label.

    Deliberately returns an UNRANKED, UNCAPPED list — see :func:`rank_and_cap`."""
    seen: set[tuple[str, str | None]] = set()
    candidates: list[IntelligentRecommendation] = []

    for rec in raw or []:
        if not isinstance(rec, IntelligentRecommendation):
            continue
        action = rec.action.strip().lower()
        if not action or _FORBIDDEN_ACTION_RE.search(action):
            continue

        if rec.target_id and rec.target_type in _OWNERSHIP_TARGET_TYPES:
            if not _owns_target(db, user, rec.target_type, rec.target_id):
                continue
        elif rec.target_id and rec.target_type not in _OWNERSHIP_TARGET_TYPES:
            # a target_id without a recognised, ownable target_type is noise,
            # not a reason to drop an otherwise-useful suggestion.
            rec = rec.model_copy(update={"target_id": None})

        if _contradicts_existing_state(db, user, rec, action):
            continue

        rec = _downgrade_unconfirmed_skill_claim(db, user, rec, language=language)

        if language:
            # Audit fix (§13): `reason` and `question` are just as user-facing
            # as `title`/`message` (a `requires_information=True` recommendation
            # displays `question` directly) — checking only title+message let a
            # mismatched reason/question slip through when the short label
            # itself was neutral/too-short-to-detect.
            text_lang = _detect_text_lang(
                f"{rec.title} {rec.message} {rec.reason} {rec.question or ''}"
            )
            if text_lang and text_lang != language:
                continue

        if any(k in action for k in ("apply", "postul", "delete", "supprim")):
            rec = rec.model_copy(update={"requires_confirmation": True})

        key = (action, rec.target_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(rec)

    return candidates


def rank_and_cap(
    candidates: list[IntelligentRecommendation],
) -> list[IntelligentRecommendation]:
    """Sort, collapse same-document duplicates, cap to 3.

    Ranking key (highest first): priority, THEN "is this actually
    actionable right now" (a recommendation still needing clarification —
    ``requires_information`` — never outranks an equally-prioritised one
    that is fully resolved, no matter its raw ``confidence``: a confident
    guess pointed at an unresolved target is not more useful than a
    slightly-less-confident one the user can act on immediately), THEN
    confidence. Call this only AFTER any target resolution (ordinal/company
    override, ambiguity-to-clarification) has already run — resolving
    targets first and ranking last is what lets this tiebreaker mean
    anything; ranking on the LLM's raw, not-yet-resolved guess would let an
    about-to-become-a-question placeholder crowd out a solid suggestion on
    confidence alone."""
    candidates = sorted(
        candidates,
        key=lambda r: (
            _PRIORITY_WEIGHT.get(r.priority, 0),
            0 if r.requires_information else 1,
            r.confidence,
        ),
        reverse=True,
    )

    # Collapse two survivors that concern the SAME document for the SAME
    # target (e.g. "generate_cover_letter" + "review_cover_letter" for one
    # job in one turn) — keep only the highest-ranked of the pair, processed
    # in already-sorted order so "best wins".
    doc_seen: set[tuple[str | None, str | None, str]] = set()
    kept: list[IntelligentRecommendation] = []
    for rec in candidates:
        category = _doc_category(rec.action.lower())
        if category is not None:
            doc_key = (rec.target_type, rec.target_id, category)
            if doc_key in doc_seen:
                continue
            doc_seen.add(doc_key)
        kept.append(rec)

    return kept[:_MAX_RECOMMENDATIONS]


def _owns_target(db: Session, user: User, target_type: str, target_id: str) -> bool:
    if not is_valid_uuid(target_id):
        return False
    if target_type == "job":
        return db.scalar(
            select(SavedJob.id).where(
                SavedJob.user_id == user.id, SavedJob.job_offer_id == target_id
            ).limit(1)
        ) is not None
    if target_type == "cv":
        return db.scalar(
            select(GeneratedCV.id).where(
                GeneratedCV.id == target_id, GeneratedCV.user_id == user.id
            ).limit(1)
        ) is not None
    if target_type == "letter":
        return db.scalar(
            select(GeneratedLetter.id).where(
                GeneratedLetter.id == target_id, GeneratedLetter.user_id == user.id
            ).limit(1)
        ) is not None
    if target_type == "application":
        return db.scalar(
            select(JobApplication.id).where(
                JobApplication.id == target_id, JobApplication.user_id == user.id
            ).limit(1)
        ) is not None
    return False


def _contradicts_existing_state(
    db: Session, user: User, rec: IntelligentRecommendation, action: str,
) -> bool:
    """A cheap safety net (the prompt already tells the LLM the current
    CV/letter/application state so it should not contradict itself — this
    is the backend's second check, not the primary mechanism)."""
    if rec.target_type != "job" or not rec.target_id:
        return False
    if any(k in action for k in ("apply", "postul")):
        existing = db.scalar(
            select(JobApplication.id).where(
                JobApplication.user_id == user.id,
                JobApplication.job_offer_id == rec.target_id,
            ).limit(1)
        )
        if existing is not None:
            return True
    if any(k in action for k in ("letter", "lettre")):
        # only a CREATE-type suggestion is a contradiction — "review"/
        # "improve"/"adapt" an existing letter is a different, legitimate
        # suggestion and must survive even when a letter already exists.
        is_generate = any(k in action for k in _LETTER_GENERATE_KEYWORDS)
        if not is_generate:
            return False
        app = db.scalar(
            select(JobApplication).where(
                JobApplication.user_id == user.id,
                JobApplication.job_offer_id == rec.target_id,
            ).order_by(JobApplication.created_at.desc()).limit(1)
        )
        if app is not None and app.letter_id:
            return True
        # Phase 8 fix: a letter can exist WITHOUT being attached to any
        # application yet. There is no direct job_offer_id FK on
        # GeneratedLetter (only `job_hash`, a digest of the job description
        # text) — compute it the same way application_service._prepare_letter
        # does when it writes one, and check for a match.
        offer = db.get(JobOffer, rec.target_id)
        if offer is not None:
            job_text = (offer.description or offer.title or "")[:8000]
            job_hash = cache.digest(job_text)
            existing_letter = db.scalar(
                select(GeneratedLetter.id).where(
                    GeneratedLetter.user_id == user.id,
                    GeneratedLetter.job_hash == job_hash,
                ).limit(1)
            )
            if existing_letter is not None:
                return True
    if any(k in action for k in ("cv", "resume", "résumé", "curriculum")):
        # Phase 9 fix (§8 "CV" — this branch did not exist before): same
        # generate-vs-review distinction as letters. "adapt_cv_to_job" /
        # "improve_cv" / "review_cv" stay legitimate even when a CV already
        # exists for this job; only a CREATE-type suggestion is redundant.
        is_generate = any(k in action for k in _GENERATE_KEYWORDS)
        if not is_generate:
            return False
        offer = db.get(JobOffer, rec.target_id)
        if offer is not None:
            job_text = (offer.description or offer.title or "")[:8000]
            job_hash = cache.digest(job_text)
            existing_cv = db.scalar(
                select(GeneratedCV.id).where(
                    GeneratedCV.user_id == user.id,
                    GeneratedCV.job_hash == job_hash,
                ).limit(1)
            )
            if existing_cv is not None:
                return True
    return False


def _downgrade_unconfirmed_skill_claim(
    db: Session, user: User, rec: IntelligentRecommendation,
    *, language: str | None = None,
) -> IntelligentRecommendation:
    """Phase 8 fix (§16 anti-hallucination, partial): the engine cannot
    fact-check free text (a claim like "your profile shows 7 years of
    Python" inside `message` is not something regex can verify) — but when
    the LLM names a specific skill in `parameters` AND presents the
    recommendation as a settled fact (`requires_information=False`) about a
    skill that is NOT in the user's own stored profile, that confidence is
    unearned. Downgrade it into a question (`requires_information=True`)
    instead of dropping it outright — the underlying nudge (this skill
    matters) is usually still worth surfacing, just not as an assertion.

    Audit fix (§13): the fallback question used to be hardcoded English —
    in an all-French conversation, a recommendation could downgrade into an
    English question. Localized by the same `language` the caller already
    detected from the user's own message."""
    skill = rec.parameters.get("skill") if isinstance(rec.parameters, dict) else None
    if not skill or not isinstance(skill, str) or rec.requires_information:
        return rec
    profile = db.get(UserProfile, user.id)
    known_skills = {s.strip().lower() for s in (profile.skills if profile else [])}
    if skill.strip().lower() in known_skills:
        return rec   # the claim matches structured data we actually have
    fallback_question = (
        f"Avez-vous vraiment de l'expérience avec {skill} ?" if language == "fr"
        else f"Do you actually have experience with {skill}?"
    )
    return rec.model_copy(update={
        "requires_information": True,
        "question": rec.question or fallback_question,
    })
