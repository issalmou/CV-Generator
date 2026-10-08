"""Unit tests: services.cv.section_splitter (rule-based, no LLM)."""

from __future__ import annotations

import pytest

from services.cv.section_splitter import SectionSplitter, detect_language


@pytest.fixture
def splitter() -> SectionSplitter:
    return SectionSplitter()


@pytest.mark.parametrize(
    "header, expected_section",
    [
        ("EDUCATION", "education"),
        ("Formation", "education"),
        ("ACADEMIC BACKGROUND", "education"),
        ("Parcours academique", "education"),
        ("EXPERIENCE", "experience"),
        ("Experiences", "experience"),
        ("WORK EXPERIENCE", "experience"),
        ("PROFESSIONAL EXPERIENCE", "experience"),
        ("Experience Professionnelle", "experience"),
        ("SKILLS", "skills"),
        ("Competences", "skills"),
        ("Competences techniques", "skills"),
        ("PROJECTS", "projects"),
        ("Projets", "projects"),
        ("CERTIFICATIONS", "certifications"),
        ("LANGUAGES", "languages"),
        ("Langues", "languages"),
        ("Centres d'interet", "interests"),
        ("Qualites personnelles", "personal_qualities"),
    ],
)
def test_detects_fr_and_en_headers(splitter, header, expected_section):
    text = f"John Doe\nSome intro\n\n{header}\nbody line one\nbody line two\n"
    sections = splitter.split(text)
    assert "body line one" in sections[expected_section]


def test_content_before_first_header_is_contact(splitter):
    text = "JOHN DOE\njohn@x.com\n\nEXPERIENCE\nDev at Acme\n"
    sections = splitter.split(text)
    assert "JOHN DOE" in sections["contact"]
    assert "john@x.com" in sections["contact"]


def test_interests_do_not_leak_into_languages(splitter):
    text = (
        "Jane\n\nLANGUAGES\nEnglish (Fluent)\n\n"
        "INTERESTS\nHiking\nChess\n"
    )
    sections = splitter.split(text)
    assert "English (Fluent)" in sections["languages"]
    assert "Hiking" in sections["interests"]
    assert "Hiking" not in sections["languages"]


def test_all_canonical_keys_always_present(splitter):
    sections = splitter.split("just some text with no headers at all")
    for key in ("contact", "summary", "education", "experience", "projects",
                "skills", "certifications", "languages", "interests", "personal_qualities"):
        assert key in sections


def test_empty_input_returns_empty_sections(splitter):
    sections = splitter.split("")
    assert all(v == "" for v in sections.values())


def test_long_line_is_not_treated_as_header(splitter):
    text = (
        "John\n\nSUMMARY\n"
        "I have strong skills in project management and delivery across teams\n"
    )
    sections = splitter.split(text)
    # the sentence containing 'skills' and 'management' must stay in summary
    assert "project management" in sections["summary"]
    assert sections["skills"] == ""


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Experience professionnelle chez une entreprise de la region", "fr"),
        ("Professional experience with a focus on the delivery of software", "en"),
        ("", "en"),
        ("émojis 🚀 and nothing else", "en"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_split_detailed_reports_matched_headers(splitter):
    text = "X\n\nEDUCATION\na\n\nSKILLS\nb\n"
    result = splitter.split_detailed(text)
    canonical = {c for _, c in result.detected_headers}
    assert canonical == {"education", "skills"}
