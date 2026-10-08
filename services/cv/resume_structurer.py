"""
CV Assistant - CV Generation
Service: ResumeStructurer

Thin orchestrator over the three section-level parsers:

    services/experience_parser.py  -> ExperienceParser
    services/education_parser.py   -> EducationParser
    services/project_parser.py     -> ProjectParser

Each parser turns ONE already-split section into structured JSON with a
single focused LLM call (via ``services.gemini_client.call_gemini``), and
never sees the rest of the resume. This keeps token usage minimal (3
small calls instead of one huge call) and keeps the hallucination surface
tiny — the model only ever sees one section at a time.

``ResumeStructurer`` exists so callers (the pipeline, the tests) have one
stable entry point and so the Level 5 whole-resume fallback
(``parse_fallback``) — the one path that DOES see the full resume — lives
next to the code it substitutes for.

LLM calls
---------
- Normal path: at most 3 (experience + education + projects), one per
  non-empty section.
- Level 5 fallback: at most 1 additional call, and ONLY when the pipeline
  detected an education/experience header but recovered an empty body for
  it (see ``ResumeParserPipeline`` for the exact trigger). This module
  never decides *when* to run the fallback — only *what it returns*.

Anti-hallucination contract: see
``services.parser_common.ANTI_HALLUCINATION_RULES``. Nothing here ever
fabricates a placeholder value; a missing field is ``None`` / ``[]``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from schemas.llm_schemas import json_schema_for, validator_for
from services.cv.education_parser import EducationParser
from services.cv.experience_parser import ExperienceParser
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    ANTI_HALLUCINATION_RULES,
    TECH_LIST_RULES,
    StructuredOutputError,
    language_directive,
    request_structured_json,
    strip_code_fences,
)
from services.cv.project_parser import ProjectParser
from services.cv.skills_parser import SkillsParser

logger = logging.getLogger(__name__)


@dataclass
class FallbackParseResult:
    """
    Structured result of the Level 5 whole-resume fallback parse.

    Mirrors the shapes returned by the three section parsers, plus
    certifications and skills, since the fallback prompt covers the whole
    resume in one shot rather than per-section.
    """
    education: list[dict[str, Any]] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


class ResumeStructurer:
    """Orchestrates the three section parsers + the Level 5 fallback."""

    def __init__(self) -> None:
        self._experience_parser = ExperienceParser()
        self._education_parser = EducationParser()
        self._project_parser = ProjectParser()
        self._skills_parser = SkillsParser()

    # ------------------------------------------------------------------
    # Normal path — delegate to one dedicated parser per section
    # ------------------------------------------------------------------

    def parse_experience(
        self, experience_text: str, *, language: str = "en"
    ) -> list[dict[str, Any]]:
        """Structure the EXPERIENCE section only. Delegates to ExperienceParser."""
        return self._experience_parser.parse(experience_text, language=language)

    def parse_education(
        self, education_text: str, *, language: str = "en"
    ) -> list[dict[str, Any]]:
        """Structure the EDUCATION section only. Delegates to EducationParser."""
        return self._education_parser.parse(education_text, language=language)

    def parse_projects(
        self, projects_text: str, *, language: str = "en"
    ) -> list[dict[str, Any]]:
        """Structure the PROJECTS section only. Delegates to ProjectParser."""
        return self._project_parser.parse(projects_text, language=language)

    def parse_skills(
        self, skills_text: str, *, language: str = "en"
    ) -> list[str]:
        """
        Structure the SKILLS section only into a FLAT ``list[str]``.
        Delegates to SkillsParser (LLM, section-scoped, local regex fallback).
        """
        return self._skills_parser.parse(skills_text, language=language)

    # ------------------------------------------------------------------
    # Level 5 — whole-resume fallback (see module docstring)
    # ------------------------------------------------------------------

    def parse_fallback(self, full_text: str, *, language: str = "en") -> FallbackParseResult:
        """
        Structure education, experience, projects, certifications and
        skills from the ENTIRE resume text in a single call.

        Must ONLY be called by the pipeline when deterministic section
        splitting found a recognised education/experience header but
        produced an empty body for it — the signature of a layout /
        extraction failure, not of a candidate who genuinely has no
        entries. See ``ResumeParserPipeline`` for the trigger.

        Returns an all-empty ``FallbackParseResult`` on any parse failure
        — never fabricated data.
        """
        if not full_text or not full_text.strip():
            logger.info("[ResumeStructurer] Empty full text - skipping fallback LLM call.")
            return FallbackParseResult()

        prompt = self._build_fallback_prompt(full_text, language)
        try:
            model = request_structured_json(
                prompt, request_type="resume_parse_fallback",
                validator=validator_for("resume_parse_fallback"),
                json_schema=json_schema_for("resume_parse_fallback"),
            )
            return FallbackParseResult(
                education=[e.model_dump() for e in model.education],
                experience=[e.model_dump() for e in model.experience],
                projects=[e.model_dump() for e in model.projects],
                certifications=[e.model_dump() for e in model.certifications],
                skills=list(model.skills),
            )
        except StructuredOutputError as exc:
            logger.warning("[ResumeStructurer] structured validation failed (%s) — "
                           "lenient salvage of the model's own output.", exc)
            return self._parse_fallback_response(exc.raw)

    # ------------------------------------------------------------------
    # Fallback prompt + response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fallback_prompt(full_text: str, language: str) -> str:
        return f"""You are a precise resume-parsing engine. Deterministic section
