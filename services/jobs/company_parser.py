"""
CV Assistant - Cover Letter
Service: JobCompanyParser

Pulls the addressing details a cover letter needs — hiring company name,
the advertised position, the company location/address and a named
recipient — out of a free-text job description.

One focused LLM call via :func:`services.gemini_client.call_gemini`
(``request_type="letter_job_company"``). Never fabricates: a field the job
description does not state comes back as an empty string. An empty job
description is answered locally with no LLM call, and any LLM/JSON failure
degrades to an empty result rather than raising — a missing company block
is acceptable on the rendered letter, a 500 is not.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    StructuredOutputError,
    request_structured_json,
    strip_code_fences,
)

logger = logging.getLogger(__name__)

_LANGUAGE_NAMES = {"fr": "French", "en": "English"}

_EMPTY: dict[str, str] = {
    "company_name": "",
    "position": "",
    "location": "",
    "recipient": "",
    "company_address": "",
}


class JobCompanyParser:
    """Extracts the letter's addressee details from a job description. One LLM call."""

    REQUEST_TYPE = "letter_job_company"

    def extract(self, job_description: str, *, language: str = "en") -> dict[str, str]:
        """
        Parameters
        ----------
        job_description:
            Raw job-posting text.
        language:
            ``"fr"`` or ``"en"`` — only used to steer the model; proper
            nouns are never translated.

        Returns
        -------
        dict[str, str]
            Keys ``company_name``, ``position``, ``location``,
            ``recipient``, ``company_address``. Missing values are ``""``.
        """
        if not job_description or not job_description.strip():
            logger.info("[JobCompanyParser] Empty job description - skipping LLM call.")
            return dict(_EMPTY)

        prompt = self._build_prompt(job_description, language)
        try:
            model = request_structured_json(
                prompt, request_type=self.REQUEST_TYPE,
                validator=validator_for(self.REQUEST_TYPE),
                json_schema=json_schema_for(self.REQUEST_TYPE),
            )
            data = model.model_dump()
        except (StructuredOutputError, RuntimeError) as exc:
            logger.warning("[JobCompanyParser] Extraction failed (%s) - returning empty "
                           "(a missing company block is acceptable, a 500 is not).", exc)
            return dict(_EMPTY)

        result = {
            "company_name": _clean(data.get("company_name")),
            "position": _clean(data.get("position")),
            "location": _clean(data.get("location")),
            "recipient": _clean(data.get("recipient")),
            "company_address": _clean(data.get("company_address")),
        }
        logger.info(
            "[JobCompanyParser] Parsed | company=%s | position=%s | recipient=%s",
            bool(result["company_name"]), bool(result["position"]), bool(result["recipient"]),
        )
        return result

    @staticmethod
    def _build_prompt(job_text: str, language: str) -> str:
        lang = _LANGUAGE_NAMES.get(language, "English")
        return f"""You are an expert HR data extractor. The user is writing a cover
letter in {lang} and needs the addressing details of this job posting.

Extract structured information from the job description below.

STRICT RULES:
- Return ONLY one valid JSON object. No explanation, no markdown, no code fences.
- If a field is not clearly stated in the text, use null. NEVER guess, NEVER
  infer a company from an email domain or a location from a phone prefix.
- Copy company names, positions and addresses verbatim (do not translate,
  reformat or expand abbreviations).
- "recipient" only if the posting names a person or a specific role to contact;
  a generic "our team" is not a recipient -> null.

FIELDS (all nullable — use null when the text does not state it):
- company_name      : the hiring company / organisation
- position          : the exact job title being advertised
- location          : city / country of the role
- recipient         : a named contact or specific role the letter should be addressed to (e.g. "Ms. Dupont", "Head of Engineering")
- company_address   : the company's postal address if literally present

JOB DESCRIPTION:
\"\"\"
{job_text}
\"\"\"

OUTPUT: the JSON object only."""


def _clean(value: Any) -> str:
    """Coerce a model value to a trimmed string; collapse internal whitespace runs."""
    if not value or not isinstance(value, str):
        return ""
    return re.sub(r"\s{2,}", " ", value).strip()
