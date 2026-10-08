"""Automated quality scoring for bake-off outputs.

The highest-signal check is FIDELITY: every atomic fact a model extracts /
uses (company, position, date, institution, degree, skill, technology,
number) must be traceable to the source CV. Anything that is not = a
hallucination flag, which the caller treats as a quality failure regardless
of speed.

Writing tasks (ats_content_optimization, cover_letter, chat) also get a
constraint-compliance check here; their overall writing quality is scored
by a human read of the saved outputs.
"""
from __future__ import annotations

import json
import re
import unicodedata

from benchmarks.bakeoff.tasks import (
    CV_PROFILE, EDUCATION_TEXT, EXPERIENCE_TEXT, JOB_DESCRIPTION,
    PROJECTS_TEXT, SAMPLE_CV_TEXT, SKILLS_TEXT,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


_CV_NORM = _norm(SAMPLE_CV_TEXT)


def _grounded(value: str, *sources: str) -> bool:
    v = _norm(value)
    if len(v) < 2:
        return True
    blob = _norm(" || ".join(sources) if sources else SAMPLE_CV_TEXT)
    if v in blob:
        return True
    # token overlap for multi-word values (rephrased locations etc.)
    toks = [t for t in re.split(r"[^a-z0-9+#.]+", v) if len(t) > 2]
    if toks and sum(t in blob for t in toks) / len(toks) >= 0.8:
        return True
    return False


def _strip(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t).strip()
    return t


def _loads(text: str):
    try:
        return json.loads(_strip(text))
    except Exception:
        # try to salvage the first {...} or [...]
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
        return None


# --- expected ground truth for the sample CV ------------------------------
EXPECTED = {
    "experience_count": 3,
    "experience_companies": {"atlas data", "beta solutions", "startup nova"},
    "education_count": 2,   # INSEA engineering + prepa MP
    "projects_count": 1,
    "skills_min": 12,
    "jd_keywords": ["python", "fastapi", "postgresql", "redis", "docker", "kubernetes",
                    "aws", "terraform", "kafka", "grpc", "ci/cd", "rest", "observabilit"],
}


def score(task: str, text: str) -> dict:
    fn = _SCORERS.get(task)
    if fn is None:
        return {"note": "no scorer"}
    try:
        return fn(text)
    except Exception as e:  # noqa: BLE001
        return {"scorer_error": f"{type(e).__name__}: {e}"}


# --- extraction scorers ---------------------------------------------------

def _score_experience(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, (dict, list)):
        return {"json_valid": False}
    items = data["experience"] if isinstance(data, dict) and "experience" in data else (
        data if isinstance(data, list) else [])
    halluc, checked = [], 0
    companies = set()
    for e in items:
        if not isinstance(e, dict):
            continue
        for field in ("company", "position", "period", "location"):
            val = e.get(field)
            if val:
                checked += 1
                if not _grounded(val, EXPERIENCE_TEXT):
                    halluc.append(f"{field}={val!r}")
        if e.get("company"):
            companies.add(_norm(e["company"]))
        for tech in (e.get("technologies") or []):
            if not _grounded(tech, EXPERIENCE_TEXT):
                halluc.append(f"tech={tech!r}")
    return {
        "json_valid": True,
        "count": len(items),
        "count_ok": len(items) == EXPECTED["experience_count"],
        "companies_ok": companies == EXPECTED["experience_companies"],
        "fields_checked": checked,
        "hallucinations": halluc,
        "hallucination_free": not halluc,
    }


def _score_education(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, (dict, list)):
        return {"json_valid": False}
    items = data["education"] if isinstance(data, dict) and "education" in data else (
        data if isinstance(data, list) else [])
    halluc = []
    for e in items:
        if not isinstance(e, dict):
            continue
        for field in ("institution", "degree", "field", "start_date", "end_date", "gpa", "location"):
            val = e.get(field)
            if val and not _grounded(val, EDUCATION_TEXT, SAMPLE_CV_TEXT):
                halluc.append(f"{field}={val!r}")
    return {"json_valid": True, "count": len(items),
            "count_ok": len(items) == EXPECTED["education_count"],
            "hallucinations": halluc, "hallucination_free": not halluc}


def _score_projects(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, (dict, list)):
        return {"json_valid": False}
    items = data["projects"] if isinstance(data, dict) and "projects" in data else (
        data if isinstance(data, list) else [])
    halluc = []
    for e in items:
        if not isinstance(e, dict):
            continue
        if e.get("title") and not _grounded(e["title"], PROJECTS_TEXT):
            halluc.append(f"title={e['title']!r}")
        for tech in (e.get("technologies") or []):
            if not _grounded(tech, PROJECTS_TEXT):
                halluc.append(f"tech={tech!r}")
    return {"json_valid": True, "count": len(items),
            "count_ok": len(items) == EXPECTED["projects_count"],
            "hallucinations": halluc, "hallucination_free": not halluc}


def _score_skills(text: str) -> dict:
    data = _loads(text)
    skills = data.get("skills") if isinstance(data, dict) else (data if isinstance(data, list) else None)
    if not isinstance(skills, list):
        return {"json_valid": False}
    halluc = [s for s in skills if isinstance(s, str) and not _grounded(s, SKILLS_TEXT)]
    return {"json_valid": True, "count": len(skills),
            "count_ok": len(skills) >= EXPECTED["skills_min"],
            "hallucinations": halluc, "hallucination_free": not halluc}


def _score_profile_analysis(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, dict):
        return {"json_valid": False}
    keys = {"professional_summary", "career_objective", "key_skills", "strengths",
            "expertise_areas", "years_of_experience", "seniority_level"}
    missing = keys - set(data)
    yoe = data.get("years_of_experience")
    summ = data.get("professional_summary", "")
    # invented numeric metric in the summary that is not in the CV?
    nums = re.findall(r"\b\d{2,}\s*%|\b\d[\d.,]*\s*(?:M|k|millions?|mille)\b", summ)
    bad_nums = [n for n in nums if not _grounded(n, SAMPLE_CV_TEXT)]
    ks_ungrounded = [s for s in (data.get("key_skills") or []) if not _grounded(s, SAMPLE_CV_TEXT)]
    return {
        "json_valid": True,
        "keys_complete": not missing,
        "missing_keys": sorted(missing),
        "yoe_int": isinstance(yoe, int),
        "yoe_plausible": isinstance(yoe, int) and 2 <= yoe <= 6,
        "seniority_ok": data.get("seniority_level") in {"Junior", "Mid", "Senior", "Lead", "Executive"},
        "summary_invented_numbers": bad_nums,
        "key_skills_ungrounded": ks_ungrounded,
        "hallucination_free": not bad_nums and not ks_ungrounded,
    }


def _score_keywords(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, dict):
        return {"json_valid": False}
    allkw = _norm(" ".join(str(x) for lst in data.values() if isinstance(lst, list) for x in lst))
    hit = [k for k in EXPECTED["jd_keywords"] if k in allkw]
    return {"json_valid": True,
            "coverage": round(len(hit) / len(EXPECTED["jd_keywords"]), 2),
            "hit": hit,
            "missed": [k for k in EXPECTED["jd_keywords"] if k not in allkw]}


# --- writing scorers (constraint compliance; quality = human read) --------

_PROFILE_SKILLS = {_norm(s) for cat in CV_PROFILE.skills for s in cat.skills}


def _score_ats_optim(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, dict):
        return {"json_valid": False, "note": "expected JSON object"}
    add = data.get("additional_skills") or []
    # additional_skills that the candidate ALREADY has (should be genuinely missing)
    already_has = [s for s in add if _norm(s) in _PROFILE_SKILLS]
    summ = data.get("optimized_summary", "")
    nums = re.findall(r"\b\d{2,}\s*%|\b\d[\d.,]*\s*(?:M|k|millions?)\b", summ)
    invented_nums = [n for n in nums if not _grounded(n, SAMPLE_CV_TEXT)]
    return {
        "json_valid": True,
        "has_optimized_summary": bool(summ),
        "has_optimized_achievements": bool(data.get("optimized_achievements")),
        "additional_skills": add,
        "additional_skills_already_owned": already_has,   # prompt violation
        "summary_invented_numbers": invented_nums,
        "hallucination_free": not invented_nums,
    }


def _score_letter(text: str) -> dict:
    t = text.strip()
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    placeholders = re.findall(r"\[[^\]]{2,30}\]", t)
    mentions_company = "atlas" in _norm(t)
    mentions_role = "backend" in _norm(t)
    # invented metric not in CV
    nums = re.findall(r"\b\d{2,}\s*%|\b\d[\d.,]*\s*(?:M|k|millions?)\b", t)
    invented = [n for n in nums if not _grounded(n, SAMPLE_CV_TEXT)]
    first_person = bool(re.search(r"\bje\b|\bj['e]", _norm(t)))
    return {
        "chars": len(t),
        "paragraphs": len(paras),
        "paragraphs_ok": len(paras) <= 6,
        "placeholders": placeholders,
        "mentions_company": mentions_company,
        "mentions_role": mentions_role,
        "first_person": first_person,
        "invented_numbers": invented,
        "hallucination_free": not invented and not placeholders,
    }


def _score_chat(text: str) -> dict:
    t = text.strip()
    fr = bool(re.search(r"\b(le|la|les|un|une|des|est|pour|avec|votre|vous)\b", _norm(t)))
    nums = re.findall(r"\b\d{2,}\s*%|\b\d[\d.,]*\s*(?:M|k)\b", t)
    invented = [n for n in nums if not _grounded(n, SAMPLE_CV_TEXT)]
    return {"chars": len(t), "in_french": fr, "invented_numbers": invented,
            "hallucination_free": not invented}


def _score_preference(text: str) -> dict:
    data = _loads(text)
    if not isinstance(data, dict):
        return {"json_valid": False}
    return {"json_valid": True,
            "query": data.get("query"), "location": data.get("location"),
            "salary_min": data.get("salary_min"),
            "job_type_not_internship": data.get("job_type") in ("job", None),
            "experience_level": data.get("experience_level")}


_SCORERS = {
    "extract_experience": _score_experience,
    "extract_education": _score_education,
    "extract_projects": _score_projects,
    "extract_skills": _score_skills,
    "profile_analysis": _score_profile_analysis,
    "ats_keyword_extraction": _score_keywords,
    "ats_content_optimization": _score_ats_optim,
    "cover_letter": _score_letter,
    "conversation_agent": _score_chat,
    "job_preference_extraction": _score_preference,
}