detection could not reliably isolate this resume's sections (unusual
layout, designer template, sidebar format...). You are given the ENTIRE
resume text below. Extract whatever is genuinely present.

{ANTI_HALLUCINATION_RULES}
{language_directive(language)}
{TECH_LIST_RULES}
- If a whole category (education, experience, projects, certifications,
  skills) is genuinely absent from the text, return an empty list for it.
  Do NOT fabricate entries to fill a category.
- Keep every date attached to its OWN entry. Never move a date from one
  entry/section to another. Copy "Present"/"Présent"/"Depuis" verbatim.

FULL RESUME TEXT:
\"\"\"
{full_text}
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
  ],
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
  ],
  "projects": [
    {{
      "title": "string or null",
      "description": "string or null",
      "technologies": ["string", "..."],
      "github": "string or null",
      "demo": "string or null"
    }}
  ],
  "certifications": [
    {{
      "name": "string or null",
      "issuer": "string or null",
      "date_text": "string or null (exact text as written)"
    }}
  ],
  "skills": ["string", "..."]
}}"""

    @staticmethod
    def _parse_fallback_response(raw: str) -> FallbackParseResult:
        """Parse the multi-key fallback response defensively (empty result on failure)."""
        clean = strip_code_fences(raw)

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning(
                "[ResumeStructurer] JSON parse failed for fallback response - "
                "returning empty result. raw=%r",
                raw[:300],
            )
            return FallbackParseResult()

        if not isinstance(data, dict):
            logger.warning(
                "[ResumeStructurer] Unexpected JSON root type for fallback response: %s",
                type(data).__name__,
            )
            return FallbackParseResult()

        def _dict_list(key: str) -> list[dict[str, Any]]:
            items = data.get(key, [])
            if not isinstance(items, list):
                logger.warning(
                    "[ResumeStructurer] Expected list for fallback key '%s', got %s.",
                    key, type(items).__name__,
                )
                return []
            return [item for item in items if isinstance(item, dict)]

        skills_raw = data.get("skills", [])
        if isinstance(skills_raw, list):
            skills = [s for s in skills_raw if isinstance(s, str) and s.strip()]
        else:
            logger.warning(
                "[ResumeStructurer] Expected list for fallback key 'skills', got %s.",
                type(skills_raw).__name__,
            )
            skills = []

        return FallbackParseResult(
            education=_dict_list("education"),
            experience=_dict_list("experience"),
            projects=_dict_list("projects"),
            certifications=_dict_list("certifications"),
            skills=skills,
        )
