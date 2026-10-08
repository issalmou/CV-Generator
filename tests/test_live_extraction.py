"""
Opt-in LIVE test: run /api/extract-cv against the real LLM backend on real
resume files.

Skipped unless BOTH:
  * a valid NVIDIA_API_KEY is configured, and
  * env var CV_LIVE_RESUME_DIR points at a folder containing .pdf/.docx CVs

Run it explicitly with:

    CV_LIVE_RESUME_DIR=/path/to/cvs pytest tests/test_live_extraction.py -v -s

It makes real (billable, rate-limited) LLM calls, so it is never part of
the default suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import services.gemini_client as gemini_client

_RESUME_DIR = os.getenv("CV_LIVE_RESUME_DIR")

pytestmark = [
    pytest.mark.skipif(not gemini_client.is_configured(), reason="NVIDIA_API_KEY not configured"),
    pytest.mark.skipif(not _RESUME_DIR, reason="CV_LIVE_RESUME_DIR not set"),
]


def _resume_files() -> list[Path]:
    if not _RESUME_DIR:
        return []
    return sorted(
        p for p in Path(_RESUME_DIR).iterdir()
        if p.suffix.lower() in (".pdf", ".docx")
    )


@pytest.mark.parametrize("resume_path", _resume_files(), ids=lambda p: p.name)
def test_live_extract_cv(resume_path: Path):
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    with resume_path.open("rb") as fh:
        resp = client.post(
            "/api/extract-cv",
            files={"file": (resume_path.name, fh.read(), "application/octet-stream")},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Contract holds for every real document, however messy.
    assert body["status"] == "success"
    assert body["language"] in ("fr", "en")
    assert set(body["confidence_scores"]) == {
        "contact", "experience", "education", "projects", "skills"
    }
    prof = body["cv_profile"]
    for key in ("name", "email", "education", "experience", "skills"):
        assert key in prof

    # No fabrication: list sections are lists, optional scalars are str|None.
    for section in ("education", "experience", "projects", "skills", "languages",
                    "certifications", "interests", "personal_qualities"):
        assert isinstance(prof[section], list)
    for scalar in ("name", "email", "phone", "linkedin", "github", "address", "nationality"):
        assert prof[scalar] is None or isinstance(prof[scalar], str)
    # a linkedin value, if any, must be a real linkedin URL (from text or annotation)
    if prof["linkedin"]:
        assert "linkedin.com" in prof["linkedin"].lower()
    # personal_qualities never carries an email / the candidate's name
    for q in prof["personal_qualities"]:
        assert "@" not in q
    # skills is a FLAT list of individual strings — never a {category, skills} object
    for s in prof["skills"]:
        assert isinstance(s, str) and "," not in s

    # New schema: education has start_date/end_date, experience has period,
    # technologies are individual strings, summary carries no contact info.
    for edu in prof["education"]:
        assert set(edu) == {"institution", "degree", "field", "start_date", "end_date", "gpa", "location"}
    for exp in prof["experience"]:
        assert "period" in exp and "start_date" not in exp
        assert all("," not in t for t in exp["technologies"])
    if prof["professional_summary"]:
        assert "@" not in prof["professional_summary"] or ".com" not in prof["professional_summary"]

    # A real CV should yield at least contact OR skills signal.
    assert prof["email"] or prof["name"] or prof["skills"]

    print(f"\n{resume_path.name}: lang={body['language']} "
          f"name={prof['name']!r} exp={len(prof['experience'])} "
          f"edu={len(prof['education'])} conf={body['confidence_scores']}")
