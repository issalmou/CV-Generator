"""
CV Assistant - CV Generation
Service: PDFGenerator
"""

import logging
from io import BytesIO
from typing import Any

from config import settings

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

PAGE_WIDTH, PAGE_HEIGHT = A4          # 595.3 x 841.9 pt
MARGIN = 14 * mm                      # même valeur pour top/right/bottom/left

COL_ACCENT = colors.HexColor("#1A3C5E")
COL_RULE   = colors.HexColor("#2E6DA4")
COL_BODY   = colors.HexColor("#212121")
COL_MUTED  = colors.HexColor("#555555")
COL_LINK   = "#2E6DA4"

FONT_REGULAR = "Helvetica"
FONT_BOLD    = "Helvetica-Bold"
FONT_ITALIC  = "Helvetica-Oblique"

# LLM-text sanity caps (NOT a fitting mechanism — see _compact_cv_data).
# The candidate's real content — every experience, every bullet, every
# project — is never dropped here (constraint #7).

# ---------------------------------------------------------------------------
# Translations — section titles & inline labels only. The candidate's own
# data (names, companies, dates, descriptions...) is NEVER translated; only
# this fixed set of UI labels changes with `language`.
# ---------------------------------------------------------------------------

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "professional_summary": "Professional Summary",
        "skills": "Skills",
        "professional_experience": "Professional Experience",
        "projects": "Projects",
        "education": "Education",
        "certifications": "Certifications",
        "languages": "Languages",
        "tech_label": "Tech",
        "stack_label": "Stack",
        "gpa_label": "GPA",
        "present": "Present",
        "linkedin": "LinkedIn",
        "github": "GitHub",
        "portfolio": "Portfolio",
        "year_singular": "year",
        "year_plural": "years",
        "month_singular": "month",
        "month_plural": "months",
    },
    "fr": {
        "professional_summary": "Résumé Professionnel",
        "skills": "Compétences",
        "professional_experience": "Expérience Professionnelle",
        "projects": "Projets",
        "education": "Formation",
        "certifications": "Certifications",
        "languages": "Langues",
        "tech_label": "Technos",
        "stack_label": "Stack",
        "gpa_label": "Moyenne",
        "present": "Présent",
        "linkedin": "LinkedIn",
        "github": "GitHub",
        "portfolio": "Portfolio",
        "year_singular": "an",
        "year_plural": "ans",
        "month_singular": "mois",
        "month_plural": "mois",
    },
}


def _t(language: str) -> dict[str, str]:
    """Return the translation table for `language`, defaulting to English."""
    return _TRANSLATIONS.get(language, _TRANSLATIONS["en"])


# ---------------------------------------------------------------------------
# Style factory  — built dynamically with a scale factor
# ---------------------------------------------------------------------------

def _build_styles(scale: float = 1.0) -> dict:
    def fs(size):  # scaled font size
        return size * scale
    def ls(size):  # scaled leading
        return size * scale
    def sp(size):  # scaled spacing
        return size * scale

    s = {}
    s["name"] = ParagraphStyle(
        "name", fontName=FONT_BOLD, fontSize=fs(17), leading=ls(21),
        textColor=COL_ACCENT, alignment=TA_CENTER, spaceAfter=sp(1),
    )
    s["contact"] = ParagraphStyle(
        "contact", fontName=FONT_REGULAR, fontSize=fs(8.5), leading=ls(11),
        textColor=COL_MUTED, alignment=TA_CENTER, spaceAfter=sp(2),
    )
    s["section_title"] = ParagraphStyle(
        "section_title", fontName=FONT_BOLD, fontSize=fs(10), leading=ls(13),
        textColor=COL_ACCENT, spaceBefore=sp(6), spaceAfter=sp(1),
    )
    s["company_line"] = ParagraphStyle(
        "company_line", fontName=FONT_BOLD, fontSize=fs(9.5), leading=ls(12),
        textColor=COL_BODY, spaceBefore=sp(4),
    )
    s["role_line"] = ParagraphStyle(
        "role_line", fontName=FONT_ITALIC, fontSize=fs(9), leading=ls(11),
        textColor=COL_MUTED,
    )
    s["body"] = ParagraphStyle(
        "body", fontName=FONT_REGULAR, fontSize=fs(9), leading=ls(12),
        textColor=COL_BODY, alignment=TA_JUSTIFY,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", fontName=FONT_REGULAR, fontSize=fs(9), leading=ls(11.5),
        textColor=COL_BODY, leftIndent=sp(10), firstLineIndent=sp(-10), spaceAfter=sp(1),
    )
    s["skills_row"] = ParagraphStyle(
        "skills_row", fontName=FONT_REGULAR, fontSize=fs(9), leading=ls(12),
        textColor=COL_BODY, spaceAfter=sp(1),
    )
    s["small_muted"] = ParagraphStyle(
        "small_muted", fontName=FONT_ITALIC, fontSize=fs(8.5), leading=ls(11),
        textColor=COL_MUTED,
    )
    s["tbl_left_bold"] = ParagraphStyle(
        "tbl_left_bold", fontName=FONT_BOLD, fontSize=fs(9.5), leading=ls(12),
        textColor=COL_BODY, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT,
    )
    s["tbl_right"] = ParagraphStyle(
        "tbl_right", fontName=FONT_ITALIC, fontSize=fs(8.5), leading=ls(12),
        textColor=COL_MUTED, spaceBefore=0, spaceAfter=0, alignment=TA_RIGHT,
    )
    s["tbl_right_muted_small"] = ParagraphStyle(
        "tbl_right_muted_small", fontName=FONT_ITALIC, fontSize=fs(8.5), leading=ls(11),
        textColor=COL_MUTED, spaceBefore=0, spaceAfter=0, alignment=TA_RIGHT,
    )
    return s


