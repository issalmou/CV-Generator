"""Deterministic, LLM-free ranking."""

from __future__ import annotations

from schemas.jobs import (
    ExperienceLevel, FreshnessStatus, JobSearchContext, JobType, NormalizedOffer,
    RemoteType,
)
from services.jobs.ranking import rank, score


def _offer(**kw):
    d = dict(source="x", source_job_id="1", source_url="https://x/1", title="Role")
    d.update(kw)
    return NormalizedOffer(**d)


def test_exact_query_match_scores_higher():
    ctx = JobSearchContext(query="data scientist")
    strong = _offer(title="Senior Data Scientist", description="data science with python")
    weak = _offer(title="Frontend Developer", description="react and css")
    assert score(strong, ctx) > score(weak, ctx)


def test_skill_overlap_matters():
    ctx = JobSearchContext(query="engineer", skills=["Python", "Kubernetes"])
    a = _offer(title="Platform Engineer", description="python kubernetes docker", skills=["Python", "Kubernetes"])
    b = _offer(title="Platform Engineer", description="java spring", skills=["Java"])
    assert score(a, ctx) > score(b, ctx)


def test_remote_preference_matters():
    ctx = JobSearchContext(query="dev", remote_type=RemoteType.remote)
    remote = _offer(title="Dev", remote_type=RemoteType.remote)
    onsite = _offer(title="Dev", remote_type=RemoteType.onsite)
    assert score(remote, ctx) > score(onsite, ctx)


def test_job_type_mismatch_penalised():
    ctx = JobSearchContext(query="dev", job_type=JobType.internship)
    intern = _offer(title="Dev", job_type=JobType.internship)
    job = _offer(title="Dev", job_type=JobType.job)
    assert score(intern, ctx) > score(job, ctx)


def test_experience_distance():
    ctx = JobSearchContext(query="dev", experience_level=ExperienceLevel.junior)
    near = _offer(title="Dev", experience_level=ExperienceLevel.junior)
    far = _offer(title="Dev", experience_level=ExperienceLevel.lead)
    assert score(near, ctx) > score(far, ctx)


def test_language_match():
    ctx = JobSearchContext(query="dev", language="fr")
    fr = _offer(title="Dev", language="fr")
    en = _offer(title="Dev", language="en")
    assert score(fr, ctx) > score(en, ctx)


def test_freshness_influences_score():
    ctx = JobSearchContext(query="dev")
    o = _offer(title="Dev")
    assert score(o, ctx, freshness=FreshnessStatus.fresh) > score(o, ctx, freshness=FreshnessStatus.expired)


def test_preferred_company_boost_and_excluded_removed():
    ctx = JobSearchContext(query="dev", preferred_companies=["Globex"], excluded_companies=["EvilCorp"])
    liked = _offer(title="Dev", company="Globex Inc")
    plain = _offer(title="Dev", company="Neutral Ltd")
    assert score(liked, ctx) > score(plain, ctx)

    banned = _offer(title="Dev", company="EvilCorp")
    kept = rank([liked, plain, banned], ctx)
    assert all(o.company != "EvilCorp" for o in kept)
    assert len(kept) == 2


def test_score_is_deterministic_and_bounded():
    ctx = JobSearchContext(query="data scientist", skills=["Python"])
    o = _offer(title="Data Scientist", description="python", skills=["Python"])
    s1, s2 = score(o, ctx), score(o, ctx)
    assert s1 == s2
    assert 0.0 <= s1 <= 1.0
