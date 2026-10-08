"""
CV Assistant - CV Generation
Service: LocalSectionExtractor

Responsibilities:
- Extract CERTIFICATIONS and LANGUAGES sections WITHOUT any LLM call —
  these are short, list-like and highly regular.
- Also provides ``extract_skills`` (returns a FLAT ``list[str]`` — no
  categories) as the LOCAL FALLBACK for
  ``services.cv.skills_parser.SkillsParser`` (the skills section itself now
  goes through a section-scoped LLM call for line-cut repair; this local
  version is used only when that call is unavailable or unparseable).
- 100% local heuristics: line / comma / bullet splitting, a simple regex
  for certification years.

Languages
---------
Proficiency levels are returned **exactly as written in the CV**
("Langue maternelle", "Courant", "B2", "Native") — extraction must not
translate or normalise them. Level normalisation, if ever wanted, is a
later concern, not this one's.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_BULLET_PREFIX_PATTERN = re.compile(r"^[\-\*•◦▪►‣\u2022\s]+")
_CATEGORY_LABEL_PATTERN = re.compile(r"^([A-Za-zÀ-ÿ0-9 &/\-]{2,40}):\s*(.+)$")
_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# Proficiency vocabulary (EN + FR) — used ONLY to locate where a level
# sits on a line that has no explicit separator ("Anglais Courant").
# The matched text is kept VERBATIM as the level; it is never mapped to a
# canonical value (extraction must not normalise).
_PROFICIENCY_LEVELS = (
    "langue maternelle", "mother tongue", "native speaker", "native language",
    "full professional proficiency", "professional working proficiency",
    "limited working proficiency",
    "native", "fluent", "advanced", "intermediate", "basic", "beginner",
    "conversational", "elementary", "proficient", "bilingual", "fluency",
    "natif", "native", "maternelle", "courant", "avance", "avancé",
    "intermediaire", "intermédiaire", "debutant", "débutant",
    "professionnel", "bilingue", "notions", "scolaire", "lu", "ecrit", "parle",
)
_PROFICIENCY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(set(_PROFICIENCY_LEVELS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


class LocalSectionExtractor:
    """Extracts skills, certifications and languages locally. No LLM calls."""

    # ------------------------------------------------------------------
    # SKILLS
    # ------------------------------------------------------------------

    def extract_skills(self, skills_text: str) -> list[str]:
        """
        Extract a FLAT, de-duplicated skills list from the raw skills
        section text — no categories (used as SkillsParser's local
        fallback).

        Any "Category :" label at the start of a line is stripped; only
        the skill values are kept, in order of appearance. Free "et"/"and"
        conjunctions are split ("PHP et Python" -> "PHP", "Python").
        """
        if not skills_text or not skills_text.strip():
            return []

        out: list[str] = []
        for raw_line in skills_text.split("\n"):
            line = _BULLET_PREFIX_PATTERN.sub("", raw_line).strip()
            if not line:
                continue
            # drop a leading "Label :" / "Label -" if present
            match = _CATEGORY_LABEL_PATTERN.match(line)
            line = match.group(2).strip() if match else line
            for item in self._split_skill_items(line):
                if item and item not in out:
                    out.append(item)

        logger.info("[LocalSectionExtractor] Extracted %d skill(s).", len(out))
        return out

    # ------------------------------------------------------------------
    # CERTIFICATIONS
    # ------------------------------------------------------------------

    def extract_certifications(self, certifications_text: str) -> list[dict[str, Any]]:
        """
        Extract certification entries from raw text.

        Heuristic per non-empty line: "<name>, <issuer> (<year>)" or
        "<name> - <issuer>, <year>" or just "<name>". Year and issuer are
        only populated if confidently found; never invented.

        Returns
        -------
        list[dict]
            Each dict: {"name": str, "issuer": str|None, "year": int|None}.
        """
        if not certifications_text or not certifications_text.strip():
            return []

        results: list[dict[str, Any]] = []
        for raw_line in certifications_text.split("\n"):
            line = _BULLET_PREFIX_PATTERN.sub("", raw_line).strip()
            if not line:
                continue

            year_match = _YEAR_PATTERN.search(line)
            year = int(year_match.group(0)) if year_match else None

            # Strip the year to isolate name/issuer, then trim ONLY a dangling
            # separator or an empty "()" left behind — never a closing paren
            # that belongs to the name ("… (CKA)").
            without_year = _YEAR_PATTERN.sub("", line)
            without_year = re.sub(r"\s*\(\s*\)\s*$", "", without_year)
            without_year = re.sub(r"\s*[,;\-–]\s*$", "", without_year).strip()
            without_year = re.sub(r"^\s*[,;\-–]\s*", "", without_year).strip()

            name = without_year
            issuer = None

            # Only ", <Issuer>" reliably marks an issuer; " - " is almost
            # always part of the certification name ("AWS … - Specialty").
            if ", " in without_year:
                parts = without_year.split(", ", 1)
                name, issuer = parts[0].strip(), parts[1].strip()

            if name:
                results.append({"name": name, "issuer": issuer or None, "year": year})

        logger.info("[LocalSectionExtractor] Extracted %d certification(s).", len(results))
        return results

    # ------------------------------------------------------------------
    # LANGUAGES
    # ------------------------------------------------------------------

    def extract_languages(self, languages_text: str) -> list[dict[str, Any]]:
        """
        Extract language proficiency entries from raw text.

        Per non-empty line / comma-separated item, the name is the text
        before the first separator (``: - – — ( |``) and the level is the
        text after it, **kept verbatim** ("Langue maternelle", "Courant",
        "B2"). When there is no separator, the proficiency vocabulary is
        used only to locate the level inside the line. When no level is
        present at all, ``level`` is ``None`` — never guessed.

        Returns
        -------
        list[dict]
            Each dict: ``{"language": str, "level": str | None}``.
        """
        if not languages_text or not languages_text.strip():
            return []

        results: list[dict[str, Any]] = []

        raw_items: list[str] = []
        for raw_line in languages_text.split("\n"):
            line = _BULLET_PREFIX_PATTERN.sub("", raw_line).strip()
            if not line:
                continue
            raw_items.extend(self._split_items(line, separators=(",", "|", ";")))

        for item in raw_items:
            language, level = self._split_language_item(item)
            language = re.sub(r"\s{2,}", " ", language).strip(" -–—:()")
            if language:
                results.append({"language": language, "level": level or None})

        logger.info("[LocalSectionExtractor] Extracted %d language(s).", len(results))
        return results

    @staticmethod
    def _split_language_item(item: str) -> tuple[str, str | None]:
        """Split one "Language <sep> Level" item, keeping the level verbatim."""
        # 1) explicit separator between name and level
        parts = re.split(r"\s*[:()•|–—-]\s*", item, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip(" ()")

        # 2) no separator — find a proficiency word inside the line and
        #    keep everything from it onwards as the (verbatim) level.
        #    The vocabulary includes accented variants, so we search the
        #    original text directly (position must map back to `item`).
        match = _PROFICIENCY_PATTERN.search(item)
        if match:
            level = item[match.start():].strip(" ()-–—:")
            name = item[: match.start()].strip(" ()-–—:")
            if name:
                return name, level
            return item.strip(), None

        # 3) just a language name, no level stated
        return item.strip(), None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_items(text: str, separators: tuple[str, ...] = (",", "|", "•", ";")) -> list[str]:
        """Split a line into individual items on any of the given separators."""
        pattern = "|".join(re.escape(sep) for sep in separators)
        parts = re.split(pattern, text)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _split_skill_items(text: str) -> list[str]:
        """Split a skills line on ',', ';', '/', '|', '•' AND the words 'et' / 'and'."""
        parts = re.split(r"\s*(?:,|;|/|\||•|&|\bet\b|\band\b)\s*", text, flags=re.IGNORECASE)
        return [p.strip(" .-") for p in parts if p.strip(" .-")]
