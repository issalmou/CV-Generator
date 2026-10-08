"""Job-search Pydantic schemas: enum coercion, context merge, request bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.applications import ApplicationStatus
from schemas.jobs import (
    FreshnessStatus, JobSearchContext, JobSearchRequest, JobType, RemoteType,
    freshness_at_least,
)


def test_context_list_fields_coerce_from_string():
    ctx = JobSearchContext(skills="Python, SQL; Machine Learning")
    assert ctx.skills == ["Python", "SQL", "Machine Learning"]


def test_context_merge_scalars_new_wins_lists_union():
    a = JobSearchContext(query="data scientist", skills=["Python"], location="Paris")
    b = JobSearchContext(query="ML engineer", skills=["Python", "SQL"], remote_type=RemoteType.remote)
    merged = a.merge(b)
    assert merged.query == "ML engineer"                 # later message overrides
    assert merged.location == "Paris"                    # kept — b didn't set it
    assert merged.skills == ["Python", "SQL"]            # union, de-duped
    assert merged.remote_type == RemoteType.remote


def test_context_merge_ignores_empty_new_values():
    a = JobSearchContext(query="data scientist", job_type=JobType.internship)
    merged = a.merge({"query": "", "job_type": None})
    assert merged.query == "data scientist"
    assert merged.job_type == JobType.internship


def test_context_merge_accepts_dict_and_none():
    a = JobSearchContext(query="x")
    assert a.merge(None).query == "x"
    assert a.merge({"location": "Lyon"}).location == "Lyon"


def test_search_request_bounds():
    with pytest.raises(ValidationError):
        JobSearchRequest(page_size=999)
    req = JobSearchRequest(sources=["LinkedIn", " indeed "])
    assert req.sources == ["linkedin", "indeed"]


def test_freshness_ordering():
    assert freshness_at_least(FreshnessStatus.fresh, FreshnessStatus.stale)
    assert not freshness_at_least(FreshnessStatus.expired, FreshnessStatus.stale)
    assert freshness_at_least(FreshnessStatus.stale, FreshnessStatus.stale)


def test_application_status_has_no_submitted():
    """The service never completes an application on an external site, so no
    status may imply that it did."""
    values = {s.value for s in ApplicationStatus}
    for forbidden in ("submitted", "successfully_submitted", "applied", "sent", "completed"):
        assert forbidden not in values
    # the known, deliberately-limited set (Phase 27 added `duplicate`,
    # Phase 46 added `prepared` — still nothing that means "submitted externally")
    assert values == {
        "prepared", "manual_required", "requires_user_action",
        "unavailable", "failed", "duplicate",
    }
