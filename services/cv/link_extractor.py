"""
CV Assistant - CV Generation
Service: link_extractor

Extracts and classifies URLs from a resume — from BOTH the PDF's link
annotations and the plain text.

Why the annotations matter (spec §4 / §23)
------------------------------------------
Modern designer / Canva résumés very often show only the word "LinkedIn"
(or a small icon) as visible text, with the real profile URL hidden in a
PDF *link annotation*. Reading the extracted text alone therefore misses
the LinkedIn / GitHub / portfolio URLs entirely. PyMuPDF exposes those
annotations via ``page.get_links()`` — this module reads them.

Priority (spec §24): a URL found in a PDF annotation is authoritative. It
is returned exactly as stored in the PDF — never reformatted, never
"cleaned up", never reconstructed from a guessed username.

No LLM calls.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PdfLink:
    """One URL found in a PDF link annotation."""
    url: str
    text: str = ""          # visible text under the annotation rectangle
    page: int = 1           # 1-indexed


@dataclass
class ClassifiedLinks:
    """URLs bucketed by kind. Each single-value field holds the FIRST match."""
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    email: str | None = None
    others: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkedin": self.linkedin,
            "github": self.github,
            "portfolio": self.portfolio,
            "email": self.email,
            "others": list(self.others),
        }


# ---------------------------------------------------------------------------
# URL recognition
# ---------------------------------------------------------------------------

# Any URL written in plain text, with or without a scheme. A URL that is
# immediately preceded by "@" is the domain part of an email address, not
# a link — the negative look-behind keeps "gmail.com" out of "x@gmail.com".
_URL_IN_TEXT = re.compile(
    r"(?<![@\w])"
    r"(?:(?:https?://|www\.)[^\s<>()\[\]]+"
    r"|(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|me|app|fr|co|xyz|tech|pro|info|site|page|link)"
    r"(?:/[^\s<>()\[\]]*)?)",
    re.IGNORECASE,
)

_LINKEDIN_RE = re.compile(r"(?:^|//|\.)linkedin\.com/(?:in|pub|profile)/", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:^|//|\.)github\.com/[^/\s]+", re.IGNORECASE)
_GITHUB_PAGES_RE = re.compile(r"\.github\.io", re.IGNORECASE)
_SOCIAL_NOISE_RE = re.compile(
    r"(?:facebook|instagram|twitter|x\.com|youtube|tiktok|indeed|glassdoor|"
    r"stackoverflow\.com/users|wa\.me|t\.me|discord)",
    re.IGNORECASE,
)


def _add_scheme(url: str) -> str:
    """Ensure an ``https://`` scheme, otherwise leave the URL byte-for-byte identical."""
    url = url.strip().strip(".,;)")
    if url.lower().startswith("mailto:"):
        return url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "https://" + url.lstrip("/")
    return url


def _looks_like_email(value: str) -> str | None:
    if value.lower().startswith("mailto:"):
        value = value[7:]
    m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", value)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# PDF annotation extraction
# ---------------------------------------------------------------------------

def extract_pdf_links(doc: "Any") -> list[PdfLink]:
    """
    Read every URI link annotation of an already-open PyMuPDF document.

    Returns a list of :class:`PdfLink`. The ``url`` is exactly what the PDF
    stores; ``text`` is the visible text sitting under the annotation
    rectangle (often just "LinkedIn"), useful for logging / debugging only.
    """
    links: list[PdfLink] = []
    try:
        import fitz  # PyMuPDF — already a project dependency
    except Exception:  # pragma: no cover - fitz is always installed in prod
        return links

    for page_index, page in enumerate(doc):
        try:
            raw_links = page.get_links()
        except Exception:  # pragma: no cover
            continue
        for link in raw_links:
            uri = (link.get("uri") or "").strip()
            if not uri:
                continue
            text = ""
            rect = link.get("from")
            if rect is not None:
                try:
                    text = page.get_textbox(rect).strip()
                except Exception:  # pragma: no cover
                    text = ""
            links.append(PdfLink(url=uri, text=text, page=page_index + 1))

    if links:
        logger.info("[link_extractor] %d PDF link annotation(s) found", len(links))
    return links


def links_from_text(text: str) -> list[str]:
    """Return every URL written in the plain text (with or without a scheme)."""
    if not text:
        return []
    return [m.group(0) for m in _URL_IN_TEXT.finditer(text)]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_links(
    pdf_links: list[PdfLink] | None = None,
    text_urls: list[str] | None = None,
) -> ClassifiedLinks:
    """
    Bucket URLs into linkedin / github / portfolio / email / others.

    PDF-annotation URLs are considered first (spec §24) and win over any
    URL merely written in the text. Every value is returned exactly as
    found, only prefixed with ``https://`` when a scheme is missing.
    """
    result = ClassifiedLinks()
    seen: set[str] = set()

    # PDF annotations first (spec §24) so they win any single-value slot.
    ordered = [pl.url for pl in (pdf_links or [])] + list(text_urls or [])

    for raw in ordered:
        raw = (raw or "").strip()
        if not raw:
            continue

        email = _looks_like_email(raw)
        if email:
            if result.email is None:
                result.email = email
            continue

        url = _add_scheme(raw)
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)

        # Fill the matching single-value slot; a URL that would overflow an
        # already-filled slot is NEVER dropped — it goes to ``others`` (a
        # second GitHub repo, a project demo host, etc. — needed to attach
        # links to individual projects downstream).
        if _LINKEDIN_RE.search(url):
            if result.linkedin is None:
                result.linkedin = url
            else:
                result.others.append(url)
        elif _GITHUB_RE.search(url) and "github.io" not in url.lower():
            if result.github is None:
                result.github = url
            else:
                result.others.append(url)
        elif _SOCIAL_NOISE_RE.search(url):
            result.others.append(url)
        elif _GITHUB_PAGES_RE.search(url) or _is_plausible_portfolio(url):
            if result.portfolio is None:
                result.portfolio = url
            else:
                result.others.append(url)
        else:
            result.others.append(url)

    logger.info(
        "[link_extractor] classified | linkedin=%s github=%s portfolio=%s email=%s others=%d",
        bool(result.linkedin), bool(result.github), bool(result.portfolio),
        bool(result.email), len(result.others),
    )
    return result


# Free e-mail / generic hosts that are never a personal portfolio.
_NON_PORTFOLIO_HOSTS = (
    "gmail.", "googlemail.", "outlook.", "hotmail.", "live.", "yahoo.",
    "icloud.", "protonmail.", "proton.me", "mail.", "yandex.", "gmx.",
    "example.com", "email.com", "orange.fr", "free.fr", "laposte.net",
)


def _is_plausible_portfolio(url: str) -> bool:
    low = url.lower()
    if any(b in low for b in ("linkedin.com", "github.com", "mailto:")):
        return False
    if any(h in low for h in _NON_PORTFOLIO_HOSTS):
        return False
    if _SOCIAL_NOISE_RE.search(low):
        return False
    return bool(re.search(r"https?://[^\s]+\.[a-z]{2,}", low))
