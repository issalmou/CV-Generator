"""
Phase 4 — skill analysis (matched / missing / uncertain) + recommended_actions.

- job_description = None  -> NO skill_analysis (Case A: general CV, no targeting)
- job_description given   -> explicit analysis:
    matched   = job skill present in the CV
    missing   = job skill absent from the info provided SO FAR (NOT "cannot do")
    uncertain = fuzzy match to confirm
- missing skills are NEVER injected into cv_data.skills / summary / experience
- additional_skills stay separate
- normalisation is reasonable ("Python 3" == "python", "Postgres" == "PostgreSQL")
  but never invents an equivalence.
"""

from __future__ import annotations

import json

from cv_models import CVProfile
from services.cv.skill_analysis import analyze


def _profile(**kw) -> CVProfile:
    base = dict(name="X", email="x@x.com", phone="+1",
                experience=[], education=[], projects=[], skills=[],
                languages=[], certifications=[])
    base.update(kw)
    return CVProfile(**base)


def _kw(**buckets) -> dict:
    empty = {"technical_skills": [], "business_skills": [], "tools": [], "frameworks": [],
             "certifications": [], "degrees": [], "soft_skills": []}
    empty.update(buckets)
    return empty


# ---------------------------------------------------------------------------
# core matching
# ---------------------------------------------------------------------------

def test_exact_and_missing():
    p = _profile(skills=[{"category": "Lang", "skills": ["Python", "SQL"]}])
    a = analyze(p, _kw(technical_skills=["Python", "SQL", "Kubernetes", "Terraform"]))
    assert {m.skill for m in a.matched} == {"Python", "SQL"}
    assert set(a.missing) == {"Kubernetes", "Terraform"}
    assert a.uncertain == []


def test_version_suffix_normalisation():
    p = _profile(skills=[{"category": "Lang", "skills": ["Python 3", "Java 17"]}])
    a = analyze(p, _kw(technical_skills=["Python", "Java"]))
    assert {m.skill for m in a.matched} == {"Python", "Java"}
    # candidate wording is preserved verbatim
    assert {m.matched_to for m in a.matched} == {"Python 3", "Java 17"}


def test_safe_alias_is_matched():
    p = _profile(skills=[{"category": "DB", "skills": ["Postgres"]}],
                 experience=[{"company": "A", "position": "Eng", "period": "2020",
                              "achievements": [], "technologies": ["k8s"]}])
    a = analyze(p, _kw(technical_skills=["PostgreSQL", "Kubernetes"]))
    # "Postgres" ≈ "PostgreSQL" and "k8s" ≈ "Kubernetes" are SAFE known
    # equivalences -> matched (never missing), candidate wording kept verbatim.
    assert {m.skill for m in a.matched} == {"PostgreSQL", "Kubernetes"}
    assert {m.matched_to for m in a.matched} == {"Postgres", "k8s"}
    assert a.missing == [] and a.uncertain == []


def test_two_different_spellings_of_the_same_tech_match():
    p = _profile(skills=[{"category": "X", "skills": ["ReactJS"]}])
    a = analyze(p, _kw(frameworks=["React.js"]))
    assert len(a.matched) == 1
    assert a.matched[0].skill == "React.js" and a.matched[0].matched_to == "ReactJS"
    assert a.missing == []


def test_uncertain_when_equivalence_not_safe():
    p = _profile(skills=[{"category": "X", "skills": ["Google Cloud Functions"]}])
    a = analyze(p, _kw(technical_skills=["Google Cloud Storage"]))
    # shares tokens but not the same product -> uncertain, NOT matched, NOT missing
    assert a.matched == []
    assert a.missing == []
    assert len(a.uncertain) == 1 and a.uncertain[0].skill == "Google Cloud Storage"


def test_technologies_from_experience_and_projects_count():
    p = _profile(
        experience=[{"company": "A", "position": "E", "period": "2020",
                     "achievements": [], "technologies": ["FastAPI"]}],
        projects=[{"title": "P", "description": "d", "technologies": ["Docker"]}],
    )
    a = analyze(p, _kw(frameworks=["FastAPI"], tools=["Docker"], technical_skills=["Rust"]))
    assert {m.skill for m in a.matched} == {"FastAPI", "Docker"}
    assert a.missing == ["Rust"]


def test_degrees_and_soft_skills_are_not_hard_matched():
    p = _profile(skills=[{"category": "X", "skills": ["Python"]}])
    a = analyze(p, _kw(technical_skills=["Python"], degrees=["BSc Computer Science"],
                       soft_skills=["Communication"]))
    assert [m.skill for m in a.matched] == ["Python"]
    assert a.missing == []   # degrees / soft skills excluded from missing


# recommended_actions moved to services/recommendations.py in Phase 6 —
# see tests/test_recommendations.py.


# ---------------------------------------------------------------------------
# pipeline integration — Case A vs Case B
# ---------------------------------------------------------------------------

def test_case_A_no_job_description_no_analysis(client, cv_profile_dict):
    payload = dict(cv_profile_dict)
    payload.pop("job_description", None)
    r = client.post("/api/generate-cv", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["skill_analysis"] is None
    assert body["recommended_actions"] == []


def test_case_B_job_description_returns_analysis(client, cv_profile_dict, db):
    # fixture JD: "We need a Python + Kubernetes backend engineer."
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    body = r.json()
    sa = body["skill_analysis"]
    assert sa is not None
    matched = {m["skill"].lower() for m in sa["matched"]}
    assert "python" in matched                        # candidate has Python
    assert any(s.lower() == "kubernetes" for s in sa["missing"])   # candidate does not

    # missing skill is NOT in the generated CV body
    from models import GeneratedCV
    src = json.loads(db.query(GeneratedCV).filter_by(id=body["generated_cv_id"]).one().structured_source)
    flat_skills = [s.lower() for cat in src["skills"] for s in cat["skills"]]
    assert "kubernetes" not in flat_skills
    body_text = (src["summary"]["professional_summary"] + " "
                 + " ".join(b for e in src["experience"] for b in e["bullets"])).lower()
    assert "kubernetes" not in body_text
    # it appears only as a suggestion + a recommended action
    assert any(a["type"] == "confirm_skill" and a["skill"].lower() == "kubernetes"
               for a in body["recommended_actions"])


def test_case_B_missing_skill_never_injected_even_across_regen(client, cv_profile_dict, db):
    from models import GeneratedCV
    ref = client.post("/api/generate-cv", json=cv_profile_dict).json()["reference"]
    for _ in range(2):
        client.post("/api/generate-cv", json={**cv_profile_dict, "reference": ref})
    rows = db.query(GeneratedCV).filter_by(reference=ref).all()
    for row in rows:
        src = json.loads(row.structured_source)
        flat = [s.lower() for cat in src["skills"] for s in cat["skills"]]
        assert "kubernetes" not in flat
