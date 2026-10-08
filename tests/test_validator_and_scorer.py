"""Unit tests: services.cv.resume_validator + services.cv.confidence_scorer (both local, no LLM)."""

from __future__ import annotations

import pytest

from services.cv.confidence_scorer import ConfidenceScorer
from services.cv.resume_validator import ResumeValidator, ValidationReport


@pytest.fixture
def validator() -> ResumeValidator:
    return ResumeValidator()


@pytest.fixture
def scorer() -> ConfidenceScorer:
    return ConfidenceScorer()


def test_contact_validation_flags_missing_name_and_email(validator):
    report = ValidationReport()
    validator.validate_contact({"name": None, "email": None}, report)
    fields = {(i.field, i.severity) for i in report.issues}
    assert ("name", "error") in fields
    assert ("email", "error") in fields


def test_contact_validation_flags_bad_email_format(validator):
    report = ValidationReport()
    validator.validate_contact({"name": "X", "email": "not-an-email"}, report)
    assert any(i.field == "email" and i.severity == "warning" for i in report.issues)


def test_experience_deduplication(validator):
    report = ValidationReport()
    entries = [
        {"company": "Acme", "position": "Dev", "period": "2021 - 2022"},
        {"company": "acme", "position": "dev", "period": "2021 - 2022"},  # dup (case-insensitive)
        {"company": "Beta", "position": "Lead", "period": "2022 - 2023"},
    ]
    out = validator.validate_experience(entries, report)
    assert len(out) == 2
    assert report.duplicates_removed.get("experience") == 1


def test_experience_missing_period_warns(validator):
    report = ValidationReport()
    validator.validate_experience([{"company": "Acme", "position": "Dev"}], report)
    assert any(i.field == "experience[0].period" and i.severity == "warning" for i in report.issues)


def test_grouped_technologies_are_split_by_validator(validator):
    report = ValidationReport()
    out = validator.validate_experience(
        [{"company": "A", "position": "Dev", "period": "2023",
          "technologies": ["Python, React, Docker"]}],
        report,
    )
    assert out[0]["technologies"] == ["Python", "React", "Docker"]


def test_education_end_before_start_is_flagged(validator):
    report = ValidationReport()
    validator.validate_education(
        [{"institution": "INSEA", "degree": "Master", "start_date": "2024", "end_date": "2022"}],
        report,
    )
    assert any("before start" in i.message for i in report.issues)


def test_validator_never_raises_on_bad_data(validator):
    report = ValidationReport()
    # missing everything — must not raise, just flag
    validator.validate_education([{}], report)
    validator.validate_projects([{"github": "http://ok.dev"}], report)
    assert report.issues  # issues recorded, no exception


def test_confidence_contact_scoring(scorer):
    report = ValidationReport()
    full = {"name": "X", "email": "x@y.com", "phone": "+212600112233",
            "linkedin": "l", "github": "g", "portfolio": "p"}
    assert scorer.score_contact(full, report) == 100
    assert scorer.score_contact({"name": "X"}, report) < 50


def test_confidence_zero_for_empty_sections(scorer):
    report = ValidationReport()
    assert scorer.score_experience([], report) == 0
    assert scorer.score_education([], report) == 0
    assert scorer.score_projects([], report) == 0
    assert scorer.score_skills([]) == 0


def test_confidence_all_keys_present(scorer):
    report = ValidationReport()
    scores = scorer.compute_all(contact={}, experience=[], education=[], projects=[], skills=[], report=report)
    assert set(scores) == {
        "contact_confidence", "experience_confidence", "education_confidence",
        "projects_confidence", "skills_confidence",
    }
    assert all(0 <= v <= 100 for v in scores.values())
