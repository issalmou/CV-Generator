"""
Mandatory skills-extraction tests (spec).

`skills` on the extraction contract is now a FLAT ``list[str]`` — never a
list of ``{category, skills}`` objects. These tests drive the whole
pipeline (LLM mocked) and assert:

- no category is ever returned;
- "PHP et Python" / "Oracle and MongoDB" / "OpenCv et BeautifulSoup" are split;
- a name the PDF split across a line break is rejoined;
- exact duplicates are dropped, order is preserved;
- a skill the model invented (not in the SKILLS text) is grounded out;
- empty section -> [];
- works for FR and EN résumés.
"""

from __future__ import annotations

import json

import pytest

from extraction_models import ExtractedCVProfile
from services.cv.resume_parser_pipeline import ResumeParserPipeline


def _run(mock_llm, resume_text, *, skills, language="fr"):
    def router(prompt, *, request_type, use_cache):
        if request_type == "resume_parse_skills":
            return json.dumps({"skills": skills})
        if request_type.startswith("resume_parse"):
            return json.dumps({request_type.split("_")[-1]: []})
        return "{}"

    mock_llm.set(router)
    return ResumeParserPipeline().parse_text(resume_text, language=language).cv_profile


# --- the contract itself ---------------------------------------------------

def test_extraction_model_skills_is_list_of_str():
    assert ExtractedCVProfile.model_fields["skills"].annotation == list[str]


def test_no_category_object_is_ever_returned(mock_llm):
    text = (
        "JEAN\njean@x.com\n\nCOMPÉTENCES\n"
        "Langages : JavaScript, C, PHP, Python\n"
        "Frameworks : Bootstrap, Laravel, Node.js\n"
        "Bases de données : MySQL, Oracle, MongoDB\n"
    )
    prof = _run(mock_llm, text, skills=[
        "JavaScript", "C", "PHP", "Python", "Bootstrap", "Laravel",
        "Node.js", "MySQL", "Oracle", "MongoDB",
    ])
    assert isinstance(prof.skills, list)
    assert all(isinstance(s, str) for s in prof.skills)
    assert prof.skills == [
        "JavaScript", "C", "PHP", "Python", "Bootstrap", "Laravel",
        "Node.js", "MySQL", "Oracle", "MongoDB",
    ]


# --- the reference CV (spec "EXEMPLE AVEC MON CV DE TEST") -----------------

_REFERENCE_SKILLS_TEXT = (
    "COMPÉTENCES TECHNIQUES\n"
    "Langages de Programmation : JavaScript, C, PHP et Python\n"
    "Frameworks et Bibliothèques : Bootstrap, Laravel, Node.js, Express.js, React JS, "
    "TensorFlow, Keras, Numpy, Pandas, scikit-learn, MatplotLib, Seaborn, OpenCv et BeautifulSoup\n"
    "Gestion de bases de données : MySQL, Oracle et MongoDB\n"
    "Langages complémentaires : HTML5, CSS3, UML\n"
    "Méthodologies : Scrum, Agile\n"
    "Outils : Git, GitHub, GitLab\n"
    "Microsoft : Word, Excel\n"
    "Logiciels de Productivité : PowerPoint, PowerBI\n"
)
_REFERENCE_EXPECTED = [
    "JavaScript", "C", "PHP", "Python", "Bootstrap", "Laravel", "Node.js",
    "Express.js", "React JS", "TensorFlow", "Keras", "Numpy", "Pandas",
    "scikit-learn", "MatplotLib", "Seaborn", "OpenCv", "BeautifulSoup",
    "MySQL", "Oracle", "MongoDB", "HTML5", "CSS3", "UML", "Scrum", "Agile",
    "Git", "GitHub", "GitLab", "Word", "Excel", "PowerPoint", "PowerBI",
]


def test_reference_cv_skills(mock_llm):
    text = f"ISSALMOU ADAAICHE\nissalmou@x.com\n\n{_REFERENCE_SKILLS_TEXT}"
    # the model returned the list with conjunctions still glued in a few items
    prof = _run(mock_llm, text, skills=[
        "JavaScript", "C", "PHP et Python", "Bootstrap", "Laravel", "Node.js",
        "Express.js", "React JS", "TensorFlow", "Keras", "Numpy", "Pandas",
        "scikit-learn", "MatplotLib", "Seaborn", "OpenCv et BeautifulSoup",
        "MySQL", "Oracle et MongoDB", "HTML5", "CSS3", "UML", "Scrum", "Agile",
        "Git", "GitHub", "GitLab", "Word", "Excel", "PowerPoint", "PowerBI",
    ])
    assert prof.skills == _REFERENCE_EXPECTED


# --- conjunctions --------------------------------------------------------

@pytest.mark.parametrize(
    "returned, source, expected",
    [
        (["PHP et Python"], "PHP et Python", ["PHP", "Python"]),
        (["Oracle and MongoDB"], "Oracle and MongoDB", ["Oracle", "MongoDB"]),
        (["OpenCv et BeautifulSoup"], "OpenCv et BeautifulSoup", ["OpenCv", "BeautifulSoup"]),
    ],
)
def test_conjunctions_split(mock_llm, returned, source, expected):
    text = f"X\nx@x.com\n\nSKILLS\n{source}\n"
    prof = _run(mock_llm, text, skills=returned)
    assert prof.skills == expected


# --- line-cut, duplicates, empty, invented -----------------------------

def test_line_cut_rejoined(mock_llm):
    text = "X\nx@x.com\n\nSKILLS\nPython, scikit-\nlearn, Pandas\n"
    prof = _run(mock_llm, text, skills=["Python", "scikit-learn", "Pandas"])
    assert prof.skills == ["Python", "scikit-learn", "Pandas"]


def test_duplicates_dropped_order_kept(mock_llm):
    text = "X\nx@x.com\n\nSKILLS\nPython, JavaScript, Python\n"
    prof = _run(mock_llm, text, skills=["Python", "JavaScript", "Python"])
    assert prof.skills == ["Python", "JavaScript"]


def test_no_skills_section(mock_llm):
    prof = _run(mock_llm, "X\nx@x.com\n\nEDUCATION\nMaster, INSEA\n", skills=[])
    assert prof.skills == []


def test_invented_skill_is_grounded_out(mock_llm):
    """Source only says Python & SQL; Django (invented) must be removed."""
    text = "X\nx@x.com\n\nSKILLS\nPython, SQL\n"
    prof = _run(mock_llm, text, skills=["Python", "SQL", "Django", "Kubernetes"])
    assert prof.skills == ["Python", "SQL"]


def test_english_cv(mock_llm):
    text = "JOHN DOE\njohn@x.com\n\nTECHNICAL SKILLS\nPython, Docker, Kubernetes, AWS, Terraform\n"
    prof = _run(mock_llm, text, skills=["Python", "Docker", "Kubernetes", "AWS", "Terraform"], language="en")
    assert prof.skills == ["Python", "Docker", "Kubernetes", "AWS", "Terraform"]
