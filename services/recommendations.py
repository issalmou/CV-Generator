"""Service: contextual recommendations (Phase 6).

Replaces the Phase-5 "missing skills only" list. A recommendation is a
**suggestion the frontend renders as a button / prompt — never an action the
backend performed**. `build()` is fully deterministic: each rule only fires when
the context genuinely supports it, so a strong CV with a JD and no missing
skills yields `[]` (or at most one truly useful next step), never the old fixed
`review_match / improve_summary / generate_cv / download_cv` list.

The vocabulary is closed:

    review_match            CV fits the job poorly
    improve_summary         the summary is weak for this job
    review_experience       an experience could be presented better / has no bullets
    confirm_skill           a job skill is not in the profile — ask if the user has it
    review_uncertain_skill  a fuzzy skill match to disambiguate
    generate_targeted_cv    no CV is tailored to this job yet
    generate_cover_letter   a target job has no cover letter
    review_cover_letter     a letter exists but its CV moved on since
    review_selected_jobs    several jobs are selected and none applied to
    apply_to_selected_jobs  selected jobs are ready to apply to
    review_job              a selected job looks stale / a weak match
    request_information     the agent needs a fact from the user to proceed

Applying anything always needs an explicit, confirmed user decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.skill_analysis import RecommendedAction, SkillAnalysis

# thresholds — deliberately conservative so a suggestion only appears when it
# would actually help.
_MATCH_POOR = 50.0
_SUMMARY_WEAK = 60.0
_EXPERIENCE_WEAK = 55.0
_SUMMARY_MIN_WORDS = 25
_MAX_CONFIRM_SKILLS = 8
_MAX_UNCERTAIN = 5


@dataclass
class RecommendationContext:
    """Everything a rule might need; every field optional. A rule that needs a
    field the caller did not provide simply does not fire."""

    # CV / job-description context
    has_job_description: bool = False
    ats_score: float | None = None
    skill_analysis: SkillAnalysis | dict | None = None
    summary_text: str = ""
    experience_bullet_counts: list[int] = field(default_factory=list)
    has_matching_cv: bool | None = None        # a CV tailored to this JD exists
    has_matching_letter: bool | None = None    # a letter for this JD/job exists
    letter_outdated: bool = False              # letter older than its CV's latest version

    # agent / job-selection context
    selected_jobs_count: int = 0
    applied_jobs_count: int = 0
    weak_jobs: list[dict[str, Any]] = field(default_factory=list)   # {id,title,reason}
    user_commanded_apply: bool = False         # the user already said "apply ..."

    # agent needs a fact to continue
    pending_question: dict[str, Any] | None = None   # {topic|skill, question, reason}


def _as_analysis(value: SkillAnalysis | dict | None) -> SkillAnalysis | None:
    if value is None:
        return None
    if isinstance(value, SkillAnalysis):
        return value
    try:
        return SkillAnalysis.model_validate(value)
    except Exception:  # noqa: BLE001 — a malformed analysis just means "no skill actions"
        return None


def build(ctx: RecommendationContext) -> list[RecommendedAction]:
    out: list[RecommendedAction] = []
    analysis = _as_analysis(ctx.skill_analysis)
    ats = ctx.ats_score

    # --- CV ⇄ job-description --------------------------------------------------
    if ctx.has_job_description:
        if ats is not None and ats < _MATCH_POOR:
            out.append(RecommendedAction(
                type="review_match",
                reason=(f"This CV scores {ats:.0f}/100 against the job. Several "
                        "requirements aren't reflected — worth a targeted pass."),
            ))
        if (ats is not None and ats < _SUMMARY_WEAK
                and _word_count(ctx.summary_text) < _SUMMARY_MIN_WORDS):
            out.append(RecommendedAction(
                type="improve_summary",
                reason=("Your professional summary is short and generic for this "
                        "role — a tighter 3–4 sentence summary would land better."),
            ))
        weak_exp = (
            any(n == 0 for n in ctx.experience_bullet_counts)
            or (ats is not None and ats < _EXPERIENCE_WEAK)
        )
        if weak_exp and ctx.experience_bullet_counts:
            out.append(RecommendedAction(
                type="review_experience",
                reason=("At least one experience has no achievement bullets or "
                        "reads thin for this job — expanding it with real results "
                        "would help."),
            ))
        if analysis is not None:
            for skill in analysis.missing[:_MAX_CONFIRM_SKILLS]:
                out.append(RecommendedAction(
                    type="confirm_skill", skill=skill,
                    reason=(f"“{skill}” is requested by the job but is not in your "
                            "profile. It does not mean you can't do it — only that "
                            "it isn't there yet."),
                    question=(f"Do you actually use {skill}? If yes, tell me in "
                              "which experience, project or company, and roughly "
                              "when — so I can add it correctly."),
                ))
            for m in analysis.uncertain[:_MAX_UNCERTAIN]:
                out.append(RecommendedAction(
                    type="review_uncertain_skill", skill=m.skill,
                    reason=(f"The job asks for “{m.skill}”; your profile has "
                            f"“{m.matched_to}”, which may or may not be the same."),
                    question=(f"Is “{m.matched_to}” the same as the job's "
                              f"“{m.skill}”, or are they different?"),
                ))
        if ctx.has_matching_cv is False:
            out.append(RecommendedAction(
                type="generate_targeted_cv",
                reason="No CV is tailored to this job yet — generating one aligns "
                       "your real experience to what it asks for.",
            ))

    # --- cover letter -------------------------------------------------------
    if ctx.has_matching_letter is False and (ctx.has_job_description
                                             or ctx.selected_jobs_count > 0):
        out.append(RecommendedAction(
            type="generate_cover_letter",
            reason="There's no cover letter for this job yet — a tailored one "
                   "makes the application complete.",
        ))
    elif ctx.has_matching_letter and ctx.letter_outdated:
        out.append(RecommendedAction(
            type="review_cover_letter",
            reason="Your CV has a newer version than the cover letter was written "
                   "from — the letter may no longer match it.",
        ))

    # --- selected jobs -----------------------------------------------------
    unapplied = max(0, ctx.selected_jobs_count - ctx.applied_jobs_count)
    if ctx.selected_jobs_count >= 3 and ctx.applied_jobs_count == 0:
        out.append(RecommendedAction(
            type="review_selected_jobs",
            reason=(f"You have {ctx.selected_jobs_count} jobs selected and haven't "
                    "applied to any — a quick review before applying is worth it."),
        ))
    if unapplied >= 1 and not ctx.user_commanded_apply:
        out.append(RecommendedAction(
            type="apply_to_selected_jobs",
            reason=(f"{unapplied} selected job(s) are ready to apply to. I'll "
                    "prepare each application for you to submit — just confirm."),
        ))
    for job in ctx.weak_jobs[:5]:
        out.append(RecommendedAction(
            type="review_job", skill=None,
            reason=(f"“{job.get('title') or job.get('id')}” looks "
                    f"{job.get('reason', 'uncertain')} — check it before applying."),
        ))

    # --- agent needs a fact ----------------------------------------------
    if ctx.pending_question:
        q = ctx.pending_question
        out.append(RecommendedAction(
            type="request_information",
            skill=q.get("skill") or q.get("topic"),
            reason=q.get("reason", "I need one detail to continue."),
            question=q.get("question") or "Could you give me that detail?",
        ))

    return out


def _word_count(text: str) -> int:
    return len((text or "").split())
