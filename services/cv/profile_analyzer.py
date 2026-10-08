"""
CV Assistant - CV Generation
Service: ProfileAnalyzer

Responsibilities:
- Analyse the candidate profile
- Identify strengths, key skills and areas of expertise
- Generate a professional summary via Gemini (single call, result reused)
"""

import json
import logging
from typing import Any

from cv_models import CVProfile
from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    StructuredOutputError,
    language_directive,
    request_structured_json,
)

logger = logging.getLogger(__name__)


class ProfileAnalyzer:
    """Analyses a CVProfile and produces structured insights used by downstream services."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze_profile(self, profile: CVProfile, *, language: str = "en") -> dict[str, Any]:
        """
        Analyse the candidate profile and return a structured analysis dict.

        ``language`` ('fr' / 'en') controls the language of every generated
        free-text field (summary, objective, strengths, expertise labels).
        The candidate's own facts are never translated.

        The result contains:
        - ``professional_summary``: AI-generated or existing summary
        - ``key_skills``: flat list of the candidate's most relevant skills
        - ``strengths``: 3-5 identified strengths
        - ``expertise_areas``: broad domain labels
        - ``years_of_experience``: approximate total
        - ``seniority_level``: Junior / Mid / Senior / Lead / Executive
        - ``career_objective``: one-sentence objective statement

        Parameters
        ----------
        profile:
            The full candidate profile.

        Returns
        -------
        dict
            Structured analysis ready to be consumed by cv_generator and ats_optimizer.
        """
        logger.info("[ProfileAnalyzer] Starting profile analysis for: %s", profile.name)

        # Build a compact text representation of the profile to minimise token usage
        profile_text = self._serialize_profile(profile)

        # --- Single structured LLM call for the full analysis ---
        prompt = self._build_analysis_prompt(profile_text, language)
        try:
            model = request_structured_json(
                prompt, request_type="profile_analysis",
                validator=validator_for("profile_analysis"),
                json_schema=json_schema_for("profile_analysis"),
            )
            analysis = model.model_dump()
        except StructuredOutputError as exc:
            logger.warning("[ProfileAnalyzer] structured validation failed (%s) — "
                           "conservative local fallback (candidate data only).", exc)
            analysis = self._local_fallback(profile)

        # If caller already provided a professional summary, honour it
        if profile.professional_summary:
            analysis["professional_summary"] = profile.professional_summary

        logger.info(
            "[ProfileAnalyzer] Analysis complete | seniority=%s | expertise_areas=%s",
            analysis.get("seniority_level"),
            analysis.get("expertise_areas"),
        )
        return analysis

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_profile(profile: CVProfile) -> str:
        """Convert the profile to a compact text block for the Gemini prompt."""
        lines: list[str] = [
            f"Candidate: {profile.name}",
            f"Email: {profile.email}",
        ]
        if profile.address:
            lines.append(f"Location: {profile.address}")
        if profile.nationality:
            lines.append(f"Nationality: {profile.nationality}")

        # Education
        if profile.education:
            lines.append("\nEDUCATION:")
            for edu in profile.education:
                gpa_str = f", GPA {edu.gpa}" if edu.gpa else ""
                dates = " – ".join(d for d in (edu.start_date, edu.end_date) if d)
                date_str = f" ({dates})" if dates else ""
                lines.append(
                    f"  - {edu.degree} in {edu.field} | {edu.institution}{date_str}{gpa_str}"
                )

        # Experience
        if profile.experience:
            lines.append("\nEXPERIENCE:")
            for exp in profile.experience:
                period = f" ({exp.period})" if exp.period else ""
                lines.append(f"  - {exp.position} @ {exp.company}{period}")
                if exp.description:
                    lines.append(f"    {exp.description}")
                for ach in exp.achievements:
                    lines.append(f"    • {ach}")
                if exp.technologies:
                    lines.append(f"    Tech: {', '.join(exp.technologies)}")

        # Skills
        if profile.skills:
            lines.append("\nSKILLS:")
            for cat in profile.skills:
                lines.append(f"  [{cat.category}] {', '.join(cat.skills)}")

        # Projects
        if profile.projects:
            lines.append("\nPROJECTS:")
            for proj in profile.projects:
                lines.append(f"  - {proj.title}: {proj.description}")
                if proj.technologies:
                    lines.append(f"    Tech: {', '.join(proj.technologies)}")

        # Certifications
        if profile.certifications:
            lines.append("\nCERTIFICATIONS:")
            for cert in profile.certifications:
                lines.append(f"  - {cert.name} ({cert.issuer}, {cert.year})")

        # Languages
        if profile.languages:
            lines.append("\nLANGUAGES:")
            for lang in profile.languages:
                lines.append(f"  - {lang.language}: {lang.level}")

        return "\n".join(lines)

    @staticmethod
    def _build_analysis_prompt(profile_text: str, language: str = "en") -> str:
        from services.cv import prompt_kit as pk

        return f"""{pk.role("an expert recruiter and ATS résumé analyst")}

OBJECTIVE: from the candidate data below, produce a professional summary, a
one-line career objective, and a structured read of the candidate's real
strengths and expertise. This feeds the rest of the CV — it must be accurate
and non-redundant with the experience section that will follow.

{pk.language_line(language)}

{pk.candidate_data(profile_text)}

{pk.ANTI_FABRICATION}

{pk.COHERENCE}

{pk.RESULTS_ORIENTED}

CONSTRAINTS:
1. professional_summary: 55–90 words, 3–4 sentences, written for a recruiter.
   Name the candidate's 2–3 signature technologies and their real domain. It
   SYNTHESISES — it does not list every skill and does not pre-quote the
   experience bullets.
2. career_objective: ONE sentence, forward-looking, specific to the candidate's
   real domain. Must not repeat the summary's wording.
3. key_skills: the candidate's most relevant REAL skills, verbatim (max 15).
4. strengths: 3–5, each grounded in a specific fact in the data. "" list if the
   data does not support any.
5. expertise_areas: broad domain LABELS ("Backend development", "Data
   engineering"). A label may be inferred from education/projects; an experience
   entry may not be invented.
6. years_of_experience: integer, counting ONLY professional experience with
   stated dates. No "+". Unclear → 0. Never round school/project time up.
7. seniority_level: one of Junior|Mid|Senior|Lead|Executive, ONLY if the stated
   experience clearly supports it — otherwise "".
8. No filler adjectives ("passionate", "motivated", "hard-working", "results-driven").

{pk.JSON_ONLY}
Exact keys:
{{
    "professional_summary": "...",
    "career_objective": "...",
    "key_skills": ["..."],
    "strengths": ["..."],
    "expertise_areas": ["..."],
    "years_of_experience": 0,
    "seniority_level": "Junior|Mid|Senior|Lead|Executive or empty string"
}}"""

    @staticmethod
    def _local_fallback(profile: CVProfile) -> dict[str, Any]:
        """Conservative fallback: derive ONLY from data the candidate actually
        provided. Never invents a seniority label, a strength, a year count or
        a templated summary — those would be fabrications that could reach the
        final PDF. An empty summary makes ATSOptimizer keep the candidate's own
        ``professional_summary`` (or leave it blank)."""
        all_skills = [s for cat in profile.skills for s in cat.skills]
        return {
            "professional_summary": profile.professional_summary or "",
            "career_objective": "",
            "key_skills": all_skills[:15],
            "strengths": [],
            "expertise_areas": [cat.category for cat in profile.skills[:3]],
            "years_of_experience": 0,
            "seniority_level": "",
        }