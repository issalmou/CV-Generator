"""
LOT 6 — the CV targets ONE page by ADAPTIVE layout, never by dropping the
candidate's content.

- every experience / every bullet / every project is rendered;
- fonts/margins/spacing shrink to a readability floor (CV_MIN_BODY_FONT_PT);
- if it still overflows, the CV flows to a 2nd page and cv_data["_layout"]
  reports fit_one_page=False — nothing is silently lost.
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from config import settings
from services.cv.cv_generator import CVGenerator
from services.cv.pdf_generator import PDFGenerator, _compact_cv_data


def _pdf_text(b: bytes) -> str:
    doc = fitz.open(stream=b, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _pdf_pages(b: bytes) -> int:
    return fitz.open(stream=b, filetype="pdf").page_count


def _cv_data(n_exp: int, bullets_per_exp: int) -> dict:
    return {
        "header": {"name": "Jane Roe", "email": "jane@example.com", "phone": "+1 555 0100"},
        "summary": {"professional_summary": "Backend engineer.", "career_objective": ""},
        "skills": [{"category": "Languages", "skills": ["Python", "Go", "SQL"]}],
        "experience": [
            {
                "company": f"Company {i}", "position": f"Engineer {i}",
                "period": "2020 - 2024", "location": "Remote",
                "bullets": [f"UNIQUEBULLET-{i}-{j} did a concrete measurable thing here"
                            for j in range(bullets_per_exp)],
                "technologies": ["Python"],
            }
            for i in range(n_exp)
        ],
        "projects": [], "education": [], "certifications": [], "languages": [],
        "language": "en",
    }


def test_compact_does_not_slice_bullets_or_projects():
    data = {
        "summary": {"professional_summary": "x"},
        "experience": [{"bullets": [f"b{i}" for i in range(9)]}],
        "projects": [{"description": "d"} for _ in range(7)],
    }
    out = _compact_cv_data(data)
    assert len(out["experience"][0]["bullets"]) == 9   # was capped at 3 before
    assert len(out["projects"]) == 7                    # was capped at 3 before


def test_all_bullets_render_for_a_normal_cv():
    data = _cv_data(n_exp=4, bullets_per_exp=5)
    pdf = PDFGenerator().render_to_bytes(data, language="en")
    text = _pdf_text(pdf).replace("\n", " ")
    for i in range(4):
        for j in range(5):
            assert f"UNIQUEBULLET-{i}-{j}" in text

    layout = data["_layout"]
    assert layout["body_font_pt"] >= settings.CV_MIN_BODY_FONT_PT
    assert isinstance(layout["fit_one_page"], bool)


def test_huge_cv_flows_to_more_pages_without_losing_content():
    n_exp, n_bul = 18, 10
    data = _cv_data(n_exp=n_exp, bullets_per_exp=n_bul)
    # make each bullet long so it wraps — a genuinely oversized CV
    for exp in data["experience"]:
        exp["bullets"] = [b + " " + "and then more detail about the outcome and the stack used"
                          for b in exp["bullets"]]
    pdf = PDFGenerator().render_to_bytes(data, language="en")
    text = _pdf_text(pdf).replace("\n", " ")

    # every single bullet still present
    for i in range(n_exp):
        for j in range(n_bul):
            assert f"UNIQUEBULLET-{i}-{j}" in text

    layout = data["_layout"]
    assert layout["fit_one_page"] is False
    assert layout["pages"] >= 2
    assert _pdf_pages(pdf) >= 2
    assert layout["body_font_pt"] >= settings.CV_MIN_BODY_FONT_PT   # never below the floor


def test_short_cv_fits_one_page_at_full_scale():
    data = _cv_data(n_exp=2, bullets_per_exp=2)
    pdf = PDFGenerator().render_to_bytes(data, language="en")
    assert _pdf_pages(pdf) == 1
    assert data["_layout"]["fit_one_page"] is True
    assert data["_layout"]["scale"] == 1.0


def test_ats_optimizer_prompt_no_longer_slices_experiences(mock_llm):
    from cv_models import ATSAnalysis, CVProfile
    from services.cv.ats_optimizer import ATSOptimizer

    profile = CVProfile(
        name="J", email="j@x.com", phone="+1",
        experience=[
            {"company": f"C{i}", "position": f"P{i}", "period": "2020-2021",
             "achievements": [f"ACH-{i}-{k}" for k in range(5)], "technologies": []}
            for i in range(6)
        ],
    )
    ats = ATSAnalysis(missing_keywords=["Kafka"], ats_score=30.0)
    ATSOptimizer().optimize_content(profile, {"professional_summary": "s"}, ats, "Kafka role")
    _, prompt = mock_llm.prompts[-1]
    # all 6 roles + their 5th achievement appear (old code stopped at role 4, ach 3)
    assert "C5 @" in prompt.replace(" @ C5", "C5 @") or "@ C5" in prompt
    assert "ACH-5-4" in prompt
    assert "ACH-0-4" in prompt
