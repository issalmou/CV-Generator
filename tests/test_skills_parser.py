"""
Unit tests: services.cv.skills_parser.SkillsParser — flat ``list[str]`` output,
no categories, no invention (LLM mocked, local fallback covered).
"""

from __future__ import annotations

import json

import pytest

from services.cv.skills_parser import SkillsParser


def test_empty_section_skips_llm(mock_llm):
    assert SkillsParser().parse("   ") == []
    assert mock_llm.calls == []


def test_returns_a_flat_list_no_categories(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps(
        {"skills": ["JavaScript", "C", "PHP", "Python", "Bootstrap", "Laravel"]}
    ))
    out = SkillsParser().parse("Langages: JavaScript, C, PHP, Python\nFrameworks: Bootstrap, Laravel", language="fr")
    assert out == ["JavaScript", "C", "PHP", "Python", "Bootstrap", "Laravel"]
    assert all(isinstance(s, str) for s in out)
    assert mock_llm.calls == ["resume_parse_skills"]


def test_flattens_a_category_object_response(mock_llm):
    # model ignored the "no categories" instruction — parser must still flatten
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps({"skills": [
        {"category": "Languages", "skills": ["Python", "SQL"]},
        {"category": "Databases", "skills": ["MySQL", "MongoDB"]},
    ]}))
    out = SkillsParser().parse("Languages: Python, SQL\nDatabases: MySQL, MongoDB")
    assert out == ["Python", "SQL", "MySQL", "MongoDB"]


def test_grouped_string_and_conjunctions_are_split(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps(
        {"skills": ["PHP et Python", "Oracle and MongoDB", "OpenCv et BeautifulSoup"]}
    ))
    out = SkillsParser().parse("PHP et Python\nOracle and MongoDB\nOpenCv et BeautifulSoup")
    assert out == ["PHP", "Python", "Oracle", "MongoDB", "OpenCv", "BeautifulSoup"]


def test_exact_duplicates_removed_order_kept(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps(
        {"skills": ["Python", "Python", "JavaScript", "python"]}
    ))
    out = SkillsParser().parse("Python, Python, JavaScript")
    assert out == ["Python", "JavaScript", "python"]   # case-sensitive: only exact dups dropped


def test_line_cut_repair_requested_in_prompt(mock_llm):
    SkillsParser().parse("scikit-\nlearn\nPandas")
    _, prompt = mock_llm.prompts[-1]
    assert "flat list" in prompt.lower()
    assert "no categor" in prompt.lower()
    assert "scikit-" in prompt


def test_falls_back_to_local_flat_list_on_bad_json(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: "not json at all")
    out = SkillsParser().parse("Python, SQL, Docker")
    assert out == ["Python", "SQL", "Docker"]


def test_never_invents(mock_llm):
    mock_llm.set(lambda p, *, request_type, use_cache: json.dumps({"skills": ["Python"]}))
    out = SkillsParser().parse("Python")
    assert out == ["Python"]
