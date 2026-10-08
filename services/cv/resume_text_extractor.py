"""
CV Assistant - CV Generation
Service: ResumeTextExtractor

Responsibilities
----------------
- Extract raw text (+ link annotations) from an uploaded résumé file
  (PDF, DOCX, TXT) held in memory.
- No interpretation, no structuring, no AI of any kind — this is the pure
  I/O layer, the first stage of :class:`services.cv.resume_parser_pipeline.ResumeParserPipeline`.

This module purposefully knows nothing about CV sections, contact info or
dates. It only answers: *"What is the raw text inside this file, and which
URLs does it hyperlink?"*.

PDF layout awareness
--------------------
PDF extraction is delegated to
:class:`services.cv.pdf_layout_reader.PDFLayoutReader`, which reads text with
coordinate-aware line extraction and reconstructs reading order from the
detected per-page layout (single- vs two-column, full-width header). This
fixes the historical failure mode where PyMuPDF's naive ``get_text("text")``
followed the PDF's content-stream order rather than the visual reading
order — scrambling section headers and bodies together on two-column,
Canva, Europass and sidebar templates.

DOCX
----
DOCX body content is read **in document order** — paragraphs and tables
interleaved as they appear in ``document.element.body`` — so a skills
table wedged between two prose paragraphs keeps its position. Table rows
are flattened cell-by-cell (``" | "`` separated), which is the correct
reading order for tabular content. External hyperlink targets are read
from the relationship table so a LinkedIn / GitHub URL that is only a
clickable hyperlink is still recovered.

TXT files have no layout concept and are decoded as UTF-8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as _DocxTable
from docx.text.paragraph import Paragraph as _DocxParagraph

from services.cv.link_extractor import PdfLink
from services.cv.pdf_layout_reader import PDFLayoutReader

logger = logging.getLogger(__name__)


@dataclass
class DocumentExtraction:
    """Raw text of a résumé file, its link annotations, and PDF layout diagnostics."""

    text: str
    links: list[PdfLink] = field(default_factory=list)
    layout_types: list[str] = field(default_factory=list)  # per page: "SINGLE_COLUMN" / "TWO_COLUMNS"
    low_layout_confidence: bool = False                    # a 2-col page rebuilt with low confidence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsupportedFileTypeError(ValueError):
    """Raised when the file extension is not one of: .pdf, .docx, .txt."""


class TextExtractionError(RuntimeError):
    """Raised when a file exists but cannot be opened or parsed at all."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ResumeTextExtractor:
    """Extracts raw text + hyperlinks from PDF, DOCX or TXT résumé bytes. No AI involved."""

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt"})

    def __init__(self) -> None:
        self._pdf_layout_reader = PDFLayoutReader()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract_document_from_bytes(self, content: bytes, filename: str) -> DocumentExtraction:
        """
        Extract raw text and link annotations from in-memory résumé bytes.

        Parameters
        ----------
        content:
            Raw file bytes (an uploaded file).
        filename:
            Original filename — used only to determine the extension.

        Returns
        -------
        DocumentExtraction
            ``text`` (normalised, layout-reconstructed for PDF), ``links``
            (PDF ``/Link`` annotations or DOCX hyperlink relationships),
            ``layout_types`` and ``low_layout_confidence`` (PDF only).

        Raises
        ------
        UnsupportedFileTypeError
            If the extension is not ``.pdf`` / ``.docx`` / ``.txt``.
        TextExtractionError
            If the file cannot be parsed.
        """
        ext = self._checked_extension(filename)
        logger.info(
            "[ResumeTextExtractor] Extracting | filename=%s | ext=%s | size=%d",
            filename, ext, len(content),
        )

        links: list[PdfLink] = []
        layout_types: list[str] = []
        low_conf = False

        if ext == ".pdf":
            try:
                result = self._pdf_layout_reader.read_bytes(content)
            except Exception as exc:
                raise TextExtractionError(f"Failed to extract PDF text: {exc}") from exc
            text = result.text
            links = list(result.links)
            layout_types = result.layout_types
            low_conf = result.low_confidence
            if result.any_two_column_page:
                logger.info(
                    "[ResumeTextExtractor] Two-column layout detected | pages_two_col=%d",
                    sum(1 for p in result.pages if p.layout.value == "TWO_COLUMNS"),
                )

        elif ext == ".docx":
            try:
                document = Document(BytesIO(content))
            except Exception as exc:
                raise TextExtractionError(f"Failed to open DOCX: {exc}") from exc
            text = self._read_docx_document(document)
            links = self._docx_hyperlinks(document)

        else:  # .txt
            text = self._decode_txt(content)

        text = self._normalize(text)
        logger.info(
            "[ResumeTextExtractor] Extraction complete | filename=%s | chars=%d | links=%d",
            filename, len(text), len(links),
        )
        return DocumentExtraction(
            text=text, links=links,
            layout_types=layout_types, low_layout_confidence=low_conf,
        )

    # ------------------------------------------------------------------
    # DOCX helpers
    # ------------------------------------------------------------------

    @classmethod
    def _read_docx_document(cls, document) -> str:
        """
        Flatten a DOCX body to text, walking ``document.element.body`` in
        order so paragraphs and tables keep their true document position.
        Table rows become ``"cell | cell | cell"`` lines.
        """
        lines: list[str] = []
        parent = document.element.body
        for child in parent.iterchildren():
            if isinstance(child, CT_P):
                text = _DocxParagraph(child, document).text.strip()
                if text:
                    lines.append(text)
            elif isinstance(child, CT_Tbl):
                for row in _DocxTable(child, document).rows:
                    cells = [c.text.strip() for c in row.cells]
                    cells = [c for c in cells if c]
                    if cells:
                        lines.append(" | ".join(cells))
        return "\n".join(lines)

    @staticmethod
    def _docx_hyperlinks(document) -> list[PdfLink]:
        """Read external hyperlink targets from a DOCX's relationship table."""
        out: list[PdfLink] = []
        try:
            for rel in document.part.rels.values():
                if "hyperlink" in rel.reltype and getattr(rel, "is_external", False):
                    out.append(PdfLink(url=str(rel.target_ref), text="", page=1))
        except Exception:  # pragma: no cover - defensive
            pass
        return out

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    @classmethod
    def _checked_extension(cls, filename: str) -> str:
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if ext not in cls.SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError(
                f"Unsupported file type '{ext or filename}'. "
                f"Supported: {sorted(cls.SUPPORTED_EXTENSIONS)}"
            )
        return ext

    @staticmethod
    def _decode_txt(content: bytes) -> str:
        try:
            return content.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - decode with errors="replace" rarely raises
            raise TextExtractionError(f"Failed to decode TXT bytes: {exc}") from exc

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Light, near-lossless cleanup: normalise line endings, strip trailing
        whitespace per line, collapse 3+ blank lines to 2 (keeps section
        gaps), strip leading/trailing blank lines. Never removes or
        reinterprets actual content.
        """
        if not text:
            return ""

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned: list[str] = []
        blank_run = 0
        for line in text.split("\n"):
            line = line.rstrip()
            if line == "":
                blank_run += 1
                if blank_run <= 2:
                    cleaned.append(line)
            else:
                blank_run = 0
                cleaned.append(line)
        return "\n".join(cleaned).strip("\n")
