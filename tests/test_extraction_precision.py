"""
Spec §17 — mandatory extraction-precision tests.

TEST 1-2  : education degree / field are split, never lumped together
TEST 3-4  : an experience stated only as a duration keeps the duration
TEST 5    : a calendar period is kept verbatim
TEST 6    : an open-ended education entry resolves end_date to the current year
TEST 7    : professional_summary never carries contact / address / links
TEST 8    : technologies are individual strings
TEST 9    : a PDF-split technology name is rejoined
TEST 10   : a technology the LLM invented (not in the source text) is removed
TEST 11   : a date from EXPERIENCE is never used for EDUCATION
TEST 12   : a location that is only part of the company name is removed
"""

from __future__ import annotations

import json

import pytest

from services.cv.date_parser import compute_experience_period
from services.cv.resume_parser_pipeline import ResumeParserPipeline
from services.cv.source_grounding import split_degree_field, strip_company_derived_location


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
    return ResumeParserPipeline().parse_text(resume_text, language="fr")


# --- TEST 1 / 2 : degree vs field -------------------------------------------

@pytest.mark.parametrize(
    "raw_degree, degree, field",
    [
        ("Master Systèmes d'Information et Systèmes Intelligents (M2SI)",
         "Master", "Systèmes d'Information et Systèmes Intelligents (M2SI)"),
        ("Licence d'Excellence en Intelligence Artificielle back-end",
         "Licence d'Excellence", "Intelligence Artificielle back-end"),
        ("Technicien spécialisé en Développement Digital, option Web Full Stack",
         "Technicien spécialisé", "Développement Digital, option Web Full Stack"),
        ("Baccalauréat option Sciences Physiques et chimiques",
         "Baccalauréat", "Sciences Physiques et chimiques"),
        # degree copied VERBATIM — abbreviation / language / parenthetical kept
        ("M.Sc. Information Systems & Intelligent Systems (M2SI)",
         "M.Sc.", "Information Systems & Intelligent Systems (M2SI)"),
        ("B.Sc. (Excellence) in Artificial Intelligence",
         "B.Sc. (Excellence)", "Artificial Intelligence"),
        ("Specialized Technician Diploma – Web Full Stack Development",
         "Specialized Technician Diploma", "Web Full Stack Development"),
    ],
)
def test_degree_field_split(raw_degree, degree, field):
    entries = [{"degree": raw_degree, "field": None}]
    split_degree_field(entries)
    assert entries[0]["degree"] == degree
    assert entries[0]["field"] == field


def test_degree_without_field_stays_null():
    entries = [{"degree": "Master", "field": None}]
    split_degree_field(entries)
    assert entries[0]["degree"] == "Master"
    assert entries[0]["field"] is None


@pytest.mark.parametrize(
    "field_in, field_out",
    [("en Informatique", "Informatique"), ("in Machine Learning", "Machine Learning"),
     ("Data Science", "Data Science"), ("of Physics", "Physics")],
)
def test_split_degree_field_strips_leading_connector_on_field(field_in, field_out):
    entries = [{"degree": "Master", "field": field_in}]
    split_degree_field(entries)
    assert entries[0]["field"] == field_out


@pytest.mark.parametrize(
    "degree, field, source, expected",
    [
        ("Licence", "Intelligence Artificielle",
         "Licence d’Excellence en Intelligence Artificielle", "Licence d’Excellence"),
        ("Bachelor", "Mathematics",
         "Bachelor of Science in Mathematics, 2018", "Bachelor of Science"),
        ("B.Sc.", "Artificial Intelligence",
         "B.Sc. (Excellence) in Artificial Intelligence", "B.Sc. (Excellence)"),
        # nothing to expand -> unchanged
        ("Master", "Data Science", "Master Data Science, 2022", "Master"),
    ],
)
def test_ground_education_degree_reexpands_truncated_qualifier(degree, field, source, expected):
    from services.cv.source_grounding import ground_education_degree
    entries = [{"degree": degree, "field": field}]
    ground_education_degree(entries, source)
    assert entries[0]["degree"] == expected


