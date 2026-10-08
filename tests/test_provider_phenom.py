"""Phenom People — per-company /widgets POST provider."""

from __future__ import annotations

import json

import pytest

from config import settings
from schemas.jobs import JobSearchContext, JobType, ProviderState
from services.providers.http import AccessDenied, HttpError
from services.providers.phenom_provider import PhenomProvider

_CONF = json.dumps([
    {"company": "Acme", "endpoint": "https://careers.acme.com/widgets", "payload": {"pageName": "search-results"}}
])


@pytest.fixture
def ctx():
    return JobSearchContext()


@pytest.fixture(autouse=True)
def _boards(monkeypatch):
    monkeypatch.setattr(settings, "PHENOM_BOARDS", _CONF)


def _fx(job_fixtures, name):
    return (job_fixtures / name).read_text(encoding="utf-8")


def test_phenom_parses(fake_http, job_fixtures, ctx):
    fake_http.route("careers.acme.com/widgets", _fx(job_fixtures, "phenom.json"))
    result = PhenomProvider().search(ctx, limit=10)

    assert result.state is ProviderState.available
    arch = next(o for o in result.offers if "Cloud Architect" in o.title)
    assert arch.source == "phenom"
    assert arch.source_job_id == "Acme:R-45678"
    assert arch.source_url == "https://careers.acme.com/global/en/job/R-45678"   # trailing /apply stripped
    assert arch.city == "Chicago"
    assert arch.description and "landing zones" in arch.description.lower()
    assert "AWS" in arch.skills and "Technology" in arch.skills               # category + ml_skills
    assert arch.posted_at is not None

    intern = next(o for o in result.offers if "Intern" in o.title)
    assert intern.job_type == JobType.internship
    assert intern.city == "New York"


def test_phenom_disabled_without_config(monkeypatch, ctx):
    monkeypatch.setattr(settings, "PHENOM_BOARDS", "")
    assert PhenomProvider().enabled is False
    assert PhenomProvider().search(ctx, limit=10).state is ProviderState.disabled


def test_phenom_disabled_on_malformed_config(monkeypatch, ctx):
    monkeypatch.setattr(settings, "PHENOM_BOARDS", "{not json")
    assert PhenomProvider().enabled is False


def test_phenom_entry_without_endpoint_ignored(monkeypatch, ctx):
    monkeypatch.setattr(settings, "PHENOM_BOARDS", json.dumps([{"company": "X"}]))
    assert PhenomProvider().enabled is False


def test_phenom_empty_response(fake_http, ctx):
    fake_http.route("careers.acme.com/widgets", '{"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}')
    result = PhenomProvider().search(ctx, limit=10)
    assert result.state is ProviderState.available and result.offers == []


def test_phenom_all_endpoints_403(fake_http, ctx):
    fake_http.route("careers.acme.com/widgets", AccessDenied("HTTP 403"))
    assert PhenomProvider().search(ctx, limit=10).state is ProviderState.blocked


def test_phenom_all_endpoints_down(fake_http, ctx):
    fake_http.route("careers.acme.com/widgets", HttpError("down"))
    assert PhenomProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable


def test_phenom_malformed_json(fake_http, ctx):
    fake_http.route("careers.acme.com/widgets", "boom")
    assert PhenomProvider().search(ctx, limit=10).state is ProviderState.temporarily_unavailable
