"""
CV Assistant - CV Generation
Service: PDFLayoutReader

Turns a PDF into layout-aware reading-order text (plus its link
annotations). Layout geometry lives in ``services.cv.column_detector``; this
module is the PyMuPDF adapter around it.

Pipeline per page (spec §1-§6):

    page.get_text("dict")                 -> line bounding boxes (not "text"
                                             content-stream order, not
                                             "blocks" — blocks merge the two
                                             columns of a sidebar template)
      -> column_detector.detect_page_layout()
             * peels a full-width header band (name / title / contact bar)
             * decides SINGLE_COLUMN vs TWO_COLUMNS from the vertical gutter
             * returns a 0..1 reconstruction confidence
      -> _reconstruct_text()
             header (reading order)  +  left column in full  +  right column
             in full  — a section header and its body are never split by
             content from the other column. Fragments on the same visual
             row (designer PDFs emit one line as several positioned runs)
             are re-joined left-to-right.

Detection is PER PAGE: a résumé whose page 1 is two-column and page 2 is
single-column is handled correctly (``layout_types`` is a list).

``low_confidence`` is set when the text cannot be fully trusted: a messy
two-column gutter, a single-column fallback on a still-multi-zone page, or
glyph-corrupted text the PDF itself produced (spaces injected mid-word).
The pipeline turns it into a "layout" validation warning + a confidence cap.

No AI, no section knowledge.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF

from services.cv.column_detector import LayoutType, TextLine, detect_page_layout
from services.cv.link_extractor import extract_pdf_links

logger = logging.getLogger(__name__)

# Re-exported so callers can keep importing LayoutType from here.
__all__ = ["LayoutType", "PDFLayoutReader", "PageLayoutResult", "DocumentLayoutResult"]

# Below this reconstruction confidence a TWO_COLUMNS page is flagged so the
# pipeline can raise a "layout" validation warning (spec §15).
LOW_LAYOUT_CONFIDENCE = 0.6


@dataclass
class PageLayoutResult:
    """Per-page layout detection + reconstructed text (diagnostics / tests)."""
    page_index: int
    layout: LayoutType
    text: str
    confidence: float = 1.0
    has_full_width_header: bool = False


@dataclass
class DocumentLayoutResult:
    """Full-document result: reconstructed text, per-page diagnostics, PDF links."""
    text: str
    pages: list[PageLayoutResult] = field(default_factory=list)
    links: list[Any] = field(default_factory=list)  # list[link_extractor.PdfLink]

    @property
    def any_two_column_page(self) -> bool:
        return any(p.layout == LayoutType.TWO_COLUMNS for p in self.pages)

    @property
    def layout_types(self) -> list[str]:
        return [p.layout.value for p in self.pages]

    @property
    def low_confidence(self) -> bool:
        """
        True if the reconstructed text cannot be fully trusted (spec §15):
        a two-column page with a messy gutter, a single-column fallback on a
        page that still shows strong multi-zone structure, OR text that the
        PDF itself hands over glyph-corrupted (spaces injected mid-word — a
        known failure of some Canva font embeddings, which no reading-order
        logic can repair).
        """
        return (
            any(p.confidence < LOW_LAYOUT_CONFIDENCE for p in self.pages)
            or _looks_glyph_corrupted(self.text)
        )


def _strip_glyph_noise(text: str) -> str:
    """
    Drop icon-font artefacts an icon set (FontAwesome, etc.) leaves in the
    extracted text: C0/C1 control characters, format & private-use
    codepoints. These are never real résumé content but routinely sit in
    front of a phone / e-mail / address on a modern contact bar. Real
    letters, digits, punctuation and whitespace are untouched.
    """
    if not text:
        return text
    out = []
    for ch in text:
        if ch in "\t\n\r":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Co", "Cn", "Cs"):
            out.append(" ")            # keep token spacing
            continue
        out.append(ch)
    # tidy the spaces we may have introduced, but keep a 2-space gap
    # (contact bars use it as a field separator)
    return re.sub(r" {3,}", "  ", "".join(out)).strip()


def _looks_glyph_corrupted(text: str) -> bool:
    """
    Heuristic for text a PDF hands over with spaces injected inside words
    ("un e expertis e avéré e e n front-en d"). Such text has an
    abnormally high share of lone single letters. French legitimately uses
    a few ("à", "y", "a", "l"), so the bar is set well above normal prose.
    """
    tokens = [t for t in text.split() if any(c.isalpha() for c in t)]
    if len(tokens) < 40:
        return False
    singles = sum(1 for t in tokens if len(t) == 1)
    return singles / len(tokens) > 0.12


class PDFLayoutReader:
    """Layout-aware PDF text + link reader."""

    def read(self, path) -> DocumentLayoutResult:
        with fitz.open(path) as doc:
            return self._read_doc(doc)

    def read_bytes(self, content: bytes) -> DocumentLayoutResult:
        with fitz.open(stream=content, filetype="pdf") as doc:
            return self._read_doc(doc)

    # ------------------------------------------------------------------

    def _read_doc(self, doc: "fitz.Document") -> DocumentLayoutResult:
        page_results: list[PageLayoutResult] = []

        for page_index, page in enumerate(doc):
            lines = self._extract_lines(page)
            if not lines:
                page_results.append(
                    PageLayoutResult(page_index, LayoutType.SINGLE_COLUMN, text="")
                )
                continue

            layout = detect_page_layout(
                lines, page_width=page.rect.width, page_height=page.rect.height
            )
            text = self._reconstruct_text(layout)

            page_results.append(
                PageLayoutResult(
                    page_index=page_index,
                    layout=layout.layout,
                    text=text,
                    confidence=layout.confidence,
                    has_full_width_header=layout.has_full_width_header,
                )
            )
            logger.info(
                "[PDFLayoutReader] page=%d | layout=%s | lines=%d | header=%s | confidence=%.2f",
                page_index, layout.layout.value, len(lines),
                layout.has_full_width_header, layout.confidence,
            )

        full_text = "\n".join(p.text for p in page_results if p.text)
        links = extract_pdf_links(doc)
        return DocumentLayoutResult(text=full_text, pages=page_results, links=links)

    # ------------------------------------------------------------------
    # PyMuPDF -> TextLine
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_lines(page: "fitz.Page") -> list[TextLine]:
        """Every visual text line on the page with its bounding box ("dict" mode)."""
        raw = page.get_text("dict")
        out: list[TextLine] = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:  # 0 = text, 1 = image
                continue
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", []))
                text = _strip_glyph_noise(text).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                out.append(TextLine(text=text, x0=x0, y0=y0, x1=x1, y1=y1))
        return out

    # ------------------------------------------------------------------
    # reading-order reconstruction
    # ------------------------------------------------------------------

    @staticmethod
    def _reconstruct_text(layout) -> str:
        parts: list[str] = []
        if layout.header:
            parts.append(PDFLayoutReader._rows_to_text(layout.header))

        if layout.layout == LayoutType.SINGLE_COLUMN:
            body = layout.columns[0] if layout.columns else []
            parts.append(PDFLayoutReader._rows_to_text(body))
        else:  # TWO_COLUMNS: full left column, then full right column
            left, right = layout.columns
            parts.append(PDFLayoutReader._rows_to_text(left))
            parts.append(PDFLayoutReader._rows_to_text(right))

        return "\n".join(p for p in parts if p).strip("\n")

    @staticmethod
    def _rows_to_text(lines: list[TextLine], *, y_tol: float = 3.0) -> str:
        """
        One text block from an already reading-ordered list of lines.

        Same-row fragments are grouped by the horizontal gap between them:

        * a small gap (word spacing) — Canva / designer PDFs emit one line
          as several positioned runs — is closed with a single space, so
          ``"Logiciels" "de" "Productivité" ":" "Microsoft" "Word,"`` stays
          one line and composed names / comma lists survive;
        * a wide gap (a right-aligned tab stop: dates, a duration, a
          location on the far side of a professional single-column CV) is
          treated as a cell boundary — the pieces go on SEPARATE lines, so
          ``"INSEA — …Appliquee"  ····  "2025 – Present"`` becomes two lines
          and the date parser actually sees the dates instead of a date
          glued to the end of an institution name.

        Rows on different y stay on their own lines regardless.
        """
        if not lines:
            return ""
        ordered = sorted(lines, key=lambda l: (round(l.y0, 1), l.x0))
        rows: list[list[TextLine]] = [[ordered[0]]]
        for ln in ordered[1:]:
            if abs(ln.y0 - rows[-1][0].y0) <= y_tol:
                rows[-1].append(ln)
            else:
                rows.append([ln])

        out: list[str] = []
        for row in rows:
            row.sort(key=lambda l: l.x0)
            cell = [row[0]]
            for prev, cur in zip(row, row[1:]):
                line_h = max(prev.y1 - prev.y0, cur.y1 - cur.y0, 1.0)
                gap = cur.x0 - prev.x1
                if gap > max(20.0, 1.8 * line_h):        # right-aligned tab stop
                    out.append(" ".join(l.text for l in cell).strip())
                    cell = [cur]
                else:                                    # word spacing
                    cell.append(cur)
            out.append(" ".join(l.text for l in cell).strip())
        return "\n".join(p for p in out if p)
