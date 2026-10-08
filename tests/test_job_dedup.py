"""Deduplication — within a provider, across providers, and false-positive guards."""

from __future__ import annotations

import pytest

from schemas.jobs import ExperienceLevel, JobType, NormalizedOffer, RemoteType
from services.jobs.dedup import deduplicate
from services.providers.parsing import normalize_url


def _offer(source, sid, title, company, *, url=None, city=None, remote=None, skills=None,
           desc=None, job_type=None, experience=None):
    return NormalizedOffer(
        source=source, source_job_id=sid,
        source_url=url or f"https://{source}.example/jobs/{sid}",
        title=title, company=company, city=city,
        remote_type=RemoteType(remote) if remote else None,
        job_type=JobType(job_type) if job_type else JobType.job,
        experience_level=ExperienceLevel(experience) if experience else None,
        skills=skills or [], description=desc,
    )


def test_same_source_id_merged():
    a = _offer("linkedin", "1", "Data Scientist", "Globex", city="Paris")
    b = _offer("linkedin", "1", "Data Scientist", "Globex", city="Paris", skills=["Python"])
    out = deduplicate([a, b])
    assert len(out) == 1
    assert out[0].skills == ["Python"]


def test_same_normalized_url_merged_across_sources():
    a = _offer("indeed", "aa", "DS", "Globex", url="https://jobs.globex.com/ds/")
    b = _offer("linkedin", "bb", "DS", "Globex", url="https://jobs.globex.com/ds")
    out = deduplicate([a, b])
    assert len(out) == 1


def test_same_job_two_providers_merged_with_also_seen_on():
    a = _offer("arbeitnow", "x", "Data Scientist", "Globex", city="Paris",
               url="https://arbeitnow.com/view/x", skills=["Python"])
    b = _offer("linkedin", "y", "Data Scientist", "Globex", city="Paris",
               url="https://www.linkedin.com/jobs/view/y", skills=["SQL"], desc="Long description here")
    out = deduplicate([a, b])
    assert len(out) == 1
    merged = out[0]
    # LinkedIn has the higher application_priority -> it becomes the primary
    assert merged.source == "linkedin"
    assert "https://arbeitnow.com/view/x" in merged.also_seen_on
    assert set(merged.skills) == {"Python", "SQL"}
    assert merged.description == "Long description here"


def test_similar_title_different_job_not_merged():
    a = _offer("linkedin", "1", "Data Scientist", "Globex", city="Paris")
    b = _offer("linkedin", "2", "Data Engineer", "Globex", city="Paris")
    out = deduplicate([a, b])
    assert len(out) == 2


def test_same_company_title_different_city_not_merged():
    a = _offer("linkedin", "1", "Data Scientist", "Globex", city="Paris")
    b = _offer("linkedin", "2", "Data Scientist", "Globex", city="Berlin")
    out = deduplicate([a, b])
    assert len(out) == 2


def test_near_duplicate_title_same_company_same_city_merged():
    a = _offer("linkedin", "1", "Senior Data Scientist", "Globex", city="Paris")
    b = _offer("indeed", "2", "Senior Data Scientist ", "Globex", city="Paris")
    out = deduplicate([a, b])
    assert len(out) == 1


def test_both_remote_merged_when_title_company_match():
    a = _offer("remotive", "1", "Data Scientist", "Globex", remote="remote")
    b = _offer("weworkremotely", "2", "Data Scientist", "Globex", remote="remote")
    out = deduplicate([a, b])
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Phase 14 — URL canonicalisation + fuzzy-merge guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("https://Jobs.Globex.COM/ds/", "https://jobs.globex.com/ds"),
    ("https://jobs.globex.com/ds?utm_source=linkedin&utm_campaign=x", "https://jobs.globex.com/ds"),
    ("https://boards.greenhouse.io/acme/jobs/5?gh_src=abc&t=1", "https://boards.greenhouse.io/acme/jobs/5?t=1"),
    ("https://x.com/j?b=2&a=1", "https://x.com/j?a=1&b=2"),          # params sorted
    ("https://x.com/j#section", "https://x.com/j"),                   # fragment dropped
])
def test_normalize_url_canonical_form(raw, expected):
    assert normalize_url(raw) == expected


def test_url_with_only_tracking_params_merges():
    a = _offer("indeed", "a", "DS", "Globex", url="https://jobs.globex.com/ds?utm_source=indeed")
    b = _offer("linkedin", "b", "DS", "Globex", url="https://jobs.globex.com/ds?ref=li&trk=x")
    assert len(deduplicate([a, b])) == 1


def test_url_distinguished_by_functional_param_not_merged():
    a = _offer("career_pages", "a", "Data Scientist", "Globex",
               url="https://careers.globex.com/apply?gh_jid=111", city="Paris")
    b = _offer("career_pages", "b", "Machine Learning Engineer", "Globex",
               url="https://careers.globex.com/apply?gh_jid=222", city="Paris")
    assert len(deduplicate([a, b])) == 2


def test_job_and_internship_same_title_not_fuzzy_merged():
    a = _offer("linkedin", "1", "Data Scientist", "Globex", city="Paris", job_type="job")
    b = _offer("indeed", "2", "Data Scientist", "Globex", city="Paris", job_type="internship")
    assert len(deduplicate([a, b])) == 2


def test_different_experience_level_same_title_not_fuzzy_merged():
    a = _offer("linkedin", "1", "Software Engineer", "Globex", city="Paris", experience="junior")
    b = _offer("indeed", "2", "Software Engineer", "Globex", city="Paris", experience="senior")
    assert len(deduplicate([a, b])) == 2


def test_same_experience_level_still_merges():
    a = _offer("linkedin", "1", "Software Engineer", "Globex", city="Paris", experience="mid")
    b = _offer("indeed", "2", "Software Engineer", "Globex", city="Paris", experience="mid")
    assert len(deduplicate([a, b])) == 1


def test_exact_url_match_overrides_fuzzy_guard():
    # same posting, same URL, but one row mislabels the level -> still one job
    a = _offer("a", "1", "Engineer", "Globex", url="https://g.com/e", experience="junior")
    b = _offer("b", "2", "Engineer", "Globex", url="https://g.com/e", experience="senior")
    assert len(deduplicate([a, b])) == 1        # url:: key wins before the fuzzy pass
