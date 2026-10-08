"""
Phase 5 — deterministic prompt-quality checks.

Two layers:

1. STRUCTURAL — every *generation* prompt is assembled from ``prompt_kit`` and
   therefore carries, verbatim and identically: a ROLE line, an OBJECTIVE, the
   fenced CANDIDATE-DATA block, the ANTI-FABRICATION contract, a strict OUTPUT
   format. Prompts that see a job description also carry the fenced TARGET-JOB
   block with its "not a source of candidate facts" caveat. The blocks must be
   byte-identical across prompts so the prompts cannot contradict each other.

2. FIDELITY — when the LLM returns a fabricated fact (a skill / company / metric
   that is NOT in the candidate data), it must not survive into the structured
   output the rest of the pipeline consumes.

No network, no real LLM — ``mock_llm`` and direct prompt-builder calls.
"""

from __future__ import annotations

import json

import pytest

from services.cv import prompt_kit as pk


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_PROFILE_TEXT = (
    "Name: Sara Idrissi\n"
    "Summary: Backend engineer, 3 years, Python data services.\n"
    "Experience: Backend Engineer @ Atlas Data (2022-Present) — built the "
    "ingestion service; cut the nightly batch from 40 min to 12 min.\n"
    "Skills: Python, SQL, PostgreSQL, Redis\n"
)
_JD = "We need Python, FastAPI, PostgreSQL, Kubernetes and Terraform. English required."


def _profile_obj():
    from cv_models import CVProfile

    return CVProfile(
        name="Sara Idrissi", email="sara@example.com", phone="+212 600 000000",
        professional_summary="Backend engineer focused on data services.",
        experience=[{
            "company": "Atlas Data", "position": "Backend Engineer",
            "period": "2022 - Present",
            "achievements": ["Built the ingestion service", "Cut the nightly batch from 40 min to 12 min"],
            "technologies": ["Python", "PostgreSQL", "Redis"],
        }],
        education=[], projects=[],
        skills=[{"category": "Languages", "skills": ["Python", "SQL"]},
                {"category": "Databases", "skills": ["PostgreSQL", "Redis"]}],
        languages=[{"language": "English", "level": "Fluent"}],
        certifications=[],
    )


def _all_generation_prompts() -> dict[str, str]:
    """Every generation prompt, built with a fixed candidate + a JD."""
    from cv_models import ATSAnalysis
    from services.cv.ats_optimizer import ATSOptimizer
    from services.cv.cv_generator import CVGenerator
    from services.cv.letter_generator import LetterGenerator
    from services.cv.profile_analyzer import ProfileAnalyzer

    profile = _profile_obj()
    analysis = {"professional_summary": "Backend engineer.", "career_objective": "",
                "key_skills": ["Python"], "expertise_areas": ["Backend"], "seniority_level": "Mid"}
    ats = ATSAnalysis(missing_keywords=["Kubernetes", "Terraform"],
                      keywords_to_highlight=["Python", "PostgreSQL"], ats_score=45.0)

    return {
        "profile_analysis": ProfileAnalyzer._build_analysis_prompt(_PROFILE_TEXT, "en"),
        "ats_optimization": ATSOptimizer._build_optimization_prompt(
            profile, analysis, ats, _JD, "en"),
        "ats_optimization_no_jd": ATSOptimizer._build_optimization_prompt(
            profile, analysis, ats, None, "en"),
        "keyword_extraction": ATSOptimizer._build_keyword_extraction_prompt(_JD),
        "skill_categorisation": _categorise_prompt(),
        "cover_letter": LetterGenerator._build_prompt(
            {"name": "Sara Idrissi", "skills": ["Python"]}, _JD,
            {"company_name": "Atlas Data", "position": "Backend Engineer"}, "en"),
    }


def _norm(s: str) -> str:
    """Collapse the f-string line wrapping so multi-line rules can be matched
    as one phrase."""
    return " ".join(s.split())


def _categorise_prompt() -> str:
    """The skill-categorisation prompt is built inline — reach it via a spy."""
    import services.cv.cv_generator as cg

    captured = {}
    orig = cg.call_gemini

    def spy(prompt, *, request_type=None, **kw):
        captured["p"] = prompt
        return json.dumps({"Programming Languages": ["Python", "SQL"]})

    cg.call_gemini = spy
    try:
        cg.CVGenerator._categorise_skills_with_gemini(["Python", "SQL"], "en")
    finally:
        cg.call_gemini = orig
    return captured["p"]


# ---------------------------------------------------------------------------
# 1 — STRUCTURAL
# ---------------------------------------------------------------------------

_CANDIDATE_FACING = {"profile_analysis", "ats_optimization", "ats_optimization_no_jd",
                     "cover_letter"}
_JD_AWARE = {"ats_optimization", "keyword_extraction", "cover_letter"}


def test_every_generation_prompt_has_a_role_and_objective():
    for name, prompt in _all_generation_prompts().items():
        assert prompt.startswith("ROLE:"), f"{name} has no ROLE line"
        assert "OBJECTIVE:" in prompt, f"{name} has no OBJECTIVE"


def test_candidate_facing_prompts_fence_the_candidate_data():
    prompts = _all_generation_prompts()
    for name in _CANDIDATE_FACING:
        p = prompts[name]
        assert "<<<CANDIDATE_DATA>>>" in p and "<<<END_CANDIDATE_DATA>>>" in p, name
        assert "the ONLY source of facts about the candidate" in p, name


def test_candidate_facing_prompts_carry_the_anti_fabrication_contract():
    prompts = _all_generation_prompts()
    for name in _CANDIDATE_FACING:
        p = _norm(prompts[name])
        assert "ANTI-FABRICATION — ABSOLUTE" in p, name
        assert "A requirement in TARGET JOB is NOT evidence the candidate has it" in p, name
        assert "NEVER derive a number the candidate did not state" in p, name


