"""
CV Assistant - Cover Letter
Service: LetterPDFGenerator

Renders a cover letter to PDF **bytes** with ReportLab — nothing is ever
written to disk (mirrors :class:`services.cv.pdf_generator.PDFGenerator`).

Layout: a two-column header (candidate block left, date + location right),
a thin accent rule, a right-aligned recipient block, a bold subject line,
the justified letter body, and a signature. Section labels, the subject
fallback and the date format follow the ``language`` argument
(``"fr"`` / ``"en"``). When the job description yielded no company name,
the recipient block and the position in the subject are simply omitted —
never replaced with a placeholder.
"""

from __future__ import annotations

import logging
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
PAGE_WIDTH, _PAGE_HEIGHT = A4
MARGIN_L = MARGIN_R = 20 * mm
MARGIN_TOP = 22 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_L - MARGIN_R
LEFT_COL_W = CONTENT_WIDTH * 0.58
RIGHT_COL_W = CONTENT_WIDTH * 0.42

SP_XS, SP_SM, SP_MD, SP_LG = 2 * mm, 3.5 * mm, 6 * mm, 10 * mm

COL_NAME = colors.HexColor("#1A1A2E")
COL_ACCENT = colors.HexColor("#2E4057")
COL_BODY = colors.HexColor("#2D2D2D")
COL_META = colors.HexColor("#555555")

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "subject_label": "Subject:",
        "subject_fallback_with_role": "Application for the {role} position",
        "subject_fallback_plain": "Job application",
        "date_format": "%B %d, %Y",
    },
    "fr": {
        "subject_label": "Objet :",
        "subject_fallback_with_role": "Candidature au poste de {role}",
        "subject_fallback_plain": "Candidature",
        "date_format": "%d %B %Y",
    },
}
_FR_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _t(language: str) -> dict[str, str]:
    return _TRANSLATIONS.get(language, _TRANSLATIONS["en"])


def _today_localised(language: str) -> str:
    today = date.today()
    if language == "fr":
        return f"{today.day} {_FR_MONTHS[today.month - 1]} {today.year}"
    return today.strftime("%B %d, %Y")


