"""
CV Assistant - Cover Letter
Service: LetterGenerator

Writes the body of a cover letter from an already-validated CV profile and
a job description. One focused LLM call via
:func:`services.gemini_client.call_gemini` (``request_type="cover_letter"``).

The prompt is deliberately strict: first person, no template phrasing, no
bracket placeholders, at most four short paragraphs, measurable impact
when the CV provides it. Output language follows the ``language`` argument
(``"fr"`` / ``"en"``). Nothing about the candidate is invented — the model
only reshapes facts already present in the profile.
"""

from __future__ import annotations

import logging

from services.gemini_client import call_gemini

logger = logging.getLogger(__name__)

_LANGUAGE_NAMES = {"fr": "French", "en": "English"}


class LetterGenerator:
    """Generates a cover-letter body string. One LLM call per letter."""

    REQUEST_TYPE = "cover_letter"

    def generate(
        self,
        cv_profile: dict,
        job_description: str,
        company: dict | None = None,
        *,
        language: str = "en",
    ) -> str:
        """
        Parameters
        ----------
        cv_profile:
            ``CVProfile.model_dump()`` of the validated candidate profile.
        job_description:
            Raw job-posting text the letter responds to.
        company:
            Optional addressing context from :class:`JobCompanyParser`
            (``company_name`` / ``position`` / ``recipient``). Used only to
            let the model address the letter correctly.
        language:
            ``"fr"`` or ``"en"`` — the language the letter is written in.

        Returns
        -------
        str
            The letter body (greeting + paragraphs + closing), ready to be
            laid out by :class:`LetterPDFGenerator`.
        """
        company = company or {}
        prompt = self._build_prompt(cv_profile, job_description, company, language)
        body = call_gemini(prompt, request_type=self.REQUEST_TYPE).strip()
        logger.info("[LetterGenerator] Generated | language=%s | chars=%d", language, len(body))
        return body

    @staticmethod
    def _build_prompt(cv_profile: dict, job_description: str, company: dict, language: str) -> str:
        import json as _json

        from services.cv import prompt_kit as pk

        lang = _LANGUAGE_NAMES.get(language, "English")

        addressee = ""
        if company.get("recipient"):
            addressee = f'Address the letter to: {company["recipient"]}.'
        elif company.get("company_name"):
            addressee = f'Address the hiring team at {company["company_name"]}.'
        role_line = f'The advertised role is: {company["position"]}.' if company.get("position") else ""
        company_line = f'The hiring company is: {company["company_name"]}.' if company.get("company_name") else ""

        profile_json = (cv_profile if isinstance(cv_profile, str)
                        else _json.dumps(cv_profile, ensure_ascii=False, default=str))

        return f"""{pk.role("a senior career coach who writes cover letters that sound like the candidate, not like AI")}

OBJECTIVE: write ONE cover letter in {lang} that connects THIS candidate's real
background to THIS specific job. It must read as a short, genuine argument for
why this person fits this role — not a re-narration of the CV.

{pk.language_line(language)}
{company_line}
{role_line}
{addressee}

{pk.candidate_data(profile_json)}

{pk.target_job(job_description)}

{pk.ANTI_FABRICATION}

LETTER-SPECIFIC RULES:
- First person (I, my). Warm, specific, confident — not servile, not generic.
- Do NOT walk through the CV section by section and do NOT restate skills as a
  list. Pick the ONE or TWO threads from the candidate's real experience that
  matter most for THIS job and tell them as a short story with their real impact.
- The job description is context: you may echo a phrase to show you read it; you
  must NOT copy a sentence from it, and you must NOT claim a requirement the
  candidate never demonstrated. If the candidate lacks something the job wants,
  simply lead with the strengths they genuinely have — never apologise for a gap.
- Stay consistent with the CV: same employers, same roles, same seniority.
- 3–4 short paragraphs, ~170–280 words total. Vary sentence length. No filler
  ("I am writing to express my interest", "team player", "perfect fit", "fast
  learner"). No bracket placeholders ([Hiring Manager], [Company], [Date]).
- Name the company and the role naturally, at least once each (if known above).

STRUCTURE: (1) who I am + the exact role; (2) one–two real, relevant experiences
with real impact; (3) why THIS role/company specifically, grounded in the
posting; (4) short close with my name.

OUTPUT: the letter text only, in {lang}. No preamble, no notes, no markdown."""