# --- TEST 3 / 4 / 5 : experience period -------------------------------------

@pytest.mark.parametrize(
    "raw, language, expected",
    [
        ("Stage de trois mois", "fr", "3 mois"),          # §17.3 — duration kept
        ("Stage de deux mois", "fr", "2 mois"),           # §17.4
        ("Internship for 6 months", "en", "6 months"),
        ("Jan 2024 - Jun 2024", "en", "6 months"),        # §17.5 — computed from 2 dates
        ("2022 - 2024", "fr", "2 ans"),
    ],
)
def test_experience_period_rule(raw, language, expected):
    assert compute_experience_period(raw, language) == expected


def test_experience_keeps_duration_end_to_end(mock_llm):
    text = (
        "JEAN DUPONT\njean@x.com\n\nEXPERIENCE PROFESSIONNELLE\n"
        "Stagiaire, Polyclinique\nStage de trois mois\n"
        "Développement d'une application de gestion.\n"
    )
    result = _run(mock_llm, text, experience=[
        {"company": "Polyclinique", "position": "Stagiaire", "period": "Stage de trois mois",
         "location": None, "description": "Développement d'une application", "achievements": [], "technologies": []},
    ])
    assert result.cv_profile.experience[0].period == "3 mois"
    # ... and NO "period could not be identified" warning
    assert not any(
        i["field"] == "experience[0].period" for i in result.validation_issues
    )


# --- TEST 6 : open-ended education (kept verbatim, §8) -------------------

def test_education_present_kept_verbatim(mock_llm):
    text = "JEAN\njean@x.com\n\nFORMATION\nMaster IA, INSEA\n2022 - Présent\n"
    result = _run(mock_llm, text, education=[
        {"institution": "INSEA", "degree": "Master", "field": "IA",
         "start_date": "2022", "end_date": "Présent", "gpa": None, "location": None},
    ])
    edu = result.cv_profile.education[0]
    assert edu.start_date == "2022"
    assert edu.end_date == "Présent"


# --- TEST 7 : summary purity ---------------------------------------------

def test_summary_excludes_contact_and_address(mock_llm):
    text = (
        "PROFIL\n"
        "Passionné par le développement web et l'intelligence artificielle, "
        "je crée des solutions innovantes alliant front-end et technologies IA.\n"
        "0640065118\n"
        "issalmou@gmail.com\n"
        "Ville 25 mars bloc Y NR 502\n"
        "laayoune\n"
        "LinkedIn\n"
        "\nJEAN DUPONT\njean@x.com\n"
    )
    result = _run(mock_llm, text)
    s = result.cv_profile.professional_summary or ""
    assert "Passionné par le développement web" in s
    for forbidden in ("0640065118", "gmail.com", "Ville 25 mars", "bloc Y", "LinkedIn", "laayoune"):
        assert forbidden not in s


# --- TEST 8 / 9 / 10 : technologies -------------------------------------

def test_technologies_individual_and_grounded(mock_llm):
    text = (
        "JEAN\njean@x.com\n\nEXPERIENCE\nDev, Acme\n2023\n"
        "Application réalisée avec Python, JavaScript, React et Node.js.\n"
    )
    result = _run(mock_llm, text, experience=[
        {"company": "Acme", "position": "Dev", "period": "2023",
         "location": None, "description": None, "achievements": [],
         "technologies": ["Python, JavaScript, React, Node.js"]},
    ])
    assert result.cv_profile.experience[0].technologies == ["Python", "JavaScript", "React", "Node.js"]


def test_line_cut_skill_rejoined_and_kept(mock_llm):
    text = "JEAN\njean@x.com\n\nCOMPETENCES\nPython, scikit-\nlearn, Pandas\n"
    result = _run(mock_llm, text, skills=["Python", "scikit-learn", "Pandas"])
    # flat list[str]; scikit-learn survives grounding thanks to normalised matching
    assert result.cv_profile.skills == ["Python", "scikit-learn", "Pandas"]
    assert all(isinstance(s, str) for s in result.cv_profile.skills)


