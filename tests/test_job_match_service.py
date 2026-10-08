"""Phase 46/60 — the deterministic job<->candidate matcher (`job_match_service`)."""

from __future__ import annotations

import pytest

from _jobs_helpers import make_offer
from schemas.jobs import JobSearchContext
from services.jobs.match_service import evaluate


def _offer(**kw):
    base = dict(title="Senior Python Backend Engineer", company="Globex", city="Paris",
                description="Python, PostgreSQL, AWS. Fintech.", language="en")
    base.update(kw)
    return make_offer("ashby", "1", **base)


def test_score_is_bounded_and_deterministic():
    ctx = JobSearchContext(query="python engineer", skills=["Python"])
    off = _offer()
    r1 = evaluate(off, ctx)
    r2 = evaluate(off, ctx)
    assert 0.0 <= r1.score <= 1.0 and r1.score == r2.score


def test_reasons_and_missing_skills():
    ctx = JobSearchContext(query="Backend Engineer",
                           skills=["Python", "PostgreSQL", "Kubernetes", "Terraform"],
                           remote_type="remote", experience_level="senior",
                           salary_min=60000, sectors=["fintech"], language="en")
    off = _offer(remote_type="remote", experience_level="senior", salary_min=70000)
    r = evaluate(off, ctx)
    assert any("title matches" in x for x in r.reasons)
    assert any("skills present" in x for x in r.reasons)
    assert any("salary meets" in x for x in r.reasons)
    assert any("fintech" in x for x in r.reasons)
    assert set(r.missing_skills) == {"Kubernetes", "Terraform"}
    assert "Python" not in r.missing_skills


def test_salary_below_minimum_is_flagged():
    ctx = JobSearchContext(query="engineer", salary_min=90000)
    r = evaluate(_offer(salary_min=50000), ctx)
    assert any("below your minimum" in x for x in r.reasons)


def test_never_returns_an_empty_reason_list():
    r = evaluate(_offer(title="X", description=None), JobSearchContext())
    assert r.reasons


def test_missing_skills_capped():
    ctx = JobSearchContext(skills=[f"skill{i}" for i in range(30)])
    r = evaluate(_offer(description="nothing relevant"), ctx)
    assert len(r.missing_skills) <= 12


def test_bad_offer_never_crashes():
    class _Broken:
        title = "T"
        remote_type = None
        experience_level = None
        location = None
        company = None
        language = None
    r = evaluate(_Broken(), JobSearchContext(query="t"))
    assert isinstance(r.score, float) and r.reasons
