"""
LOT 2 — additional_skills are computed, returned separately, and NEVER
injected into the CV body or presented as owned skills (constraint #6).
"""

from __future__ import annotations

import json

from cv_models import ATSAnalysis, CVProfile
from services.cv.cv_generator import CVGenerator
from services.generation_service import run_cv_pipeline


PROFILE = CVProfile(
    name="John Doe", email="john@example.com", phone="+212600112233",
    skills=[{"category": "Languages", "skills": ["Python", "SQL"]}],
    experience=[{"company": "Acme", "position": "Engineer",
                 "period": "2021-2023", "achievements": ["Built the billing service"],
                 "technologies": ["Python"]}],
)


def test_additional_skills_not_merged_into_cv_skills(mock_llm):
    opt = {
        "optimized_summary": "s", "career_objective": "o",
        "optimized_achievements": [],
        "additional_skills": ["Kubernetes", "Terraform"],
        "_status": "applied",
    }
    cv = CVGenerator().generate_cv(PROFILE, {"key_skills": []}, ATSAnalysis(), opt, language="en")

    flat = [s for cat in cv["skills"] for s in cat["skills"]]
    assert "Kubernetes" not in flat and "Terraform" not in flat
    assert set(flat) == {"Python", "SQL"}
    assert cv["additional_skills"] == ["Kubernetes", "Terraform"]
    assert cv["ats_optimization"] == "applied"
    assert not any(cat["category"] == "Additional Skills" for cat in cv["skills"])


def test_suggestion_that_candidate_already_owns_is_dropped(mock_llm):
    opt = {"optimized_achievements": [], "additional_skills": ["python", "Kafka"], "_status": "applied"}
    cv = CVGenerator().generate_cv(PROFILE, {"key_skills": []}, ATSAnalysis(), opt, language="en")
    assert cv["additional_skills"] == ["Kafka"]   # "python" already owned -> not a suggestion


def test_pipeline_result_carries_suggestions_and_status(mock_llm, cv_profile_dict):
    from cv_models import GenerateCVRequest
    from tests.conftest import _default_llm_router

    req = GenerateCVRequest(**cv_profile_dict)

    def router(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "ats_content_optimization":
            return json.dumps({
                "optimized_summary": "S", "career_objective": "O",
                "optimized_achievements": [], "additional_skills": ["Kubernetes", "Python"],
            })
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)

    mock_llm.set(router)
    result = run_cv_pipeline(req, "cv-test-1")
    assert result.pdf_bytes[:4] == b"%PDF"
    assert result.ats_status in {"applied", "cache"}
    # "Python" is already in the fixture profile -> filtered out of suggestions
    assert "Python" not in result.additional_skills
    assert "Kubernetes" in result.additional_skills
    assert result.cv_data["language"] == "en"
