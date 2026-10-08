"""
Integration tests: the generation pipeline
(ProfileAnalyzer -> ATSOptimizer -> CVGenerator -> PDFGenerator), LLM mocked.

Focus: language propagation, valid PDF output, and the "no invented
defaults" rule on the generation side.
"""

from __future__ import annotations

import pytest

from cv_models import CVProfile, Education, Experience, Language, SkillCategory
from services.cv.ats_optimizer import ATSOptimizer
from services.cv.cv_generator import CVGenerator
from services.cv.pdf_generator import PDFGenerator
from services.cv.profile_analyzer import ProfileAnalyzer


@pytest.fixture
def profile() -> CVProfile:
    return CVProfile(
        name="John Doe", email="john@example.com", phone="+212600112233",
        education=[Education(
            institution="INSEA", degree="Master", field="Data Science",
            start_date="2022", end_date="2024",
        )],
        experience=[Experience(
            company="Acme Corp", position="Software Engineer",
            period="Jan 2021 - Present",
            achievements=["Shipped the billing service"], technologies=["Python"],
        )],
        skills=[SkillCategory(category="Languages", skills=["Python", "SQL"])],
        languages=[Language(language="English", level="Fluent")],
    )


@pytest.mark.parametrize("language", ["fr", "en"])
def test_full_generation_produces_valid_pdf(mock_llm, profile, language):
    an = ProfileAnalyzer().analyze_profile(profile)
    ats_opt = ATSOptimizer()
    kw = ats_opt.extract_keywords("Python + Kubernetes backend role")
    ats = ats_opt.calculate_match_score(profile, kw, an)
    opt = ats_opt.optimize_content(profile, an, ats, "Python + Kubernetes backend role", language=language)
    cv = CVGenerator().generate_cv(profile, an, ats, opt, language=language)
    assert cv["language"] == language
    pdf = PDFGenerator().render_to_bytes(cv, language=language)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_pdf_section_titles_follow_language(mock_llm, profile):
    from services.cv.pdf_generator import _t

    assert _t("fr")["professional_experience"] == "Expérience Professionnelle"
    assert _t("en")["professional_experience"] == "Professional Experience"
    assert _t("fr")["skills"] == "Compétences"


def test_analyzer_fallback_does_not_invent_content(mock_llm, profile):
    mock_llm.set(lambda p, *, request_type, use_cache: "this is not valid json")
    an = ProfileAnalyzer().analyze_profile(profile)
    # conservative fallback: no invented seniority / strengths / summary
    assert an["seniority_level"] == ""
    assert an["strengths"] == []
    assert an["professional_summary"] == ""  # profile had none -> stays empty


def test_ats_optimizer_no_job_description_skips_llm(mock_llm, profile):
    an = {"professional_summary": "S", "career_objective": "O", "key_skills": ["Python"],
          "expertise_areas": ["Backend"], "seniority_level": ""}
    from cv_models import ATSAnalysis

    opt = ATSOptimizer().optimize_content(profile, an, ATSAnalysis(ats_score=0.0), "", language="en")
    assert "ats_content_optimization" not in mock_llm.calls
    assert "optimized_achievements" in opt
