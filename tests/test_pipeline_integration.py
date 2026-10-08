"""
Integration tests: the full extraction pipeline
(ResumeParserPipeline.parse_text), LLM mocked.

Covers: the section-scoped LLM-call budget, the no-fabrication contract,
the language input vs. auto-detection behaviour, and the Level 5 fallback
trigger / non-trigger.
"""

from __future__ import annotations

import json

import pytest

from services.cv.resume_parser_pipeline import ResumeParserPipeline

_SECTION_CALLS = {
    "resume_parse_experience", "resume_parse_education",
    "resume_parse_projects", "resume_parse_skills",
}


def test_only_section_scoped_calls_no_whole_resume_call(mock_llm, resume_text_en):
    ResumeParserPipeline().parse_text(resume_text_en, language="en")
    assert mock_llm.calls.count("resume_parse_fallback") == 0
    # one call per non-empty structured section, nothing else
    assert set(mock_llm.calls) <= _SECTION_CALLS
    assert len(mock_llm.calls) == len(set(mock_llm.calls)) <= 4


def test_contact_certs_languages_never_hit_the_llm(mock_llm, resume_text_en):
    result = ResumeParserPipeline().parse_text(resume_text_en, language="en")
    assert result.cv_profile.email == "john.doe@example.com"
    assert result.cv_profile.languages
    assert result.cv_profile.certifications
    # contact / languages / certifications are 100% local
    assert not any(c in mock_llm.calls for c in ("contact", "languages", "certifications"))


def test_skills_go_through_a_section_scoped_call(mock_llm, resume_text_en):
    ResumeParserPipeline().parse_text(resume_text_en, language="en")
    assert "resume_parse_skills" in mock_llm.calls
    # the skills prompt only ever contains the skills block
    skills_prompt = next(p for rt, p in mock_llm.prompts if rt == "resume_parse_skills")
    assert "WORK EXPERIENCE" not in skills_prompt
    assert "Python" in skills_prompt


def test_contact_fields_extracted(mock_llm, resume_text_en):
    prof = ResumeParserPipeline().parse_text(resume_text_en, language="en").cv_profile
    assert prof.name == "John Doe"
    assert prof.phone and "112233" in prof.phone
    assert prof.linkedin and "linkedin.com/in/johndoe" in prof.linkedin
    assert prof.github and "github.com/johndoe" in prof.github
    assert prof.nationality == "Moroccan"
    assert prof.address and prof.address.startswith("12 Rue de la Paix")


def test_language_hint_wins_but_detection_is_still_reported(mock_llm, resume_text_fr):
    # French document, caller asks for 'en'
    result = ResumeParserPipeline().parse_text(resume_text_fr, language="en")
    assert result.language == "en"
    assert result.detected_language == "fr"


def test_no_language_hint_uses_detection(mock_llm, resume_text_fr):
    result = ResumeParserPipeline().parse_text(resume_text_fr)
    assert result.language == result.detected_language == "fr"


def test_missing_data_is_never_fabricated(mock_llm):
    """A skills-only resume: no experience/education headers -> no fabrication, no fallback."""
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps(
        {request_type.split("_")[-1]: []}
    ) if request_type.startswith("resume_parse") else "{}")

    text = "JANE ROE\njane@x.com\n\nSKILLS\nPython, SQL\n"
    result = ResumeParserPipeline().parse_text(text, language="en")

    assert result.cv_profile.experience == []
    assert result.cv_profile.education == []
    assert result.gemini_fallback_used is False
    assert "resume_parse_fallback" not in mock_llm.calls
    # the gap is surfaced, not hidden
    assert result.confidence["experience_confidence"] == 0


def test_level5_fallback_fires_when_header_present_but_body_empty(mock_llm):
    def router(p, *, request_type, use_cache):
        if request_type == "resume_parse_fallback":
            return json.dumps({
                "education": [{"institution": "INSEA", "degree": "Master", "field": "AI",
                               "start_date": "2022", "end_date": "2024", "gpa": None, "location": None}],
                "experience": [{"company": "Acme", "position": "Dev", "period": "2022 - Present",
                                "location": None, "description": None,
                                "achievements": [], "technologies": []}],
                "projects": [], "certifications": [], "skills": [],
            })
        return json.dumps({"experience": [], "education": [], "projects": [], "skills": []})

    mock_llm.set(router)
    # EXPERIENCE and EDUCATION headers present, but immediately followed by the next header
    text = "JANE\njane@x.com\n\nEXPERIENCE\nEDUCATION\nSKILLS\nPython\n"
    result = ResumeParserPipeline().parse_text(text, language="en")

    assert result.gemini_fallback_used is True
    assert result.cv_profile.experience and result.cv_profile.experience[0].company == "Acme"
    assert result.cv_profile.education and result.cv_profile.education[0].institution == "INSEA"


def test_response_contract_shape(mock_llm, resume_text_en):
    d = ResumeParserPipeline().parse_text(resume_text_en, language="en").to_dict()
    for key in ("cv_profile", "language", "detected_language", "confidence",
                "validation_issues", "duplicates_removed", "sections_detected"):
        assert key in d
    for key in ("contact_confidence", "experience_confidence", "education_confidence",
                "projects_confidence", "skills_confidence"):
        assert key in d["confidence"]
