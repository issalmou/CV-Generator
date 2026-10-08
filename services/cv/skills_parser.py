"""
CV Assistant - CV Generation
Service: SkillsParser

Turns the raw text of the SKILLS section (and ONLY that section) into a
**flat list of individual skills**, using a single focused LLM call via
``services.gemini_client.call_gemini``.

No categories (spec: extraction produces ``skills: list[str]``, never
``list[{category, skills}]``). Any category labels the CV uses
("Langages de programmation :", "Databases:", ...) are treated as noise
and dropped — only the skill values are kept, in their order of
appearance.

Why an LLM call for skills (the other list-like sections stay local)
-------------------------------------------------------------------
The skills block is the one most damaged by PDF extraction: designer
templates routinely split a single technology name across a line break
("scikit-\\nlearn", "Power\\nBI", "React\\nJS"). A pure regex cannot tell
"Open\\nCV" -> "OpenCV" from "Power\\nBI" -> "Power BI". A focused,
section-scoped prompt can, while still being forbidden from inventing,
merging or renaming anything.

If the block is empty, or the LLM response cannot be parsed, this falls
back to ``LocalSectionExtractor.extract_skills`` so the pipeline never
loses a working local result.
"""

from __future__ import annotations

import json
import logging
import re

from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.cv.local_section_extractor import LocalSectionExtractor
from services.parser_common import (
    ANTI_HALLUCINATION_RULES,
    TECH_LIST_RULES,
    StructuredOutputError,
    language_directive,
    request_structured_json,
    strip_code_fences,
)

logger = logging.getLogger(__name__)

# Splits an item the model may still have returned as one string
# ("PHP et Python" / "Oracle and MongoDB" / "a, b; c").
_ITEM_SPLIT = re.compile(r"\s*(?:,|;|/|\||•|\bet\b|\band\b|&)\s*", re.IGNORECASE)


class SkillsParser:
    """Turns the SKILLS section into a flat, de-duplicated ``list[str]``."""

    REQUEST_TYPE = "resume_parse_skills"

    def __init__(self) -> None:
        self._local = LocalSectionExtractor()

    def parse(self, skills_text: str, *, language: str = "en") -> list[str]:
        """
        Parse the raw SKILLS section text into a flat ``list[str]``.

        Empty section -> ``[]``. LLM parse failure -> local regex fallback.
        Order of appearance is preserved; exact duplicates are dropped.
        """
        if not skills_text or not skills_text.strip():
            logger.info("[SkillsParser] Empty section - skipping LLM call.")
            return []

        prompt = self._build_prompt(skills_text, language)
        try:
            model = request_structured_json(
                prompt, request_type=self.REQUEST_TYPE,
                validator=validator_for(self.REQUEST_TYPE),
                json_schema=json_schema_for(self.REQUEST_TYPE),
            )
            parsed = self._postprocess(model.skills)
        except StructuredOutputError as exc:
            parsed = self._postprocess(self._parse_response(exc.raw) or [])
            if not parsed:
                logger.warning("[SkillsParser] structured validation failed (%s) — "
                               "falling back to local regex extraction (safe: same text).", exc)
                return self._local.extract_skills(skills_text)

        logger.info("[SkillsParser] Parsed %d skill(s).", len(parsed))
        return parsed

    @staticmethod
    def _postprocess(items: list[str]) -> list[str]:
        """Split leftover compound strings and de-duplicate, order-preserving."""
        out: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            for piece in _ITEM_SPLIT.split(item):
                piece = piece.strip(" .•-")
                if piece and piece not in out:
                    out.append(piece)
        return out

    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(section_text: str, language: str) -> str:
        return f"""You are a precise resume-parsing engine. You are given ONLY the
SKILLS section of a resume. Extract ONLY the skills / technologies in it.

{ANTI_HALLUCINATION_RULES}
{language_directive(language)}
{TECH_LIST_RULES}

OUTPUT FORMAT — READ CAREFULLY:
- Return ONE FLAT LIST of strings. NO categories, NO grouping.
- If the CV groups skills under labels ("Langages :", "Frameworks :",
  "Databases:", "Outils :", ...), IGNORE the labels and merge every skill
  into the single list.
- One skill per string. Never merge two skills into one string
  ("PHP et Python" -> "PHP", "Python"; "Oracle and MongoDB" -> "Oracle",
  "MongoDB").
- Keep each name EXACTLY as written ("Node.js", "React JS", "PowerBI",
  "scikit-learn", "OpenCv"). Do not rename, do not correct spelling.
- Rejoin a name the PDF split across a line break ONLY when the text makes
  it unambiguous ("scikit-\\nlearn" -> "scikit-learn").
- Keep the order in which the skills appear in the text.
- Include ONLY skills explicitly present. Never add, deduce or "fix" one.

SKILLS SECTION TEXT:
\"\"\"
{section_text}
\"\"\"

Return ONLY this JSON object and nothing else:
{{
  "skills": ["skill1", "skill2", "skill3"]
}}"""

    @staticmethod
    def _parse_response(raw: str) -> list[str] | None:
        """Return the flat, de-duplicated skill list, or None if unusable."""
        try:
            data = json.loads(strip_code_fences(raw))
        except json.JSONDecodeError:
            return None

        items = data.get("skills") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None

        out: list[str] = []
        for item in items:
            # tolerate the model still returning {"category", "skills"} objects
            if isinstance(item, dict):
                sub = item.get("skills")
                if isinstance(sub, list):
                    items.extend(sub)  # flatten in place
                continue
            if not isinstance(item, str):
                continue
            for piece in _ITEM_SPLIT.split(item):
                piece = piece.strip(" .•-")
                if piece and piece not in out:
                    out.append(piece)

        return out or None
