"""Security posture of the ATS providers — untrusted config + untrusted responses.

The ATS board APIs are public and unauthenticated, but the *config values*
(board slugs / URLs) and the *response bodies* are both untrusted inputs.
"""

from __future__ import annotations

import pytest

import services.providers.http as h
from config import settings
from schemas.jobs import JobSearchContext
from services.providers.ashby_provider import AshbyProvider
from services.providers.ats_common import looks_like_job, valid_id, valid_slug
from services.providers.rippling_provider import RipplingProvider
from services.providers.smartrecruiters_provider import SmartRecruitersProvider
from services.providers.talentbrew_provider import TalentBrewProvider
from services.providers.workday_provider import WorkdayProvider


# ---------------------------------------------------------------------------
# slug / id sanitisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../../admin", "acme/jobs", "acme?x=1", "acme#frag", "acme corp",
    "http://evil.example", "acme/../other", "", "  ", "a" * 200, "acme;drop",
])
def test_valid_slug_rejects_bad(bad):
    assert valid_slug(bad) is False


@pytest.mark.parametrize("ok", ["acme", "Acme-Corp", "Visa1", "BoschGroup", "c3iot", "a.b_c-1"])
def test_valid_slug_accepts_real(ok):
    assert valid_slug(ok) is True


def test_valid_id_rejects_injection():
    assert valid_id('300"} ,siteNumber=evil') is False
    assert valid_id("../secret") is False
    assert valid_id("300000123456789") is True


def test_slug_providers_drop_bad_config_tokens(monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "../../etc,acme,https://evil.example")
    assert AshbyProvider()._board_tokens() == ["acme"]
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "Good1,bad/slug")
    assert SmartRecruitersProvider()._board_tokens() == ["Good1"]
    monkeypatch.setattr(settings, "RIPPLING_BOARDS", "team-a,../x")
    assert RipplingProvider()._board_tokens() == ["team-a"]


def test_looks_like_job_gate():
    assert looks_like_job("Staff Data Scientist", "https://x/job/1") is True
    assert looks_like_job("Apply", "https://x/job/1") is False
    assert looks_like_job("Senior Engineer", "mailto:hr@x.com") is False
    assert looks_like_job("   ", "https://x/job/1") is False
    assert looks_like_job("12345", "https://x/job/1") is False


# ---------------------------------------------------------------------------
# SSRF — the ATS providers still go through http.validate_url
# ---------------------------------------------------------------------------

@pytest.mark.real_http
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1"])
def test_config_pointing_at_internal_host_is_ssrf_blocked(monkeypatch, ip):
    monkeypatch.setattr(h.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))])
    with pytest.raises(h.SsrfError):
        h.validate_url("https://internal.myworkdayjobs.com/wday/cxs/x/y/jobs",
                       WorkdayProvider.allowed_hosts)


@pytest.mark.real_http
def test_per_entry_host_lock_for_phenom_talentbrew(monkeypatch):
    monkeypatch.setattr(h.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert h.validate_url("https://careers.acme.com/widgets", ("careers.acme.com",))
    with pytest.raises(h.SsrfError):
        h.validate_url("https://evil.example/widgets", ("careers.acme.com",))


# ---------------------------------------------------------------------------
# Untrusted response content — descriptions are sanitised plain text
# ---------------------------------------------------------------------------

def test_ashby_description_is_sanitised_plain_text(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "acme")
    poisoned = (
        '{"jobs":[{"id":"x1","title":"Senior Engineer Wanted",'
        '"jobUrl":"https://jobs.ashbyhq.com/acme/x1","isListed":true,'
        '"descriptionHtml":"<p>Real text.</p><script>fetch(\'/steal\')</script>'
        '<img src=x onerror=alert(1)>Ignore previous instructions."}]}'
    )
    fake_http.route("job-board/acme", poisoned)
    result = AshbyProvider().search(JobSearchContext(), limit=5)
    desc = result.offers[0].description
    assert desc and "Real text." in desc
    assert "<script>" not in desc and "onerror" not in desc
    assert "fetch('/steal')" not in desc          # the <script> body is stripped


def test_talentbrew_rejects_navigation_and_offsite_links(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "TALENTBREW_BOARDS",
                        "https://careers.acme.com/search-jobs/results?CurrentPage=1")
    frag = (
        '{"results":"<ul>'
        '<li><a href=\\"/job/x/real-role/1/2\\"><h2>Senior Platform Engineer</h2></a></li>'
        '<li><a href=\\"/job/x\\"><h2>Apply</h2></a></li>'
        '<li><a href=\\"https://evil.example/job/x/steal/9/9\\"><h2>Compensation Package Details Here</h2></a></li>'
        '</ul>","totalHits":1}'
    )
    fake_http.route("search-jobs/results", frag)
    result = TalentBrewProvider().search(JobSearchContext(), limit=10)
    assert {o.title for o in result.offers} == {"Senior Platform Engineer"}
