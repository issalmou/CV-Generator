"""
Unit tests: the three section parsers + parser_common + the ResumeStructurer
facade. The LLM is always mocked.
"""

from __future__ import annotations

import json

import pytest

from services.cv.education_parser import EducationParser
from services.cv.experience_parser import ExperienceParser
from services.parser_common import parse_list_response, strip_code_fences
from services.cv.project_parser import ProjectParser
from services.cv.resume_structurer import ResumeStructurer


# ---------------------------------------------------------------------------
# parser_common
# ---------------------------------------------------------------------------

def test_strip_code_fences():
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'


def test_parse_list_response_accepts_wrapped_and_bare(caplog):
    import logging

    log = logging.getLogger("t")
    assert parse_list_response('{"experience": [{"company": "X"}]}', expected_key="experience", logger=log) == [{"company": "X"}]
    assert parse_list_response('[{"company": "Y"}]', expected_key="experience", logger=log) == [{"company": "Y"}]


def test_parse_list_response_returns_empty_on_garbage(caplog):
    import logging

    log = logging.getLogger("t")
    assert parse_list_response("not json at all", expected_key="experience", logger=log) == []
    assert parse_list_response('{"experience": "oops"}', expected_key="experience", logger=log) == []


# ---------------------------------------------------------------------------
# Individual parsers
# ---------------------------------------------------------------------------

def test_experience_parser_happy_path(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps({"experience": [{
        "company": "Acme", "position": "Dev", "period": "2021 - 2023",
        "location": None, "description": None,
        "achievements": [], "technologies": ["Go"],
    }]}))
    out = ExperienceParser().parse("Dev at Acme 2021-2023", language="en")
    assert out[0]["company"] == "Acme"
    assert out[0]["period"] == "2021 - 2023"
    assert mock_llm.calls == ["resume_parse_experience"]


def test_parsers_skip_llm_on_empty_section(mock_llm):
    assert ExperienceParser().parse("   ") == []
    assert EducationParser().parse("") == []
    assert ProjectParser().parse("\n\n") == []
    assert mock_llm.calls == []


def test_parser_returns_empty_on_malformed_json(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: "sorry, I cannot do that")
    assert EducationParser().parse("Master, INSEA, 2024") == []


def test_language_directive_is_injected(mock_llm):
    ProjectParser().parse("Some project", language="fr")
    _, prompt = mock_llm.prompts[-1]
    assert "French" in prompt


# ---------------------------------------------------------------------------
# ResumeStructurer facade
# ---------------------------------------------------------------------------

def test_structurer_delegates_to_the_section_parsers(mock_llm):
    s = ResumeStructurer()
    assert type(s._experience_parser).__name__ == "ExperienceParser"
    assert type(s._education_parser).__name__ == "EducationParser"
    assert type(s._project_parser).__name__ == "ProjectParser"
    assert type(s._skills_parser).__name__ == "SkillsParser"

    s.parse_experience("Dev at Acme", language="en")
    s.parse_education("Master INSEA", language="en")
    s.parse_projects("Portfolio", language="en")
    s.parse_skills("Python, SQL", language="en")
    assert mock_llm.calls == [
        "resume_parse_experience", "resume_parse_education",
        "resume_parse_projects", "resume_parse_skills",
    ]


def test_fallback_parses_multi_key_response(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps({
        "education": [{"institution": "INSEA"}],
        "experience": [{"company": "Acme"}],
        "projects": [], "certifications": [], "skills": ["Python"],
    }))
    result = ResumeStructurer().parse_fallback("full resume text", language="en")
    # Structured validation normalises every entry to its full shape
    # (missing fields -> None), never fabricating a value.
    assert len(result.education) == 1 and result.education[0]["institution"] == "INSEA"
    assert result.education[0]["degree"] is None
    assert len(result.experience) == 1 and result.experience[0]["company"] == "Acme"
    assert result.experience[0]["position"] is None
    assert result.skills == ["Python"]


def test_fallback_empty_on_bad_json(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: "not json")
    result = ResumeStructurer().parse_fallback("text", language="en")
    assert result.education == [] and result.experience == [] and result.skills == []
