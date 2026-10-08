"""
CV Assistant - CV Generation
Service: text_cleaner

Conservative, context-aware cleanup of PDF-extracted résumé text
(spec §6). Two jobs:

- ``join_wrapped_lines`` — rejoin a single piece of information that the
  PDF layout wrapped onto several lines
  ("Gestion du temps et des\\npriorités" -> one line), WITHOUT ever
  merging across a real boundary (a new bullet, a capitalised new item,
  an ALL-CAPS section header, a date line...).
- ``split_list_items`` — turn a short list-like block
  ("Sport\\nMusique\\nVoyage") into individual trimmed items, first
  rejoining any wrapped continuation lines.

Purely local. No LLM. No section knowledge.
"""

from __future__ import annotations

import re

_BULLET = re.compile(r"^\s*[\-\*•◦▪►‣·•]\s+")
_ENDS_OPEN = re.compile(r"[a-zà-ÿ0-9,;:’'\-/&(]\s*$")          # line ends mid-thought
_STARTS_CONTINUATION = re.compile(r"^\s*[a-zà-ÿ0-9(’'&/]")     # next line continues it
_DATE_LINE = re.compile(r"\b(19|20)\d{2}\b|present|présent|depuis|since", re.IGNORECASE)
_CONTACTISH = re.compile(
    r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}|https?://|www\.|linkedin\.com|github\.com"
    r"|\+?\d[\d\s().\-]{6,}\d",
    re.IGNORECASE,
)


def _is_hard_boundary(line: str) -> bool:
    """A line that must never be glued onto the previous one."""
    s = line.strip()
    if not s:
        return True
    if _BULLET.match(line):
        return True
    if s.isupper() and len(s.split()) <= 6:      # ALL-CAPS header / label
        return True
    if s.endswith(":"):                          # "Compétences :" style label
        return True
    if _CONTACTISH.search(s):                    # email / URL / phone line
        return True
    if _DATE_LINE.search(s) and len(s.split()) <= 6:
        return True
    return False


def join_wrapped_lines(text: str) -> str:
    """
    Rejoin lines that are obviously one wrapped sentence / item.

    A line B is appended to the previous line A iff:
      - A ends "open" (lower-case letter, digit, comma, hyphen, "&", "(")
      - B starts as a continuation (lower-case / digit / "(")
      - B is not a hard boundary (bullet, ALL-CAPS header, ":" label, date)
    """
    if not text:
        return text
    out: list[str] = []
    for raw in text.split("\n"):
        if (
            out
            and out[-1].strip()
            and _ENDS_OPEN.search(out[-1])
            and _STARTS_CONTINUATION.match(raw)
            and not _is_hard_boundary(raw)
        ):
            out[-1] = out[-1].rstrip() + " " + raw.strip()
        else:
            out.append(raw)
    return "\n".join(out)


def split_list_items(text: str) -> list[str]:
    """
    Split a list-like section block into individual items.

    Handles newline-, comma-, bullet- and pipe-separated lists, and first
    rejoins wrapped continuation lines so "Gestion du temps et des\\n
    priorités" stays one item. Order is preserved, duplicates dropped,
    nothing is invented.
    """
    if not text or not text.strip():
        return []

    joined = join_wrapped_lines(text)
    items: list[str] = []
    for line in joined.split("\n"):
        line = _BULLET.sub("", line).strip()
        if not line:
            continue
        # a single line may still hold "a, b, c" or "a | b | c"
        parts = re.split(r"\s*[,;|•]\s*|\s{2,}", line) if _has_multi(line) else [line]
        for part in parts:
            part = part.strip(" .-–—:·")
            if part and part not in items:
                items.append(part)
    return items


def _has_multi(line: str) -> bool:
    """True when a line clearly enumerates several short items on one row."""
    if "," in line or ";" in line or "|" in line or "•" in line:
        # avoid splitting a normal sentence that just contains a comma
        return len(line) < 90 or line.count(",") >= 2
    return bool(re.search(r"\S {2,}\S", line)) and len(line) < 90
