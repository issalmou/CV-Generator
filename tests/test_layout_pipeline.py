"""
End-to-end layout tests: real PDFs (built with ReportLab at test time) go
through ``PDFLayoutReader`` and the full parsing pipeline.

Covers spec §18:

* A  single-column PDF                         -> SINGLE_COLUMN, order kept
* B  two-column PDF                            -> TWO_COLUMNS, left then right
* C  two-column PDF with a full-width header   -> header read before columns
* D  page 1 two-column / page 2 single-column  -> per-page detection
* E  LinkedIn present ONLY as a PDF link annotation -> still recovered
* I/J  a French and an English CV reconstruct cleanly and keep their language
"""

from __future__ import annotations

import io

import fitz  # PyMuPDF
import pytest

from services.cv.pdf_layout_reader import PDFLayoutReader
from services.cv.resume_parser_pipeline import ResumeParserPipeline

pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

PAGE_W, PAGE_H = A4


def _draw_block(c, x, top, lines, leading=15.0):
    y = PAGE_H - top
    for ln in lines:
        c.drawString(x, y, ln)
        y -= leading
    return PAGE_H - y  # bottom offset from page top


def _single_page_pdf(lines) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _draw_block(c, 60, 60, lines)
    c.save()
    return buf.getvalue()


def _two_col_pdf(left, right, *, header=None, link=None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    top = 60.0
    if header:
        c.setFont("Helvetica-Bold", 16)
        for i, ln in enumerate(header):
            c.drawCentredString(PAGE_W / 2, PAGE_H - top - i * 22, ln)
        top += len(header) * 22 + 30
    c.setFont("Helvetica", 10)
    _draw_block(c, 55, top, left)
    _draw_block(c, 330, top, right)
    if link:
        text, url, (lx, ly) = link
        c.drawString(lx, PAGE_H - ly, text)
        c.linkURL(url, (lx, PAGE_H - ly - 2, lx + 90, PAGE_H - ly + 10), relative=0)
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# A. single column
# ---------------------------------------------------------------------------

def test_A_single_column_pdf_keeps_order():
    lines = [
        "MARIE CURIE",
        "marie@example.com | +33 1 23 45 67 89",
        "EXPERIENCE",
        "Researcher, Institut du Radium (2019 - 2024)",
        "Led the polonium program.",
        "EDUCATION",
        "PhD Physics, Sorbonne, 2017 - 2020",
        "SKILLS",
        "Python, R, MATLAB",
    ]
    res = PDFLayoutReader().read_bytes(_single_page_pdf(lines))
    assert res.layout_types == ["SINGLE_COLUMN"]
    text = res.text
    assert text.index("EXPERIENCE") < text.index("EDUCATION") < text.index("SKILLS")


# ---------------------------------------------------------------------------
# B. two columns
# ---------------------------------------------------------------------------

def test_B_two_column_pdf_left_then_right():
    left = ["PROFIL", "Ingénieur logiciel."] + [f"comp {i}" for i in range(8)]
    right = ["EXPERIENCE", "Dev, ACME (2021 - 2024)"] + [f"tache {i}" for i in range(8)]
    res = PDFLayoutReader().read_bytes(_two_col_pdf(left, right))
    assert res.layout_types == ["TWO_COLUMNS"]
    text = res.text
    assert "PROFIL" in text and "EXPERIENCE" in text
    # the left column is fully emitted before the right one
    assert text.index("comp 7") < text.index("EXPERIENCE")


# ---------------------------------------------------------------------------
# C. two columns + full-width header
# ---------------------------------------------------------------------------

def test_C_full_width_header_read_before_columns():
    header = ["JOHN SMITH", "Full Stack Developer", "john@example.com  +212 600 000000"]
    left = ["PROFILE", "Passionate builder."] + [f"skill {i}" for i in range(8)]
    right = ["EXPERIENCE", "Engineer, Globex (Jan 2022 - Mar 2024)"] + [f"did {i}" for i in range(8)]
    res = PDFLayoutReader().read_bytes(_two_col_pdf(left, right, header=header))
    assert res.layout_types == ["TWO_COLUMNS"]
    assert res.pages[0].has_full_width_header
    text = res.text
    assert text.index("JOHN SMITH") < text.index("PROFILE")
    assert text.index("Full Stack Developer") < text.index("EXPERIENCE")


# ---------------------------------------------------------------------------
# D. page 1 two-column, page 2 single-column
# ---------------------------------------------------------------------------

def test_D_mixed_layout_per_page():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    _draw_block(c, 55, 60, ["LEFT COL"] + [f"l{i}" for i in range(9)])
    _draw_block(c, 330, 60, ["RIGHT COL"] + [f"r{i}" for i in range(9)])
    c.showPage()
    c.setFont("Helvetica", 10)
    _draw_block(c, 60, 60, ["PAGE TWO"] + [f"line {i}" for i in range(10)])
    c.save()
    res = PDFLayoutReader().read_bytes(buf.getvalue())
    assert res.layout_types == ["TWO_COLUMNS", "SINGLE_COLUMN"]
    assert res.any_two_column_page


# ---------------------------------------------------------------------------
# E. LinkedIn only as a PDF link annotation
# ---------------------------------------------------------------------------

def test_E_linkedin_recovered_from_annotation_only(mock_llm):
    left = ["PROFIL", "Resume court."] + [f"c{i}" for i in range(8)]
    right = ["EXPERIENCE", "Stage de 3 mois, ACME"] + [f"t{i}" for i in range(8)]
    pdf = _two_col_pdf(
        left, right,
        header=["ADAM LAMBERT", "Data Analyst", "adam@example.com"],
        link=("LinkedIn", "https://www.linkedin.com/in/adam-lambert-123", (55, 720)),
    )
    # the visible text has no linkedin URL, only the word "LinkedIn"
    reader_text = PDFLayoutReader().read_bytes(pdf).text
    assert "linkedin.com/in/adam" not in reader_text

    result = ResumeParserPipeline().parse_bytes(pdf, "adam.pdf", language="en")
    assert result.cv_profile.linkedin == "https://www.linkedin.com/in/adam-lambert-123"


# ---------------------------------------------------------------------------
# I / J. French and English CVs reconstruct and keep their language
# ---------------------------------------------------------------------------

def test_I_french_cv_language_and_sections(mock_llm):
    lines = [
        "SOPHIE DURAND",
        "sophie.durand@example.fr | +33 6 12 34 56 78",
        "PROFIL",
        "Développeuse web passionnée par l'accessibilité.",
        "EXPÉRIENCE PROFESSIONNELLE",
        "Développeuse, Studio Web (janvier 2022 - mars 2024)",
        "Refonte du site principal.",
        "FORMATION",
        "Master Informatique, Université de Lyon, 2019 - 2021",
        "COMPÉTENCES",
        "Python, Django, PostgreSQL",
    ]
    result = ResumeParserPipeline().parse_bytes(_single_page_pdf(lines), "sophie.pdf", language="fr")
    assert result.language == "fr"
    assert result.cv_profile.name == "Sophie Durand"
    assert "experience" in result.sections_detected


def test_right_aligned_dates_land_on_their_own_line():
    # professional single-column CV: entity at the left margin, dates /
    # duration / location right-aligned on a far tab stop. The wide gap must
    # split them onto separate lines so the date parser sees the dates
    # instead of a date glued to the end of an institution name.
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    rows = [
        ("EXPERIENCE", None),
        ("ACME Corporation", "3 months"),
        ("Backend Engineer Intern", "Casablanca, Morocco"),
        ("Did a lot of useful backend work on the billing service", None),
        ("EDUCATION", None),
        ("National Institute of Statistics and Applied Economics", "2025 - Present"),
        ("M.Sc. Data Science", "Rabat, Morocco"),
        ("University of Science and Technology", "2022 - 2025"),
        ("B.Sc. Computer Science", "Casablanca, Morocco"),
        ("SKILLS", None),
        ("Python, SQL, Docker, FastAPI", None),
    ]
    y = PAGE_H - 80
    for left, right in rows:
        c.drawString(45, y, left)
        if right:
            c.drawRightString(PAGE_W - 45, y, right)
        y -= 26
    c.save()

    text = PDFLayoutReader().read_bytes(buf.getvalue()).text
    assert "\n2025 - Present" in text
    assert "\n3 months" in text
    # the date is NOT stuck to the institution name
    assert "Applied Economics 2025" not in text


def test_ambiguous_multizone_layout_is_flagged(mock_llm):
    # THREE stacked text columns (a Canva / Europass grid) -> the
    # single-column top-to-bottom read scrambles them; it must be flagged
    # low-confidence (spec §15). A normal 1-column CV with a right-aligned
    # date tab stop must NOT trip this (see test_right_aligned_dates...).
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 9)
    # a full-width intro paragraph on top defeats any single clean gutter
    for i in range(4):
        c.drawString(35, PAGE_H - (70 + i * 15), "x" * 90)
    for i in range(18):
        y = 150 + i * 26
        c.drawString(35, PAGE_H - y, f"left column line {i} here")
        c.drawString(240, PAGE_H - y, f"middle column line {i} here")
        c.drawString(445, PAGE_H - y, f"right col {i}")
    c.save()
    res = PDFLayoutReader().read_bytes(buf.getvalue())
    assert res.low_confidence
    assert res.pages[0].confidence < 0.6

    result = ResumeParserPipeline().parse_bytes(buf.getvalue(), "messy.pdf", language="en")
    layout_issues = [i for i in result.validation_issues if i["field"] == "layout"]
    assert layout_issues and layout_issues[0]["severity"] == "warning"
    assert all(v <= 55 for v in result.confidence.values())


def test_J_english_cv_language_and_sections(mock_llm):
    lines = [
        "JAMES WILSON",
        "james.wilson@example.com | +1 415 555 0100",
        "SUMMARY",
        "Backend engineer with a focus on reliability.",
        "WORK EXPERIENCE",
        "Backend Engineer, Initech (2022 - Present)",
        "Owned the payments pipeline.",
        "EDUCATION",
        "BSc Computer Science, MIT, 2018 - 2022",
        "SKILLS",
        "Go, Kubernetes, PostgreSQL",
    ]
    result = ResumeParserPipeline().parse_bytes(_single_page_pdf(lines), "james.pdf", language="en")
    assert result.language == "en"
    assert result.cv_profile.name == "James Wilson"
    assert "experience" in result.sections_detected
