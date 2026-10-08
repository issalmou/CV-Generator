"""Phase 6 — contextual recommendations (`services/recommendations.py`).

Corrects the Phase-5 "missing skills only" restriction: the full vocabulary is
available, but a recommendation is emitted ONLY when the context genuinely
supports it. Never a fixed list.
"""

from __future__ import annotations

from schemas.skill_analysis import SkillAnalysis, SkillMatch
from services.recommendations import RecommendationContext, build


def _types(ctx: RecommendationContext) -> list[str]:
    return [a.type for a in build(ctx)]


# ---------------------------------------------------------------------------
# strong CV → (almost) nothing
# ---------------------------------------------------------------------------

def test_strong_cv_no_missing_no_letter_only_suggests_the_cover_letter():
    """ATS 91, everything matched, no letter → the ONLY useful next step is the
    cover letter. No improve_summary / review_match / confirm_skill noise."""
    ctx = RecommendationContext(
        has_job_description=True, ats_score=91.0,
        skill_analysis=SkillAnalysis(matched=[SkillMatch(skill="Python")], missing=[], uncertain=[]),
        summary_text=" ".join(["word"] * 60),
        experience_bullet_counts=[3, 4],
        has_matching_letter=False,
    )
    assert _types(ctx) == ["generate_cover_letter"]


def test_strong_cv_with_letter_suggests_nothing():
    ctx = RecommendationContext(
        has_job_description=True, ats_score=88.0,
        skill_analysis=SkillAnalysis(matched=[SkillMatch(skill="Python")], missing=[], uncertain=[]),
        summary_text=" ".join(["word"] * 50),
        experience_bullet_counts=[3],
        has_matching_letter=True,
    )
    assert build(ctx) == []


def test_no_job_description_is_quiet():
    ctx = RecommendationContext(has_job_description=False, ats_score=30.0)
    assert build(ctx) == []


# ---------------------------------------------------------------------------
# weak CV → only the relevant actions
# ---------------------------------------------------------------------------

def test_weak_cv_gets_match_summary_and_skill_actions_only():
    ctx = RecommendationContext(
        has_job_description=True, ats_score=42.0,
        skill_analysis=SkillAnalysis(
            matched=[SkillMatch(skill="Python")],
            missing=["Kubernetes"],
            uncertain=[SkillMatch(skill="AWS Lambda", matched_to="AWS")],
        ),
        summary_text="Backend engineer.",           # weak (< 25 words)
        experience_bullet_counts=[2, 3],
        has_matching_letter=True,                    # so no cover-letter noise
    )
    t = _types(ctx)
    assert "review_match" in t          # ats < 50
    assert "improve_summary" in t       # ats < 60 + short summary
    assert "review_experience" in t     # ats < 55
    assert "confirm_skill" in t         # missing Kubernetes
    assert "review_uncertain_skill" in t
    # never the generic Phase-5 junk
    assert "download_cv" not in t and "generate_cv" not in t


def test_confirm_skill_keeps_the_intelligent_question():
    ctx = RecommendationContext(
        has_job_description=True, ats_score=70.0,
        skill_analysis=SkillAnalysis(matched=[], missing=["Terraform"], uncertain=[]),
        summary_text=" ".join(["word"] * 40),
        experience_bullet_counts=[3],
        has_matching_letter=True,
    )
    acts = build(ctx)
    ks = next(a for a in acts if a.type == "confirm_skill")
    assert ks.skill == "Terraform"
    assert "not in your profile" in ks.reason
    assert ks.question and "which experience" in ks.question.lower()


def test_missing_skill_is_not_can_not_do():
    ctx = RecommendationContext(
        has_job_description=True, ats_score=70.0,
        skill_analysis=SkillAnalysis(matched=[], missing=["Kubernetes"], uncertain=[]),
        summary_text=" ".join(["word"] * 40), experience_bullet_counts=[3],
        has_matching_letter=True,
    )
    ks = next(a for a in build(ctx) if a.type == "confirm_skill")
    assert "does not mean you can't do it" in ks.reason


def test_experience_with_zero_bullets_flags_even_at_a_decent_score():
    ctx = RecommendationContext(
        has_job_description=True, ats_score=72.0,
        summary_text=" ".join(["word"] * 40),
        experience_bullet_counts=[3, 0],            # one empty role
        has_matching_letter=True,
    )
    assert "review_experience" in _types(ctx)


# ---------------------------------------------------------------------------
# job selection (agent context)
# ---------------------------------------------------------------------------

def test_selected_jobs_none_applied_suggests_review_and_apply():
    ctx = RecommendationContext(selected_jobs_count=5, applied_jobs_count=0)
    t = _types(ctx)
    assert "review_selected_jobs" in t
    assert "apply_to_selected_jobs" in t


def test_apply_suggestion_suppressed_when_user_already_commanded_it():
    ctx = RecommendationContext(selected_jobs_count=3, applied_jobs_count=0,
                                user_commanded_apply=True)
    assert "apply_to_selected_jobs" not in _types(ctx)


def test_all_selected_jobs_applied_is_quiet():
    ctx = RecommendationContext(selected_jobs_count=4, applied_jobs_count=4)
    assert build(ctx) == []


def test_weak_job_gets_a_review_job_action():
    ctx = RecommendationContext(
        selected_jobs_count=2, applied_jobs_count=2,
        weak_jobs=[{"id": "j1", "title": "Senior Go Dev", "reason": "stale (posted 40 days ago)"}],
    )
    acts = build(ctx)
    assert [a.type for a in acts] == ["review_job"]
    assert "Senior Go Dev" in acts[0].reason


# ---------------------------------------------------------------------------
# request_information — a real question, never an executed action
# ---------------------------------------------------------------------------

def test_pending_question_becomes_request_information():
    ctx = RecommendationContext(pending_question={
        "skill": "Kubernetes",
        "question": "The job wants 5 years of Kubernetes. Do you use it? In which context and since when?",
        "reason": "Kubernetes is required by the job but not in your profile.",
    })
    acts = build(ctx)
    assert len(acts) == 1 and acts[0].type == "request_information"
    assert acts[0].skill == "Kubernetes"
    assert "5 years of Kubernetes" in acts[0].question


def test_recommendation_is_never_an_execution():
    """build() only reads its context and returns suggestions — it has no db,
    no side effects. (Regression guard: the signature takes only a dataclass.)"""
    import inspect
    sig = inspect.signature(build)
    assert list(sig.parameters) == ["ctx"]
