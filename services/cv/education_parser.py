"""
CV Assistant - CV Generation
Service: EducationParser

Turns the raw text of the EDUCATION section (and ONLY that section) into a
list of structured education entries, using a single focused LLM call via
``services.gemini_client.call_gemini``.

Never receives the full resume, never fabricates a value — see
``services.parser_common.ANTI_HALLUCINATION_RULES``.

Degree
------
``degree`` is copied **verbatim** from the entry's text — same language,
same abbreviation, same parentheticals ("M.Sc.", "B.Sc. (Excellence)",
"Specialized Technician Diploma" are never normalised to "Master" /
"Bachelor" or translated). The parser only separates the degree from the
``field`` that follows it.

Dates
-----
The model returns ``start_date`` / ``end_date`` **exactly as written in
that entry's own text**. The "open-ended entry -> current year" rule
(``"2022 - Présent"`` -> ``end_date = <this year>``) is applied
afterwards, in Python, by
``services.cv.date_parser.resolve_education_dates`` — the current year is
NEVER written into the prompt.

Output entry shape (every key always present; missing values are ``None``):

    {
      "institution": str | None,
      "degree":      str | None,
      "field":       str | None,
      "start_date":  str | None,   # exact text, e.g. "2022", "Septembre 2022"
      "end_date":    str | None,   # exact text, e.g. "2024", "Présent"
      "gpa":         str | None,   # exact text, e.g. "3.8/4.0", "Mention Bien"
      "location":    str | None,
    }
"""

from __future__ import annotations

import logging
from typing import Any

from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    ANTI_HALLUCINATION_RULES,
    StructuredOutputError,
    language_directive,
    parse_list_response,
    request_structured_json,
)

logger = logging.getLogger(__name__)


class EducationParser:
    """Structures the EDUCATION section into JSON. One LLM call per parse."""

    REQUEST_TYPE = "resume_parse_education"
    EXPECTED_KEY = "education"

    def parse(self, education_text: str, *, language: str = "en") -> list[dict[str, Any]]:
        """
        Parse the raw EDUCATION section text into structured entries.

        Parameters
        ----------
        education_text:
            Raw text of the "education" section only (output of
            ``SectionSplitter``). Never the full resume.
        language:
            ``"fr"`` or ``"en"`` — free-text only; proper nouns, degree
            names and dates are never translated.

        Returns
        -------
        list[dict]
            One dict per degree / programme. Empty list if the section is
            empty or the response could not be parsed. Never fabricated.
        """
        if not education_text or not education_text.strip():
            logger.info("[EducationParser] Empty section - skipping LLM call.")
            return []

        prompt = self._build_prompt(education_text, language)
        try:
            model = request_structured_json(
                prompt, request_type=self.REQUEST_TYPE,
                validator=validator_for(self.REQUEST_TYPE),
                json_schema=json_schema_for(self.REQUEST_TYPE),
            )
            entries = [e.model_dump() for e in model.education]
        except StructuredOutputError as exc:
            logger.warning("[EducationParser] structured validation failed (%s) — "
                           "lenient salvage of the model's own output.", exc)
            entries = parse_list_response(exc.raw, expected_key=self.EXPECTED_KEY, logger=logger)
        logger.info("[EducationParser] Parsed %d education entr%s.",
                    len(entries), "y" if len(entries) == 1 else "ies")
        return entries

    @staticmethod
    def _build_prompt(section_text: str, language: str) -> str:
        return f"""You are a precise resume-parsing engine. You are given ONLY the
EDUCATION section of a resume. Extract ONLY the education entries in it.

{ANTI_HALLUCINATION_RULES}
{language_directive(language)}

DEGREE vs FIELD — split them, never put the whole phrase in "degree":
- "degree" = the qualification part ONLY (the level / diploma name).
- "field" = the subject / major / speciality that follows it.
- COPY THE DEGREE VERBATIM — exactly the characters that appear in the
  text. Do NOT translate it, do NOT expand or contract an abbreviation,
  do NOT replace it with a "standard" equivalent, do NOT drop a
  parenthetical or a qualifier. Keep the source language.
    "M.Sc." stays "M.Sc."           (NOT "Master", NOT "MSc")
    "B.Sc. (Excellence)" stays "B.Sc. (Excellence)"   (NOT "Bachelor")
    "Specialized Technician Diploma" stays as written  (NOT translated)
    "Ph.D." stays "Ph.D."           "Licence d'Excellence" stays as written
- The list of qualification words to RECOGNISE (not to output): master,
  licence, bachelor, baccalauréat, doctorat, PhD, ingénieur, technicien
  spécialisé, DUT, BTS, MBA, certificat, diploma, M.Sc., B.Sc., B.A.,
  M.A., B.Eng. … — recognise any of these (and their dotted / spaced
  forms) to find where the degree ends and the field begins, then copy
  the degree text unchanged.
- Examples (input -> degree | field), degree copied character-for-character:
  "Master Systèmes d'Information et Systèmes Intelligents (M2SI)"
     -> "Master" | "Systèmes d'Information et Systèmes Intelligents (M2SI)"
  "M.Sc. Information Systems & Intelligent Systems (M2SI)"
     -> "M.Sc." | "Information Systems & Intelligent Systems (M2SI)"
  "B.Sc. (Excellence) in Artificial Intelligence"
     -> "B.Sc. (Excellence)" | "Artificial Intelligence"
  "Specialized Technician Diploma – Web Full Stack Development"
     -> "Specialized Technician Diploma" | "Web Full Stack Development"
  "Baccalauréat option Sciences Physiques et chimiques"
     -> "Baccalauréat" | "Sciences Physiques et chimiques"
- If NO speciality is stated, field = null. Never invent one.

DATES - READ CAREFULLY:
- Take each entry's dates ONLY from that same entry's own lines. NEVER
  reuse a date that belongs to another entry or to another section
  (a work-experience date must never become an education date).
- For a range "A - B" (or "A – B", "A to B"): start_date = "A",
  end_date = "B", copied verbatim.
- If the entry says "... - Présent", "... - Present", "Depuis 2022",
  "Since 2022": keep that wording in end_date (or start_date for
  "Depuis 2022") exactly — do NOT convert it to a year yourself.
- If the entry has a single date only, put it in end_date and leave
  start_date null.
- If the entry has NO date at all, both start_date and end_date are null.
- Copy any GPA / mention / moyenne verbatim into "gpa".

EDUCATION SECTION TEXT:
\"\"\"
{section_text}
\"\"\"

Return ONLY this JSON object:
{{
  "education": [
    {{
      "institution": "string or null",
      "degree": "string or null",
      "field": "string or null",
      "start_date": "string or null (exact text as written)",
      "end_date": "string or null (exact text as written)",
      "gpa": "string or null (exact text as written)",
      "location": "string or null"
    }}
  ]
}}"""