# ---------------------------------------------------------------------------
# Table helpers  — take content_width as parameter
# ---------------------------------------------------------------------------

def _two_col_table(left: Paragraph, right: Paragraph, content_width: float) -> Table:
    col_l = content_width * 0.68
    col_r = content_width * 0.32
    t = Table([[left, right]], colWidths=[col_l, col_r])
    t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("ALIGN",         (0, 0), (0, 0),   "LEFT"),
        ("ALIGN",         (1, 0), (1, 0),   "RIGHT"),
    ]))
    t.hAlign = "LEFT"
    return t


def _make_rule(content_width: float) -> Table:
    """
    Ligne de séparation.
    rowHeights=[0.5] → hauteur quasi-nulle → LINEBELOW collé juste sous le titre.
    content_width identique à tous les autres Table → alignement parfait.
    """
    t = Table([[""]], colWidths=[content_width], rowHeights=[0.5])
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0, 0), (-1, -1), 0.75, COL_RULE),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    t.hAlign = "LEFT"
    return t


# ---------------------------------------------------------------------------
# Content pre-processor
# ---------------------------------------------------------------------------

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > max_chars // 2:
            return cut[:idx + 1].strip()
    return cut.rstrip() + "\u2026"


# Helvetica (ReportLab's built-in) has no glyph for these; an LLM emits
# them occasionally. Map to ASCII so they don't render as blank boxes.
_GLYPH_FALLBACKS = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": " - ",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ", " ": " ", " ": " ",
}


