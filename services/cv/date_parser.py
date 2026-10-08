"""
CV Assistant - CV Generation
Service: date_parser

Local, dependency-free helpers that turn the raw date wording extracted
from ONE résumé entry into its final form.

Design rules (spec §8, §9, §19 — accuracy over completeness):
- NEVER invent a date. If the entry has no temporal info, the result is
  ``None`` (education) or ``None`` (experience ``period``).
- EDUCATION dates are kept EXACTLY as written. "2022 - Présent" stays
  ``start="2022"``, ``end="Présent"`` — "Présent" is NOT turned into a
  year. "Depuis 2022" is normalised to ``start="2022"``, ``end="Présent"``.
- EXPERIENCE ``period`` is a DURATION:
    * a duration stated in the CV is kept ("Stage de trois mois" -> "3 mois");
    * two calendar dates -> the duration is COMPUTED from them
      ("Jan 2023 - Mar 2024" -> "1 an 3 mois" / "1 year 3 months");
    * an ongoing role ("... - Présent", "Depuis 2022") runs to today.
  The current date is read dynamically (``datetime.now()``) — never
  written into a prompt.

No LLM calls. No third-party libraries.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "janvier": 1, "janv": 1,
    "february": 2, "feb": 2, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2,
    "march": 3, "mar": 3, "mars": 3,
    "april": 4, "apr": 4, "avril": 4, "avr": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juin": 6,
    "july": 7, "jul": 7, "juillet": 7, "juil": 7,
    "august": 8, "aug": 8, "août": 8, "aout": 8,
    "september": 9, "sep": 9, "sept": 9, "septembre": 9,
    "october": 10, "oct": 10, "octobre": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "december": 12, "dec": 12, "décembre": 12, "decembre": 12, "déc": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

_PRESENT_WORDS: tuple[str, ...] = (
    "present", "présent", "presente", "présente",
    "current", "now", "today", "ongoing", "to date",
    "actuel", "actuelle", "aujourd'hui", "aujourd hui",
    "en cours", "a ce jour", "à ce jour", "maintenant",
)
_SINCE_WORDS: tuple[str, ...] = ("depuis", "since", "from")

_NUMBER_WORDS: dict[str, int] = {
    "un": 1, "une": 1, "one": 1, "deux": 2, "two": 2, "trois": 3, "three": 3,
    "quatre": 4, "four": 4, "cinq": 5, "five": 5, "six": 6, "sept": 7, "seven": 7,
    "huit": 8, "eight": 8, "neuf": 9, "nine": 9, "dix": 10, "ten": 10,
    "onze": 11, "eleven": 11, "douze": 12, "twelve": 12,
    "dix-huit": 18, "eighteen": 18, "vingt-quatre": 24,
}
_DURATION_UNITS = r"mois|months?|semaines?|weeks?|ans?|annees?|années?|years?|jours?|days?"
_DURATION_PATTERN = re.compile(
    rf"\b(\d{{1,3}}|{'|'.join(sorted(_NUMBER_WORDS, key=len, reverse=True))})[\s\-]+({_DURATION_UNITS})\b",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{4}})\b", re.IGNORECASE)
_NUM_MONTH_YEAR = re.compile(r"\b(\d{1,2})[/.\-](\d{4})\b")
_BARE_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_RANGE_SEP = re.compile(r"\s*(?:-|–|—|to|à|au|\bjusqu['’]?\s*(?:au|à)?\b)\s*", re.IGNORECASE)


def _today() -> date:
    return datetime.now().date()


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------

def _is_present_token(text: str | None) -> bool:
    if not text:
        return False
    low = text.strip().lower()
    return any(w in low for w in _PRESENT_WORDS)


def _strip_since_prefix(text: str) -> str | None:
    low = text.strip().lower()
    for w in _SINCE_WORDS:
        if low.startswith(w + " "):
            return text.strip()[len(w):].strip(" -–—:")
    return None


def _parse_point(token: str) -> tuple[int, int] | None:
    """
    Parse one date point -> ``(year, month)``. ``month == 0`` marks a bare
    year (no month written). ``None`` when nothing parses.
    """
    token = token.strip()
    if not token:
        return None
    m = _MONTH_YEAR.search(token)
    if m:
        return int(m.group(2)), _MONTHS[m.group(1).lower()]
    m = _NUM_MONTH_YEAR.search(token)
    if m:
        month = int(m.group(1))
        return int(m.group(2)), (month if 1 <= month <= 12 else 0)
    m = _BARE_YEAR.search(token)
    if m:
        return int(m.group(0)), 0
    return None


def _fmt_duration(total_months: int, language: str) -> str | None:
    if total_months <= 0:
        return None
    fr = language == "fr"
    years, months = divmod(total_months, 12)
    if years == 0:
        return f"{months} mois" if fr else f"{months} month{'s' if months > 1 else ''}"
    y = f"{years} an{'s' if years > 1 else ''}" if fr else f"{years} year{'s' if years > 1 else ''}"
    if months == 0:
        return y
    mo = f"{months} mois" if fr else f"{months} month{'s' if months > 1 else ''}"
    return f"{y} {mo}"


# ---------------------------------------------------------------------------
# Public API — EDUCATION
# ---------------------------------------------------------------------------

def resolve_education_dates(
    start_raw: str | None,
    end_raw: str | None,
) -> tuple[str | None, str | None]:
    """
    Final ``(start_date, end_date)`` for ONE education entry — kept exactly
    as written (spec §8):

    - "2022 - 2024"   -> ("2022", "2024")
    - "2022 - Présent"-> ("2022", "Présent")   ("Présent" is NOT a year)
    - "Depuis 2022"   -> ("2022", "Présent")
    - no date         -> (None, None)
    """
    start = (start_raw or "").strip() or None
    end = (end_raw or "").strip() or None

    if start:
        since = _strip_since_prefix(start)
        if since is not None:
            start = since or None
            if not end:
                end = "Présent"

    if end and _is_present_token(end):
        end = "Présent" if "present" not in end.lower() else "Present"

    return start, end


# ---------------------------------------------------------------------------
# Public API — EXPERIENCE
# ---------------------------------------------------------------------------

def compute_experience_period(period_raw: str | None, language: str = "fr") -> str | None:
    """
    Turn the raw ``period`` of ONE experience entry into a DURATION
    (spec §9):

    - a duration written in the CV is kept ("Stage de trois mois" -> "3 mois")
    - two calendar dates          -> duration computed ("1 an 3 mois")
    - "Depuis 2022" / "... - Present" -> computed up to today
    - a single lone date          -> returned verbatim
    - nothing temporal            -> ``None`` (never invented)
    """
    if not period_raw or not period_raw.strip():
        return None

    text = re.sub(r"\s+", " ", period_raw.strip())

    # Is there a real "<start> <sep> <end>" range with a date on each side?
    range_parts: list[str] | None = None
    parts = _RANGE_SEP.split(text, maxsplit=1)
    if len(parts) == 2:
        left_has_date = _parse_point(parts[0]) is not None
        right_ok = _parse_point(parts[1]) is not None or _is_present_token(parts[1])
        if left_has_date and right_ok:
            range_parts = parts

    # 1. a duration written in the CV (and NOT a two-date range) is kept verbatim
    dur = _DURATION_PATTERN.search(text)
    if dur and range_parts is None:
        qty = _NUMBER_WORDS.get(dur.group(1).lower())
        if qty is None:
            try:
                qty = int(dur.group(1))
            except ValueError:
                qty = None
        return f"{qty if qty is not None else dur.group(1)} {dur.group(2).lower()}"

    # 2. "Depuis <date>" / "Since <date>" -> computed up to today
    since = _strip_since_prefix(text)
    if since is not None and range_parts is None:
        p = _parse_point(since)
        return _months_between(p, (_today().year, _today().month), language) if p else None

    # 3. an explicit two-date range -> compute the duration
    if range_parts is not None:
        start_p = _parse_point(range_parts[0])
        end_p = (
            (_today().year, _today().month)
            if _is_present_token(range_parts[1])
            else _parse_point(range_parts[1])
        )
        if start_p and end_p:
            return _months_between(start_p, end_p, language)

    # 4. a single lone date token -> keep it verbatim (it IS in the CV)
    if _parse_point(text) and len(text.split()) <= 3:
        return text

    # 5. nothing usable
    return None


def _months_between(
    start: tuple[int, int],
    end: tuple[int, int],
    language: str,
) -> str | None:
    (sy, sm), (ey, em) = start, end
    if sm == 0 or em == 0:
        # year-only range: "2022 - 2024" reads as ~2 years, not 25 months
        years = max(1, ey - sy)
        fr = language == "fr"
        return f"{years} an{'s' if years > 1 else ''}" if fr else f"{years} year{'s' if years > 1 else ''}"
    total = (ey - sy) * 12 + (em - sm) + 1  # inclusive of both endpoints
    return _fmt_duration(total, language)