class LetterPDFGenerator:
    """Cover-letter PDF renderer. ``render_to_bytes`` only — no disk I/O."""

    def render_to_bytes(
        self,
        candidate: dict,
        company: dict,
        letter_body: str,
        *,
        language: str = "en",
    ) -> bytes:
        """
        Parameters
        ----------
        candidate:
            ``name`` / ``email`` / ``phone`` / ``address`` (strings; ``""`` when absent).
        company:
            ``company_name`` / ``position`` / ``recipient`` / ``company_address``
            (strings; ``""`` when absent).
        letter_body:
            The letter text produced by :class:`LetterGenerator`.
        language:
            ``"fr"`` or ``"en"`` — drives labels, the subject fallback and the date.

        Returns
        -------
        bytes
            The rendered PDF.
        """
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=MARGIN_L, rightMargin=MARGIN_R,
            topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
            title="Cover Letter",
        )
        doc.build(self._story(candidate, company, letter_body, language))
        pdf = buf.getvalue()
        buf.close()
        logger.info("[LetterPDFGenerator] Rendered | language=%s | bytes=%d", language, len(pdf))
        return pdf

    # ------------------------------------------------------------------

    @staticmethod
    def _styles() -> dict[str, ParagraphStyle]:
        return {
            "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=18,
                                   leading=22, textColor=COL_NAME, alignment=TA_LEFT),
            "meta_left": ParagraphStyle("meta_left", fontName="Helvetica", fontSize=9,
                                        leading=13, textColor=COL_META, alignment=TA_LEFT),
            "meta_right": ParagraphStyle("meta_right", fontName="Helvetica", fontSize=9,
                                         leading=13, textColor=COL_META, alignment=TA_RIGHT),
            "company_name": ParagraphStyle("company_name", fontName="Helvetica-Bold", fontSize=9,
                                           leading=13, textColor=COL_BODY, alignment=TA_RIGHT),
            "subject": ParagraphStyle("subject", fontName="Helvetica-Bold", fontSize=10,
                                      leading=14, textColor=COL_NAME, alignment=TA_LEFT),
            "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10,
                                   leading=15, textColor=COL_BODY, alignment=TA_JUSTIFY),
            "closing": ParagraphStyle("closing", fontName="Helvetica", fontSize=10,
                                      leading=14, textColor=COL_BODY, alignment=TA_LEFT),
        }

    def _story(self, candidate: dict, company: dict, letter_body: str, language: str) -> list:
        s = self._styles()
        t = _t(language)
        story: list = []

        # 1. Header — candidate (left) | date + location (right)
        left = _vstack([
            Paragraph(_esc(candidate.get("name", "")), s["name"]),
            Spacer(1, SP_XS),
            Paragraph(_esc(candidate.get("address", "")), s["meta_left"]),
            Paragraph(_esc(candidate.get("email", "")), s["meta_left"]),
            Paragraph(_esc(candidate.get("phone", "")), s["meta_left"]),
        ])
        right = _vstack([
            Paragraph(_today_localised(language), s["meta_right"]),
            Paragraph(_esc(company.get("location", "")), s["meta_right"]),
        ])
        header = Table([[left, right]], colWidths=[LEFT_COL_W, RIGHT_COL_W], hAlign="LEFT")
        header.setStyle(_ZERO_PAD)
        story.append(header)
        story.append(Spacer(1, SP_SM))

        # 2. Accent rule
        story.append(HRFlowable(width="100%", thickness=0.6, color=COL_ACCENT))
        story.append(Spacer(1, SP_MD))

        # 3. Recipient / company block (right-aligned) — omitted when unknown
        company_cells: list = []
        if company.get("recipient"):
            company_cells.append(Paragraph(_esc(company["recipient"]), s["meta_right"]))
        if company.get("company_name"):
            company_cells.append(Paragraph(_esc(company["company_name"]), s["company_name"]))
        for line in (company.get("company_address", "") or "").splitlines():
            if line.strip():
                company_cells.append(Paragraph(_esc(line.strip()), s["meta_right"]))
        if company_cells:
            block = Table([["", _vstack(company_cells)]],
                          colWidths=[LEFT_COL_W, RIGHT_COL_W], hAlign="LEFT")
            block.setStyle(_ZERO_PAD)
            story.append(block)
            story.append(Spacer(1, SP_MD))

        # 4. Subject line
        story.append(Paragraph(
            f'{t["subject_label"]} {_esc(self._subject(company, t))}', s["subject"]))
        story.append(Spacer(1, SP_MD))

        # 5. Body (greeting + paragraphs + closing come from the LLM text)
        for i, block_text in enumerate(_split_paragraphs(letter_body)):
            story.append(Paragraph(_esc(block_text), s["body"]))
            story.append(Spacer(1, SP_SM))

        # 6. Signature
        story.append(Spacer(1, SP_LG))
        story.append(Paragraph(_esc(candidate.get("name", "")), s["closing"]))
        return story

    @staticmethod
    def _subject(company: dict, t: dict[str, str]) -> str:
        role = (company.get("position") or "").strip()
        if role:
            return t["subject_fallback_with_role"].format(role=role)
        return t["subject_fallback_plain"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ZERO_PAD = TableStyle([
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
])


def _vstack(items: list) -> Table:
    """Wrap flowables into a zero-padding single-column table (a CSS flex-column)."""
    tbl = Table([[it] for it in items], colWidths=[None])
    tbl.setStyle(_ZERO_PAD)
    return tbl


def _split_paragraphs(letter_body: str) -> list[str]:
    """Split on blank lines; collapse intra-paragraph newlines to spaces."""
    blocks = [b.strip() for b in (letter_body or "").strip().split("\n\n") if b.strip()]
    return [" ".join(b.splitlines()) for b in blocks]


# Helvetica has no glyph for the fancy dashes / quotes an LLM sometimes
# emits; map them to ASCII so they don't render as tofu.
_GLYPH_FALLBACKS = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": " - ",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ", " ": " ",
}


def _esc(text: str) -> str:
    """Normalise exotic glyphs, then escape ReportLab's mini-markup specials."""
    text = text or ""
    for bad, good in _GLYPH_FALLBACKS.items():
        text = text.replace(bad, good)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
