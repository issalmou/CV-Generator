"""
CV Assistant - CV Generation
Service: ExperienceParser

Turns the raw text of the EXPERIENCE section (and ONLY that section) into
a list of structured experience entries, using a single focused LLM call
via ``services.gemini_client.call_gemini``.

It never receives the full resume, never receives contact / skills /
education text, and never fabricates a value — see
``services.parser_common.ANTI_HALLUCINATION_RULES``.

Dates
-----
Each role's date range is returned as one ``period`` string, copied from
that role's own text. "Depuis <date>" / "Since <date>" is normalised to
"<date> - Présent" / "<date> - Present" afterwards in Python by
``services.cv.date_parser.compute_experience_period`` — never in the prompt.

Output entry shape (every key always present; missing values are
``None`` / ``[]``):

    {
      "company":      str | None,
      "position":     str | None,
      "period":       str | None,   # e.g. "Janvier 2024 - Juin 2024"
      "location":     str | None,
      "description":  str | None,
      "achievements": list[str],
      "technologies": list[str],    # individual items, never grouped
    }
"""

from __future__ import annotations

import logging
from typing import Any

from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini
from services.parser_common import (
    ANTI_HALLUCINATION_RULES,
    TECH_LIST_RULES,
    StructuredOutputError,
    language_directive,
    parse_list_response,
    request_structured_json,
)

logger = logging.getLogger(__name__)


class ExperienceParser:
    """Structures the EXPERIENCE section into JSON. One LLM call per parse."""

    REQUEST_TYPE = "resume_parse_experience"
    EXPECTED_KEY = "experience"

    def parse(self, experience_text: str, *, language: str = "en") -> list[dict[str, Any]]:
        """
        Parse the raw EXPERIENCE section text into structured entries.

        Parameters
        ----------
        experience_text:
            Raw text of the "experience" section only (output of
            ``SectionSplitter``). Never the full resume.
        language:
            ``"fr"`` or ``"en"`` — controls only the language of any
            free-text the model normalises (description / achievements).
            Proper nouns, dates and technology names are never translated.

        Returns
        -------
        list[dict]
            One dict per role. Empty list if the section is empty or the
            response could not be parsed. Never fabricated.
        """
        if not experience_text or not experience_text.strip():
            logger.info("[ExperienceParser] Empty section - skipping LLM call.")
            return []

        prompt = self._build_prompt(experience_text, language)
        try:
            model = request_structured_json(
                prompt, request_type=self.REQUEST_TYPE,
                validator=validator_for(self.REQUEST_TYPE),
                json_schema=json_schema_for(self.REQUEST_TYPE),
            )
            entries = [e.model_dump() for e in model.experience]
        except StructuredOutputError as exc:
            logger.warning("[ExperienceParser] structured validation failed (%s) — "
                           "lenient salvage of the model's own output.", exc)
            entries = parse_list_response(exc.raw, expected_key=self.EXPECTED_KEY, logger=logger)
        logger.info("[ExperienceParser] Parsed %d experience entr%s.",
                    len(entries), "y" if len(entries) == 1 else "ies")
        return entries

    @staticmethod
    def _build_prompt(section_text: str, language: str) -> str:
        return f"""You are a precise resume-parsing engine. You are given ONLY the
EXPERIENCE section of a resume. Extract ONLY the professional experiences
in it.

{ANTI_HALLUCINATION_RULES}
{language_directive(language)}
{TECH_LIST_RULES}

PERIOD - READ CAREFULLY:
- "period" is the time span for THIS role, taken ONLY from this role's
  own lines. NEVER borrow a date from another role, from education, or
  from anywhere else in the resume.
- If CALENDAR DATES are given, copy them verbatim:
  "Janvier 2024 - Juin 2024", "2023 - 2024", "Jan 2024 - Present",
  "Depuis janvier 2024". Keep "Present" / "Présent" / "depuis" as-is.
- If NO calendar dates are given but a DURATION is stated, keep the
  duration as the period:
  "Stage de trois mois" -> "3 mois"
  "Stage de deux mois"  -> "2 mois"
  "Internship for 6 months" -> "6 months"
  "1 year contract" -> "1 year"
- NEVER turn a duration into invented calendar dates
  ("Stage de 3 mois" must NOT become "Janvier 2024 - Mars 2024").
- If there is neither a date nor a duration, period = null.

LOCATION:
- Only set "location" if a place is written as the job location.
- Do NOT infer it from the company name. "Polyclinique Internationale de
  Laâyoune" does NOT make location = "Laâyoune".

Split the text into one entry per role/position. Put quantified or
outcome bullets into "achievements" and a short free-text summary (if
any) into "description".

EXPERIENCE SECTION TEXT:
\"\"\"
{section_text}
\"\"\"

Return ONLY this JSON object:
{{
  "experience": [
    {{
      "company": "string or null",
      "position": "string or null",
      "period": "string or null (exact date range as written)",
      "location": "string or null",
      "description": "string or null",
      "achievements": ["string", "..."],
      "technologies": ["string", "..."]
    }}
  ]
}}"""