def _normalise_glyphs(obj):
    """Recursively replace Helvetica-missing glyphs in every string of a dict/list tree."""
    if isinstance(obj, str):
        for bad, good in _GLYPH_FALLBACKS.items():
            obj = obj.replace(bad, good)
        return obj
    if isinstance(obj, list):
        return [_normalise_glyphs(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _normalise_glyphs(v) for k, v in obj.items()}
    return obj


def _compact_cv_data(cv_data: dict) -> dict:
    """Normalise glyphs and apply ONLY sanity caps to LLM-generated prose
    (summary / project description). It NEVER drops an experience, a bullet or
    a project — one-page fitting is done by adaptive layout, not by hiding the
    candidate's content (constraint #7)."""
    import copy
    data = _normalise_glyphs(copy.deepcopy(cv_data))

    summary = data.get("summary", {})
    text = summary.get("professional_summary", "")
    cap = settings.CV_SUMMARY_HARD_CAP
    if text and len(text) > cap:
        logger.warning("[PDFGenerator] professional_summary %d chars > sanity cap %d — trimmed "
                       "(LLM prose, not candidate facts)", len(text), cap)
        summary["professional_summary"] = _truncate(text, cap)

    pcap = settings.CV_PROJECT_DESC_HARD_CAP
    for proj in data.get("projects", []):
        desc = proj.get("description", "")
        if desc and len(desc) > pcap:
            logger.warning("[PDFGenerator] project description %d chars > sanity cap %d — trimmed",
                           len(desc), pcap)
            proj["description"] = _truncate(desc, pcap)
    return data


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _display_url(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


# ---------------------------------------------------------------------------
# Story builder  — scale + content_width aware
# ---------------------------------------------------------------------------

def _build_story(cv_data: dict, scale: float, content_width: float, language: str = "en") -> list:
    data = _compact_cv_data(cv_data)
    st   = _build_styles(scale)
    sp   = lambda x: x * scale   # spacing helper
    t    = _t(language)

    story = []

    def section_rule(block, title):
        block.append(Paragraph(title.upper(), st["section_title"]))
        block.append(_make_rule(content_width))
        block.append(Spacer(1, sp(2) * mm))

    # ── Header ────────────────────────────────────────────────────────
    header = data.get("header", {})
    story.append(Paragraph(header.get("name", ""), st["name"]))
    contact_parts = [header[k] for k in ("email", "phone", "address") if header.get(k)]
    if contact_parts:
        story.append(Paragraph(" | ".join(contact_parts), st["contact"]))
    link_parts = []
    for key, label_key in (("linkedin", "linkedin"), ("github", "github"), ("portfolio", "portfolio")):
        if header.get(key):
            url = _normalise_url(header[key])
            display = _display_url(url)
            link_parts.append(f'{t[label_key]}: <a href="{url}" color="{COL_LINK}">{display}</a>')
    if link_parts:
        story.append(Paragraph(" | ".join(link_parts), st["contact"]))
    story.append(Spacer(1, sp(1.5) * mm))

    # ── Summary ───────────────────────────────────────────────────────
    summary_text = data.get("summary", {}).get("professional_summary", "").strip()
    if summary_text:
        block = []
        section_rule(block, t["professional_summary"])
        block.append(Paragraph(summary_text, st["body"]))
        block.append(Spacer(1, sp(1) * mm))
        story.append(KeepTogether(block))

    # ── Skills ────────────────────────────────────────────────────────
    skills = data.get("skills", [])
    if skills:
        block = []
        section_rule(block, t["skills"])
        for cat in skills:
            cat_name   = cat.get("category", "")
            cat_skills = cat.get("skills", [])
            if cat_skills:
                block.append(Paragraph(
                    f"<b>{cat_name}:</b> {', '.join(cat_skills)}", st["skills_row"]
                ))
        block.append(Spacer(1, sp(1) * mm))
        story.append(KeepTogether(block))

    # ── Experience ────────────────────────────────────────────────────
    experiences = data.get("experience", [])
    if experiences:
        header_block = []
        section_rule(header_block, t["professional_experience"])
        story.append(KeepTogether(header_block))
        for exp in experiences:
            # The candidate-supplied period string is shown verbatim on the
            # right (extraction no longer produces separate start/end dates
            # to compute a duration from).
            period = (exp.get("period") or "").strip()
            block = []
            block.append(Spacer(1, sp(1)))
            block.append(_two_col_table(
                Paragraph(exp.get("company", ""),  st["tbl_left_bold"]),
                Paragraph(period,                  st["tbl_right"]),
                content_width,
            ))
            block.append(_two_col_table(
                Paragraph(f"<i>{exp.get('position', '')}</i>", ParagraphStyle(
                    "role_loc_left",
                    fontName=FONT_ITALIC, fontSize=st["tbl_right_muted_small"].fontSize,
                    leading=st["tbl_right_muted_small"].leading,
                    textColor=COL_MUTED, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT,
                )),
                Paragraph(exp.get("location", ""), st["tbl_right_muted_small"]),
                content_width,
            ))
            for bullet in exp.get("bullets", []):
                block.append(Paragraph(f"\u2022 {bullet}", st["bullet"]))
            if exp.get("technologies"):
                block.append(Paragraph(
                    f"<b>{t['tech_label']}:</b> {', '.join(exp['technologies'])}", st["small_muted"]
                ))
            block.append(Spacer(1, sp(1) * mm))
            story.append(KeepTogether(block))

    # ── Projects ──────────────────────────────────────────────────────
    projects = data.get("projects", [])
    if projects:
        header_block = []
        section_rule(header_block, t["projects"])
        story.append(KeepTogether(header_block))
        for proj in projects:
            block = []
            block.append(Paragraph(proj.get("title", ""), st["company_line"]))
            if proj.get("description"):
                block.append(Paragraph(proj["description"], st["body"]))
            if proj.get("technologies"):
                block.append(Paragraph(
                    f"<b>{t['stack_label']}:</b> {', '.join(proj['technologies'])}", st["small_muted"]
                ))
            links = []
            if proj.get("github"):
                url = _normalise_url(proj["github"])
                links.append(f'<a href="{url}" color="{COL_LINK}">GitHub</a>')
            if proj.get("demo"):
                url = _normalise_url(proj["demo"])
                links.append(f'<a href="{url}" color="{COL_LINK}">Demo</a>')
            if links:
                block.append(Paragraph(" | ".join(links), st["small_muted"]))
            block.append(Spacer(1, sp(1) * mm))
            story.append(KeepTogether(block))

    # ── Education ─────────────────────────────────────────────────────
    education = data.get("education", [])
    if education:
        header_block = []
        section_rule(header_block, t["education"])
        story.append(KeepTogether(header_block))
        for edu in education:
            # Extraction provides start_date / end_date strings (either may
            # be absent). Show whichever are present, joined with an en dash.
            date_parts = [str(edu.get(k)).strip() for k in ("start_date", "end_date") if edu.get(k)]
            year_range = " – ".join(dict.fromkeys(date_parts))
            degree_str = edu.get("degree", "")
            field      = edu.get("field", "")
            if field:
                degree_str += f" — {field}" if language == "fr" else f" in {field}"
            if edu.get("gpa"):
                degree_str += f" \u2014 {t['gpa_label']}: {edu['gpa']}"
            block = []
            block.append(Spacer(1, sp(1)))
            block.append(_two_col_table(
                Paragraph(edu.get("institution", ""), st["tbl_left_bold"]),
                Paragraph(year_range,                 st["tbl_right"]),
                content_width,
            ))
            block.append(_two_col_table(
                Paragraph(f"<i>{degree_str}</i>", ParagraphStyle(
                    "edu_degree_left",
                    fontName=FONT_ITALIC, fontSize=st["tbl_right_muted_small"].fontSize,
                    leading=st["tbl_right_muted_small"].leading,
                    textColor=COL_MUTED, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT,
                )),
                Paragraph(edu.get("location", ""), st["tbl_right_muted_small"]),
                content_width,
            ))
            block.append(Spacer(1, sp(1) * mm))
            story.append(KeepTogether(block))

    # ── Certifications ────────────────────────────────────────────────
    certifications = data.get("certifications", [])
    if certifications:
        block = []
        section_rule(block, t["certifications"])
        for cert in certifications:
            block.append(Paragraph(
                f"\u2022 {cert.get('name', '')} \u2014 {cert.get('issuer', '')} ({cert.get('year', '')})",
                st["body"],
            ))
        block.append(Spacer(1, sp(1) * mm))
        story.append(KeepTogether(block))

    # ── Languages ─────────────────────────────────────────────────────
    languages = data.get("languages", [])
    if languages:
        block = []
        section_rule(block, t["languages"])
        parts = [f"{lang['language']} ({lang['level']})" for lang in languages]
        block.append(Paragraph(" | ".join(parts), st["body"]))
        block.append(Spacer(1, sp(1) * mm))
        story.append(KeepTogether(block))

    return story


# ---------------------------------------------------------------------------
# Render helper — build the story into a PDF and report the real page count.
#
# The old `_measure_story_height` summed `flowable.wrap()` which returns 0 for a
# `KeepTogether` until it is split — so it silently under-measured large CVs and
# the "fits one page" decision was wrong. Building the doc and reading
# `doc.page` is the honest measure (and most CVs fit on the first try anyway).
# ---------------------------------------------------------------------------

def _render_story(story: list, margin: float, content_width: float,
                  content_height: float) -> tuple[bytes, int]:
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
    )
    doc.addPageTemplates([PageTemplate(id="p1", frames=[Frame(
        margin, margin, content_width, content_height,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="main",
    )])])
    doc.build(list(story))
    return buf.getvalue(), max(1, getattr(doc, "page", 1))


# ---------------------------------------------------------------------------
# PDFGenerator
# ---------------------------------------------------------------------------

_BASE_BODY_PT = 9.0    # _build_styles "body"/"bullet" fontSize before scaling
_BASE_AUX_PT = 8.5     # _build_styles "contact"/"small_muted" fontSize before scaling
_MARGIN_LADDER_MM = (14.0, 12.0, 10.5)   # generous -> tight, then CV_MIN_MARGIN_MM


def _layout_meta(scale: float, margin_mm: float, fit_one: bool, pages: int) -> dict:
    return {
        "scale": round(scale, 3),
        "margin_mm": round(margin_mm, 1),
        "body_font_pt": round(_BASE_BODY_PT * scale, 1),
        "min_body_font_pt": settings.CV_MIN_BODY_FONT_PT,
        "fit_one_page": fit_one,
        "pages": pages,
    }


class PDFGenerator:
    """
    Renders CV data into an ATS-friendly PDF using ReportLab.

    One-page strategy (constraint #7) — ADAPTIVE, never destructive:
    - every experience, every bullet, every project is always rendered;
    - to fit one page the renderer walks: margins (14 -> CV_MIN_MARGIN_MM mm)
      then a uniform scale on fonts + spacing + leading, down to a floor where
      body text = CV_MIN_BODY_FONT_PT;
    - if it still does not fit, the CV flows onto a 2nd page (nothing dropped)
      and ``cv_data["_layout"]["fit_one_page"]`` is False so the API can say so.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def render_to_bytes(self, cv_data: dict, language: str | None = None) -> bytes:
        """
        Render the CV straight to PDF bytes — no disk I/O. This is the only
        rendering path used by the production API (POST /api/generate-cv),
        consumed via a StreamingResponse.

        `language` ('fr'/'en') controls section titles and inline labels
        only. If omitted, falls back to `cv_data["language"]` (set by
        CVGenerator.generate_cv), then to English.
        """
        return self._render(cv_data, language=language)

    # ------------------------------------------------------------------
    # Core render
    # ------------------------------------------------------------------

    def _min_scale(self) -> float:
        """Lowest uniform scale that keeps body text >= CV_MIN_BODY_FONT_PT and
        the auxiliary labels >= CV_MIN_AUX_FONT_PT."""
        by_body = settings.CV_MIN_BODY_FONT_PT / _BASE_BODY_PT
        by_aux = settings.CV_MIN_AUX_FONT_PT / _BASE_AUX_PT
        return min(1.0, max(by_body, by_aux))

    def _candidate_configs(self) -> list[tuple[float, float]]:
        """(margin_mm, scale) candidates, loosest first. Coarse on purpose —
        each one is a real render."""
        min_scale = self._min_scale()
        margins = list(_MARGIN_LADDER_MM)
        if settings.CV_MIN_MARGIN_MM not in margins:
            margins.append(settings.CV_MIN_MARGIN_MM)
        scales: list[float] = []
        s = 1.0
        while s >= min_scale - 1e-6:
            scales.append(round(s, 3))
            s -= 0.04
        if scales[-1] > min_scale:
            scales.append(round(min_scale, 3))
        # loosest overall first: full margin & scale, then tighten scale, then margin
        out: list[tuple[float, float]] = []
        for m in margins:
            for sc in scales:
                out.append((m, sc))
        return out

    def _render(self, cv_data: dict, language: str | None = None) -> bytes:
        language = language or cv_data.get("language") or "en"
        min_scale = self._min_scale()

        best_floor: tuple[bytes, int, float, float] | None = None  # (pdf, pages, margin_mm, scale)
        for margin_mm, scale in self._candidate_configs():
            margin = margin_mm * mm
            cw = PAGE_WIDTH - 2 * margin
            ch = PAGE_HEIGHT - 2 * margin
            story = _build_story(cv_data, scale=scale, content_width=cw, language=language)
            pdf, pages = _render_story(story, margin, cw, ch)
            if pages == 1:
                cv_data["_layout"] = _layout_meta(scale, margin_mm, True, 1)
                logger.info("[PDFGenerator] one page @ margin=%.1fmm scale=%.0f%% (body=%.1fpt)",
                            margin_mm, scale * 100, _BASE_BODY_PT * scale)
                return pdf
            # remember the tightest (= last) config as the fallback
            best_floor = (pdf, pages, margin_mm, scale)

        # Could not fit one page above the readability floor. Ship the floor
        # config — every experience/bullet/project is there, it just paginates.
        pdf, pages, margin_mm, scale = best_floor  # type: ignore[misc]
        cv_data["_layout"] = _layout_meta(scale, margin_mm, False, pages)
        logger.warning("[PDFGenerator] content cannot fit one page above the %.1fpt readability "
                       "floor — %d pages, NOTHING dropped (fit_one_page=False)",
                       settings.CV_MIN_BODY_FONT_PT, pages)
        return pdf