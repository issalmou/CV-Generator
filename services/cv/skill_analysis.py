"""Service: skill analysis (Phase 4).

Compares the skills a job description asks for against the skills the candidate
has actually provided — with *reasonable* normalisation, never invention.

Rules:
* normalise for comparison only: lowercase, trim, drop a trailing version
  ("Python 3" -> "python"), collapse punctuation/space, apply a small hand-kept
  alias map of SAFE equivalences (postgres = postgresql, k8s = kubernetes, …);
* an exact or alias match -> ``matched``;
* a partial / token-overlap match where equivalence is not certain ->
  ``uncertain`` (NOT matched — "better non-confirmed than wrongly present");
* no match -> ``missing`` ("absent from the info provided", NOT "cannot do it");
* the candidate's own skill wording is kept verbatim — never renamed.

The candidate's skill universe = declared skills + technologies used in each
experience + technologies used in each project + certification names.
"""

from __future__ import annotations

import re

from cv_models import CVProfile
from schemas.skill_analysis import SkillAnalysis, SkillMatch

# Hand-kept, deliberately small. Only SAFE, well-known equivalences. Each entry
# maps a normalised token to its canonical normalised token.
_ALIASES: dict[str, str] = {
    "postgres": "postgresql", "psql": "postgresql",
    "k8s": "kubernetes",
    "js": "javascript", "ecmascript": "javascript",
    "ts": "typescript",
    "py": "python",
    "golang": "go",
    "gcp": "google cloud", "google cloud platform": "google cloud",
    "aws cloud": "aws", "amazon web services": "aws",
    "node": "node.js", "nodejs": "node.js",
    "reactjs": "react", "react.js": "react",
    "vuejs": "vue", "vue.js": "vue",
    "postgre": "postgresql",
    "ms sql": "sql server", "mssql": "sql server",
    "gh actions": "github actions",
    "ci cd": "ci/cd", "cicd": "ci/cd",
    "rest api": "rest", "restful": "rest",
    "tf": "terraform",
    "dl": "deep learning", "ml": "machine learning",
    "nlp": "natural language processing",
}

_VERSION_TAIL = re.compile(r"[\s\-_]*v?\d+(?:\.\d+){0,3}\+?$")
_NON_ALNUM = re.compile(r"[^a-z0-9+#./ ]+")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = _VERSION_TAIL.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _ALIASES.get(s, s)


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[ /.\-_]+", _norm(s)) if len(t) >= 3}


def _candidate_universe(profile: CVProfile) -> dict[str, str]:
    """normalised -> the candidate's ORIGINAL wording (kept verbatim)."""
    out: dict[str, str] = {}

    def add(raw: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        n = _norm(raw)
        if n and n not in out:
            out[n] = raw

    for cat in profile.skills:
        for sk in cat.skills:
            add(sk)
    for exp in profile.experience:
        for t in exp.technologies:
            add(t)
    for proj in profile.projects:
        for t in proj.technologies:
            add(t)
    for cert in profile.certifications:
        add(cert.name)
    return out


def _job_skills(keywords: dict[str, list[str]]) -> list[str]:
    """The job's requested skills — the buckets that are genuinely 'skills'.
    Degrees / soft skills are intentionally excluded from the hard match."""
    wanted = ("technical_skills", "frameworks", "tools")
    seen: set[str] = set()
    out: list[str] = []
    for bucket in wanted:
        for kw in keywords.get(bucket, []) or []:
            kw = (kw or "").strip()
            k = kw.lower()
            if kw and k not in seen:
                seen.add(k)
                out.append(kw)
    return out


def analyze(profile: CVProfile, keywords: dict[str, list[str]]) -> SkillAnalysis:
    universe = _candidate_universe(profile)
    universe_norm = set(universe)

    matched: list[SkillMatch] = []
    uncertain: list[SkillMatch] = []
    missing: list[str] = []

    for job_skill in _job_skills(keywords):
        n = _norm(job_skill)
        if not n:
            continue
        # `_norm` already folds safe aliases (postgres->postgresql, k8s->kubernetes,
        # "Python 3"->python …) so an equivalence resolves here as a normalised
        # exact match. `matched_to` keeps the candidate's ORIGINAL wording.
        if n in universe_norm:
            matched.append(SkillMatch(skill=job_skill, matched_to=universe[n], confidence="exact"))
            continue
        # fuzzy: strong token overlap but not a safe equivalence
        jt = _tokens(job_skill)
        best = None
        for cn, craw in universe.items():
            ct = _tokens(craw)
            if jt and ct and (jt <= ct or ct <= jt or len(jt & ct) >= 2):
                best = craw
                break
        if best is not None:
            uncertain.append(SkillMatch(skill=job_skill, matched_to=best, confidence="fuzzy"))
        else:
            missing.append(job_skill)

    return SkillAnalysis(matched=matched, missing=missing, uncertain=uncertain)


# Phase 6 — `recommended_actions` moved to ``services/recommendations.py``
# (contextual, not skill-only). This module now does skill analysis only.
