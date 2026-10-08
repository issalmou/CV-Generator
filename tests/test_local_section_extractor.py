"""Unit tests: services.cv.local_section_extractor (skills / certifications / languages; no LLM)."""

from __future__ import annotations

import pytest

from services.cv.local_section_extractor import LocalSectionExtractor


@pytest.fixture
def extractor() -> LocalSectionExtractor:
    return LocalSectionExtractor()


def test_skills_flat_list_labels_dropped(extractor):
    text = "Languages: Python, SQL\nDatabases: PostgreSQL, MongoDB\n"
    result = extractor.extract_skills(text)
    assert result == ["Python", "SQL", "PostgreSQL", "MongoDB"]      # flat, no categories


def test_skills_flat_list_no_labels(extractor):
    result = extractor.extract_skills("Python, Docker, Kubernetes, Git")
    assert result == ["Python", "Docker", "Kubernetes", "Git"]


def test_skills_split_on_et_and_and(extractor):
    assert extractor.extract_skills("PHP et Python") == ["PHP", "Python"]
    assert extractor.extract_skills("Oracle and MongoDB") == ["Oracle", "MongoDB"]


def test_skills_exact_duplicates_removed(extractor):
    assert extractor.extract_skills("Python\nPython\nSQL") == ["Python", "SQL"]


def test_skills_empty(extractor):
    assert extractor.extract_skills("") == []


def test_certifications_name_issuer_year(extractor):
    text = "AWS Certified Solutions Architect, Amazon, 2023\nScrum Master - Scrum.org, 2021\n"
    result = extractor.extract_certifications(text)
    assert result[0]["name"] == "AWS Certified Solutions Architect"
    assert result[0]["issuer"] == "Amazon"
    assert result[0]["year"] == 2023
    assert result[1]["year"] == 2021


def test_certification_without_year_is_none_not_guessed(extractor):
    result = extractor.extract_certifications("Deep Learning Specialization, Coursera\n")
    assert result[0]["year"] is None


def test_certification_name_kept_whole_not_truncated(extractor):
    # fidelity: a " - <suffix>" and a "(...)" belong to the cert NAME
    result = extractor.extract_certifications(
        "AWS Certified Machine Learning - Specialty (2023)\n"
        "Certified Kubernetes Administrator (CKA), 2022\n"
    )
    assert result[0]["name"] == "AWS Certified Machine Learning - Specialty"
    assert result[0]["year"] == 2023
    assert result[1]["name"] == "Certified Kubernetes Administrator (CKA)"
    assert result[1]["year"] == 2022


def test_languages_with_levels_kept_verbatim(extractor):
    # Extraction must NOT translate/normalise levels (spec §10).
    result = extractor.extract_languages("English (Fluent), French (Native), Spanish (Basic)")
    by_lang = {r["language"]: r["level"] for r in result}
    assert by_lang == {"English": "Fluent", "French": "Native", "Spanish": "Basic"}


def test_language_without_level_is_none(extractor):
    result = extractor.extract_languages("Arabic\n")
    assert result[0]["language"] == "Arabic"
    assert result[0]["level"] is None


def test_french_levels_kept_verbatim(extractor):
    result = extractor.extract_languages("Français (Courant)\nAnglais (Intermédiaire)")
    by_lang = {r["language"]: r["level"] for r in result}
    assert by_lang == {"Français": "Courant", "Anglais": "Intermédiaire"}


def test_langue_maternelle_phrase_kept_verbatim(extractor):
    result = extractor.extract_languages(
        "Arabe — Langue maternelle\nFrançais — Courant\nAnglais — Intermédiaire"
    )
    by_lang = {r["language"]: r["level"] for r in result}
    assert by_lang == {
        "Arabe": "Langue maternelle",
        "Français": "Courant",
        "Anglais": "Intermédiaire",
    }


def test_language_no_separator_uses_vocabulary(extractor):
    result = extractor.extract_languages("Anglais Courant\nArabe Langue maternelle")
    by_lang = {r["language"]: r["level"] for r in result}
    assert by_lang == {"Anglais": "Courant", "Arabe": "Langue maternelle"}