def test_invented_technology_is_removed(mock_llm):
    """§17.10 — source mentions only Python; Django/PostgreSQL invented by the model are dropped."""
    text = "JEAN\njean@x.com\n\nEXPERIENCE\nDev, Acme\n2023\nDéveloppement d'une application avec Python.\n"
    result = _run(mock_llm, text, experience=[
        {"company": "Acme", "position": "Dev", "period": "2023",
         "location": None, "description": None, "achievements": [],
         "technologies": ["Python", "Django", "PostgreSQL"]},
    ])
    assert result.cv_profile.experience[0].technologies == ["Python"]


# --- GPA / mention kept verbatim as text (not coerced to a number) ----

def test_gpa_kept_verbatim_as_text(mock_llm):
    text = (
        "JEAN\njean@x.com\n\nFORMATION\n"
        "MSc Data Science, UCL\n2018 - 2019\nGPA: 3.9/4.0 - Distinction\n"
    )
    result = _run(mock_llm, text, education=[
        {"institution": "UCL", "degree": "MSc", "field": "Data Science",
         "start_date": "2018", "end_date": "2019",
         "gpa": "3.9/4.0 - Distinction", "location": None},
    ])
    assert result.cv_profile.education[0].gpa == "3.9/4.0 - Distinction"


# --- TEST 11 : no cross-section date -----------------------------------

def test_experience_date_never_used_for_education(mock_llm):
    text = (
        "JEAN\njean@x.com\n\n"
        "EXPERIENCE\nDev, Acme\n2024 - 2025\n\n"
        "FORMATION\nMaster, INSEA\n2020 - 2022\n"
    )
    # the model wrongly copies the experience years onto the education entry
    result = _run(
        mock_llm, text,
        education=[{"institution": "INSEA", "degree": "Master", "field": None,
                    "start_date": "2024", "end_date": "2025", "gpa": None, "location": None}],
        experience=[{"company": "Acme", "position": "Dev", "period": "2024 - 2025",
                     "location": None, "description": None, "achievements": [], "technologies": []}],
    )
    edu = result.cv_profile.education[0]
    # 2024 / 2025 are not in the FORMATION text -> grounded out
    assert edu.start_date is None and edu.end_date is None


# --- TEST 12 : inferred location -------------------------------------

def test_location_from_company_name_is_removed():
    entries = [{"company": "Polyclinique Internationale de Laâyoune", "location": "Laâyoune"}]
    strip_company_derived_location(entries)
    assert entries[0]["location"] is None


def test_real_location_is_kept():
    entries = [{"company": "Acme Corp", "location": "Rabat"}]
    strip_company_derived_location(entries)
    assert entries[0]["location"] == "Rabat"


# --- interests / personal_qualities are list[str] (§12 / §13) --------

def test_interests_is_a_list(mock_llm):
    text = (
        "JEAN\njean@x.com\n\nPROFIL\nDéveloppeur full-stack passionné par le web moderne.\n\n"
        "CENTRES D'INTÉRÊT\nSport\nMusique\nVoyage\nExploration\n"
    )
    result = _run(mock_llm, text)
    assert result.cv_profile.interests == ["Sport", "Musique", "Voyage", "Exploration"]


def test_personal_qualities_is_a_list_without_contact(mock_llm):
    text = (
        "JEAN DUPONT\njean.dupont@x.com\n\nPROFIL\nIngénieur logiciel spécialisé en données.\n\n"
        "QUALITÉS\n"
        "Esprit d'analyse\n"
        "Gestion du temps et des\npriorités\n"
        "Travail en équipe\n"
        "jean.dupont@x.com\n"          # contact leakage -> must be dropped
        "JEAN DUPONT\n"                 # the name -> must be dropped
    )
    result = _run(mock_llm, text)
    pq = result.cv_profile.personal_qualities
    assert "Esprit d'analyse" in pq
    assert "Gestion du temps et des priorités" in pq   # wrapped line rejoined
    assert "Travail en équipe" in pq
    assert not any("@" in q for q in pq)
    assert "JEAN DUPONT" not in pq and "Jean Dupont" not in pq