def test_jd_aware_prompts_fence_the_job_and_mark_it_non_factual():
    prompts = _all_generation_prompts()
    for name in _JD_AWARE:
        p = prompts[name]
        assert "TARGET JOB" in p or "JOB_DESCRIPTION" in p, name
    # the two that put the JD through prompt_kit also carry the caveat
    for name in ("ats_optimization", "cover_letter"):
        assert "NOT a source of candidate" in _norm(prompts[name]), name


def test_structured_prompts_demand_json_only():
    prompts = _all_generation_prompts()
    for name in ("profile_analysis", "ats_optimization", "keyword_extraction",
                 "skill_categorisation"):
        assert "return ONLY one valid JSON object" in prompts[name], name


def test_the_shared_blocks_are_byte_identical_across_prompts():
    """No drift: wherever ANTI_FABRICATION / candidate-data framing appears it is
    the exact same text — that is what guarantees the prompts can't contradict."""
    prompts = _all_generation_prompts()
    for name in _CANDIDATE_FACING:
        assert pk.ANTI_FABRICATION.strip() in prompts[name], name
    for name in ("ats_optimization", "cover_letter"):
        assert 'TARGET JOB — context for tailoring ONLY.' in prompts[name], name


def test_no_prompt_tells_the_model_to_add_missing_keywords():
    for name, prompt in _all_generation_prompts().items():
        low = prompt.lower()
        assert "integrate missing keywords" not in low, name
        assert "add the missing keywords" not in low, name
        assert "include all keywords" not in low, name


def test_ats_prompt_routes_missing_keywords_to_suggestions_only():
    p = _all_generation_prompts()["ats_optimization"]
    assert "MUST NOT write any of these" in p
    assert "disqualifying fabrication" in p
    assert '"additional_skills"' in p and "SUGGESTIONS" in p


def test_cover_letter_prompt_is_not_a_cv_restatement_and_stays_consistent():
    p = _norm(_all_generation_prompts()["cover_letter"])
    assert "Do NOT walk through the CV section by section" in p
    assert "must NOT copy a sentence from it" in p
    assert "Stay consistent with the CV: same employers, same roles, same seniority" in p
    # if the JD wants a skill the candidate lacks, the letter must not claim it
    assert "must NOT claim a requirement the candidate never demonstrated" in p


def test_no_jd_prompt_does_not_invent_a_target():
    p = _norm(_all_generation_prompts()["ats_optimization_no_jd"])
    assert "general-purpose (no target job)" in p
    assert "Do NOT invent a target company or role" in p
    assert "TARGET JOB — context" not in p   # no JD block when there is no JD


# ---------------------------------------------------------------------------
# 2 — FIDELITY: a fabricated LLM answer must not reach structured output
# ---------------------------------------------------------------------------

def test_profile_analyzer_falls_back_conservatively_on_a_bad_answer(mock_llm):
    """An unusable LLM answer must degrade to the conservative local fallback —
    which only ever echoes data the candidate actually provided (no invented
    seniority, year count, strength or templated summary)."""
    from services.cv.profile_analyzer import ProfileAnalyzer

    mock_llm.set(lambda p, *, request_type, use_cache, **kw: "the model refused to answer")
    out = ProfileAnalyzer().analyze_profile(_profile_obj())
    assert out["seniority_level"] == ""
    assert out["years_of_experience"] == 0
    assert out["strengths"] == []
    assert out["professional_summary"] in ("", "Backend engineer focused on data services.")


def test_ats_optimizer_local_fallback_never_injects_missing_skills(mock_llm):
    from cv_models import ATSAnalysis
    from services.cv.ats_optimizer import ATS_LOCAL_FALLBACK, ATSOptimizer

    mock_llm.set(lambda p, *, request_type, use_cache, **kw: "<not json>")
    ats = ATSAnalysis(missing_keywords=["Kubernetes", "Terraform"], ats_score=30.0)
    out = ATSOptimizer().optimize_content(
        _profile_obj(), {"professional_summary": "Backend engineer."}, ats,
        "Kubernetes + Terraform role", language="en")
    assert out["_status"] == ATS_LOCAL_FALLBACK
    assert out["additional_skills"] == []
    blob = json.dumps(out).lower()
    assert "kubernetes" not in blob and "terraform" not in blob


def test_skill_categoriser_is_bypassed_when_the_profile_has_skills(mock_llm):
    """The strongest guarantee: a candidate who provided categorised skills
    never has them near the LLM at all — _build_skills copies them straight
    through, so a fabricating model has no entry point."""
    from services.cv.cv_generator import CVGenerator

    called = {"n": 0}

    def spy(prompt, *, request_type, use_cache=True, **kw):
        called["n"] += 1
        return json.dumps({"Cloud": ["Kubernetes"]})   # would-be fabrication

    mock_llm.set(spy)
    profile = _profile_obj()   # has two real skill categories
    out = CVGenerator()._build_skills(
        profile, {"key_skills": ["Python"]}, None, {}, "en")
    flat = {s for cat in out for s in cat["skills"]}
    assert flat == {"Python", "SQL", "PostgreSQL", "Redis"}   # exactly the real ones
    assert "Kubernetes" not in flat
    assert called["n"] == 0                                    # LLM never consulted


@pytest.mark.parametrize("lang,expected", [("fr", "French"), ("en", "English")])
def test_language_line_is_consistent_everywhere(lang, expected):
    line = pk.language_line(lang)
    assert expected in line
    assert "Keep proper nouns exactly as written" in line
