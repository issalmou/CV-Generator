"""
Spec §20 — mandatory extraction-accuracy tests.

Covers the education start/end-date rules, the experience `period` rule,
the "never borrow a date from another section" rule, exhaustive /
line-cut-repaired technologies, and the professional-summary cleanup.

The LLM is mocked: each test feeds the pipeline the exact JSON a
section parser would return, then asserts on the post-processed
ExtractedCVProfile.
"""

from __future__ import annotations

import json

import pytest

from services.cv.resume_parser_pipeline import ResumeParserPipeline

# Pure-helper date tests live in test_date_parser.py; this file exercises
# the same rules end-to-end through the pipeline.


# ---------------------------------------------------------------------------
# End-to-end through the pipeline
# ---------------------------------------------------------------------------

def _run(mock_llm, resume_text, *, education=None, experience=None, projects=None, skills=None):
    def router(prompt, *, request_type, use_cache):
        if request_type == "resume_parse_education":
            return json.dumps({"education": education or []})
        if request_type == "resume_parse_experience":
            return json.dumps({"experience": experience or []})
        if request_type == "resume_parse_projects":
            return json.dumps({"projects": projects or []})
        if request_type == "resume_parse_skills":
            return json.dumps({"skills": skills or []})
        return "{}"

    mock_llm.set(router)
    return ResumeParserPipeline().parse_text(resume_text, language="en")


def test_education_range_split(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nEDUCATION\nMaster, INSEA\n2022 - 2024\n"
    result = _run(mock_llm, text, education=[
        {"institution": "INSEA", "degree": "Master", "field": None,
         "start_date": "2022", "end_date": "2024", "gpa": None, "location": None},
    ])
    edu = result.cv_profile.education[0]
    assert edu.start_date == "2022"
    assert edu.end_date == "2024"


def test_education_open_ended_kept_verbatim(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nEDUCATION\nPhD, MIT\n2022 - Présent\n"
    result = _run(mock_llm, text, education=[
        {"institution": "MIT", "degree": "PhD", "field": None,
         "start_date": "2022", "end_date": "Présent", "gpa": None, "location": None},
    ])
    edu = result.cv_profile.education[0]
    assert edu.start_date == "2022"
    assert edu.end_date == "Présent"          # NOT converted to a year (§8)


def test_education_without_dates_stays_null(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nEDUCATION\nMaster en Informatique - INSEA\n"
    result = _run(mock_llm, text, education=[
        {"institution": "INSEA", "degree": "Master", "field": "Informatique",
         "start_date": None, "end_date": None, "gpa": None, "location": None},
    ])
    edu = result.cv_profile.education[0]
    assert edu.start_date is None
    assert edu.end_date is None


def test_education_does_not_borrow_experience_date(mock_llm):
    """§20.9 — Education '2022 - 2024', Experience '2024 - 2025': education must stay 2022-2024."""
    text = (
        "JOHN DOE\njohn@x.com\n\n"
        "EXPERIENCE\nDev, Acme\n2024 - 2025\n\n"
        "EDUCATION\nMaster, INSEA\n2022 - 2024\n"
    )
    result = _run(
        mock_llm, text,
        education=[{"institution": "INSEA", "degree": "Master", "field": None,
                    "start_date": "2022", "end_date": "2024", "gpa": None, "location": None}],
        experience=[{"company": "Acme", "position": "Dev", "period": "2024 - 2025",
                     "location": None, "description": None, "achievements": [], "technologies": []}],
    )
    edu = result.cv_profile.education[0]
    exp = result.cv_profile.experience[0]
    assert (edu.start_date, edu.end_date) == ("2022", "2024")
    assert exp.period == "1 year"   # computed from 2024 - 2025


def test_experience_period_computed_from_two_dates(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nEXPERIENCE\nEngineer, Beta\nJan 2024 - Jun 2024\n"
    result = _run(mock_llm, text, experience=[
        {"company": "Beta", "position": "Engineer", "period": "Jan 2024 - Jun 2024",
         "location": None, "description": None, "achievements": [], "technologies": []},
    ])
    # 6 months span (Jan..Jun inclusive), pipeline language is "en"
    assert result.cv_profile.experience[0].period == "6 months"


def test_experience_ongoing_runs_to_today(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nEXPERIENCE\nEngineer, Beta\nSince January 2024\n"
    result = _run(mock_llm, text, experience=[
        {"company": "Beta", "position": "Engineer", "period": "Since January 2024",
         "location": None, "description": None, "achievements": [], "technologies": []},
    ])
    period = result.cv_profile.experience[0].period
    assert period is not None and ("month" in period or "year" in period)


def test_technologies_stay_individual(mock_llm):
    text = (
        "JOHN DOE\njohn@x.com\n\nEXPERIENCE\nDev, Acme\n2023\n"
        "Built the platform with Python, JavaScript, React, Node.js and MongoDB.\n"
    )
    result = _run(mock_llm, text, experience=[
        {"company": "Acme", "position": "Dev", "period": "2023",
         "location": None, "description": None, "achievements": [],
         # model mistakenly returned one grouped string — pipeline must split it
         "technologies": ["Python, JavaScript, React, Node.js, MongoDB"]},
    ])
    techs = result.cv_profile.experience[0].technologies
    assert techs == ["Python", "JavaScript", "React", "Node.js", "MongoDB"]


def test_summary_has_no_contact_leakage(mock_llm):
    """§20.8 — the two-column 'Profile' block often carries phone/email/address."""
    text = (
        "PROFILE\n"
        "Passionate full-stack developer building AI-driven products.\n"
        "0640065118\n"
        "issalmouadaaiche@gmail.com\n"
        "Ville 25 mars bloc Y NR 502\n"
        "\nJOHN DOE\njohn@x.com\n"
    )
    result = _run(mock_llm, text)
    summary = result.cv_profile.professional_summary or ""
    assert "0640065118" not in summary
    assert "@gmail.com" not in summary
    assert "Passionate full-stack developer" in summary


def test_summary_null_when_no_real_prose(mock_llm):
    text = "PROFILE\n0640065118\njohn@x.com\n\nJOHN DOE\njohn@x.com\n"
    result = _run(mock_llm, text)
    assert result.cv_profile.professional_summary is None
