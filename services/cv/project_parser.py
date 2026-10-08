"""
CV Assistant - CV Generation
Service: ProjectParser

Turns the raw text of the PROJECTS section (and ONLY that section) into a
list of structured project entries, using a single focused LLM call via
``services.gemini_client.call_gemini``.

Never receives the full resume, never fabricates a value — see
``services.parser_common.ANTI_HALLUCINATION_RULES``. GitHub / demo URLs
are only populated when literally present in the text.

Output entry shape (every key always present; missing values are
``None`` / ``[]``):

    {
      "title":        str | None,
      "description":  str | None,
      "technologies": list[str],   # individual items, never grouped
      "github":       str | None,
      "demo":         str | None,
    }
"""

from __future__ import annotations

import logging
from typing import Any

from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    ANTI_HALLUCINATION_RULES,
    TECH_LIST_RULES,
    StructuredOutputError,
    language_directive,
    parse_list_response,
    request_structured_json,
)

logger = logging.getLogger(__name__)


class ProjectParser:
    """Structures the PROJECTS section into JSON. One LLM call per parse."""

    REQUEST_TYPE = "resume_parse_projects"
    EXPECTED_KEY = "projects"

    def parse(self, projects_text: str, *, language: str = "en") -> list[dict[str, Any]]:
        """
        Parse the raw PROJECTS section text into structured entries.

        Parameters
        ----------
        projects_text:
            Raw text of the "projects" section only (output of
            ``SectionSplitter``). Never the full resume.
        language:
            ``"fr"`` or ``"en"`` — free-text (description) only; titles,
            tool names and URLs are never translated.

        Returns
        -------
        list[dict]
            One dict per project. Empty list if the section is empty or
            the response could not be parsed. Never fabricated.
        """
        if not projects_text or not projects_text.strip():
            logger.info("[ProjectParser] Empty section - skipping LLM call.")
            return []

        prompt = self._build_prompt(projects_text, language)
        try:
            model = request_structured_json(
                prompt, request_type=self.REQUEST_TYPE,
                validator=validator_for(self.REQUEST_TYPE),
                json_schema=json_schema_for(self.REQUEST_TYPE),
            )
            entries = [e.model_dump() for e in model.projects]
        except StructuredOutputError as exc:
            logger.warning("[ProjectParser] structured validation failed (%s) — "
                           "lenient salvage of the model's own output.", exc)
            entries = parse_list_response(exc.raw, expected_key=self.EXPECTED_KEY, logger=logger)
        logger.info("[ProjectParser] Parsed %d project entr%s.",
                    len(entries), "y" if len(entries) == 1 else "ies")
        return entries

    @staticmethod
    def _build_prompt(section_text: str, language: str) -> str:
        return f"""You are a precise resume-parsing engine. You are given ONLY the
PROJECTS section of a resume. Extract ONLY the project entries in it.

{ANTI_HALLUCINATION_RULES}
{language_directive(language)}
{TECH_LIST_RULES}

Only fill "github" / "demo" when an actual URL for that project is
present in the text.

PROJECTS SECTION TEXT:
\"\"\"
{section_text}
\"\"\"

Return ONLY this JSON object:
{{
  "projects": [
    {{
      "title": "string or null",
      "description": "string or null",
      "technologies": ["string", "..."],
      "github": "string or null",
      "demo": "string or null"
    }}
  ]
}}"""
