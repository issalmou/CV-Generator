"""
Integration tests: file -> text extraction for real PDF and DOCX inputs.

The fixtures are generated at test time (ReportLab for PDF, python-docx
for DOCX) so the suite carries no binary blobs, while still exercising the
real ``PyMuPDF`` / ``python-docx`` code paths end to end.
"""

from __future__ import annotations

import io

import pytest

from services.cv.resume_parser_pipeline import ResumeParserPipeline
from services.cv.resume_text_extractor import (
    ResumeTextExtractor,
    UnsupportedFileTypeError,
)

_RESUME_LINES = [
    "JOHN DOE",
    "john.doe@example.com | +212 600 112233 | linkedin.com/in/johndoe",
    "",
    "PROFESSIONAL SUMMARY",
    "Backend engineer focused on data platforms.",
    "",
    "WORK EXPERIENCE",
    "Software Engineer, Acme Corp - Remote",
    "Jan 2021 - Present",
    "Shipped the billing service",
    "",
    "EDUCATION",
    "Master in Data Science, INSEA, 2024",
    "",
    "SKILLS",
    "Languages: Python, SQL",
]


@pytest.fixture
def pdf_bytes() -> bytes:
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in _RESUME_LINES:
        c.drawString(50, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


@pytest.fixture
def docx_bytes() -> bytes:
    docx = pytest.importorskip("docx")
    from docx import Document

    doc = Document()
    for line in _RESUME_LINES:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_pdf_text_extraction(pdf_bytes):
    text = ResumeTextExtractor().extract_document_from_bytes(pdf_bytes, "resume.pdf").text
    assert "JOHN DOE" in text
    assert "WORK EXPERIENCE" in text
    assert "Python" in text


def test_docx_text_extraction(docx_bytes):
    text = ResumeTextExtractor().extract_document_from_bytes(docx_bytes, "resume.docx").text
    assert "JOHN DOE" in text
    assert "EDUCATION" in text


def test_unsupported_extension_rejected():
    with pytest.raises(UnsupportedFileTypeError):
        ResumeTextExtractor().extract_document_from_bytes(b"x", "resume.pages")


def test_pipeline_on_generated_pdf(mock_llm, pdf_bytes):
    result = ResumeParserPipeline().parse_bytes(pdf_bytes, "resume.pdf", language="en")
    assert result.cv_profile.name == "John Doe"
    assert result.cv_profile.email == "john.doe@example.com"
    assert "experience" in result.sections_detected or result.cv_profile.experience
    assert result.language == "en"


def test_pipeline_on_generated_docx(mock_llm, docx_bytes):
    result = ResumeParserPipeline().parse_bytes(docx_bytes, "resume.docx", language="en")
    assert result.cv_profile.name == "John Doe"
    assert result.cv_profile.skills  # local skills extraction worked
