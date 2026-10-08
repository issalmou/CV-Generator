"""
CV Assistant - CV Generation
Service: CVGenerator

Responsibilities:
- Assemble all CV sections from profile, profile analysis and ATS optimisation
- Use Gemini for skill categorisation (one call, reused across sections)
- Produce a structured CVData dict consumed by PDFGenerator

Gemini calls in this module: maximum 1 (skill categorisation only when needed).
All other section assembly is local, reusing data already produced by upstream
services.
"""

import json
import logging
from typing import Any
import re
from cv_models import CVProfile
from services.gemini_client import call_gemini

logger = logging.getLogger(__name__)


class CVGenerator:
    """Assembles structured CV content from all upstream service results."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_cv(
        self,
        profile: CVProfile,
        profile_analysis: dict[str, Any],
        ats_analysis_obj: Any,  # ATSAnalysis instance
        optimized_content: dict[str, Any],
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Produce a complete, structured CV data dictionary.

        The returned dict contains all sections ready to be rendered by
        PDFGenerator.render_to_bytes():
          - header
          - summary
          - skills
          - experience
          - projects
          - education
          - certifications
          - languages

        Parameters
        ----------
        profile:
            Raw candidate profile.
        profile_analysis:
            Output of ProfileAnalyzer.analyze_profile().
        ats_analysis_obj:
            ATSAnalysis instance from ATSOptimizer.calculate_match_score().
        optimized_content:
            Output of ATSOptimizer.optimize_content().
        language:
            'fr' or 'en'. Only affects newly-generated text: skill-category
            naming (when Gemini categorisation is needed) and is carried
            through to PDFGenerator for section-title translation. The
            candidate's own data (names, companies, dates, etc.) is never
            translated.

        Returns
        -------
        dict
            Fully assembled CV data, including the `language` key consumed
            by PDFGenerator.
        """
        logger.info("[CVGenerator] Assembling CV for: %s | language=%s", profile.name, language)

        header = self._build_header(profile)
        summary = self._build_summary(profile_analysis, optimized_content)
        skills = self._build_skills(
            profile, profile_analysis, ats_analysis_obj, optimized_content, language=language
        )
        experience = self._build_experience(profile, optimized_content)
        projects = self._build_projects(profile)
        education = self._build_education(profile)
        certifications = self._build_certifications(profile)
        languages = self._build_languages(profile)

        cv_data: dict[str, Any] = {
            "header": header,
            "summary": summary,
            "skills": skills,
            "experience": experience,
            "projects": projects,
            "education": education,
            "certifications": certifications,
            "languages": languages,
            "ats_score": getattr(ats_analysis_obj, "ats_score", 0.0),
            "language": language,
            # SUGGESTIONS ONLY — job keywords the candidate does NOT have.
            # Returned for the frontend to show separately; NEVER rendered in
            # the CV body, NEVER presented as owned skills (constraint #6).
            "additional_skills": self._suggested_skills(profile, optimized_content),
            # ATS optimisation outcome for the frontend (constraint #10).
            "ats_optimization": optimized_content.get("_status", "skipped"),
        }
        logger.info(
            "[CVGenerator] CV assembly complete | suggested_skills=%d | ats=%s",
            len(cv_data["additional_skills"]), cv_data["ats_optimization"],
        )
        return cv_data

    @staticmethod
    def _suggested_skills(profile: CVProfile, optimized_content: dict[str, Any]) -> list[str]:
        """``additional_skills`` from ATS optimisation, filtered so nothing the
        candidate ALREADY lists can leak in as a 'suggestion', de-duplicated,
        capped at 5. These are proposals — acceptance is a later user action."""
        owned = {
            s.strip().lower()
            for cat in profile.skills for s in cat.skills if s.strip()
        }
        out: list[str] = []
        for sk in optimized_content.get("additional_skills", []) or []:
            if not isinstance(sk, str):
                continue
            sk = sk.strip()
            if sk and sk.lower() not in owned and sk not in out:
                out.append(sk)
        return out[:5]

    # ------------------------------------------------------------------
    # Section builders (all local – no extra Gemini calls)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_header(profile: CVProfile) -> dict[str, Any]:
        return {
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone,
            "linkedin": profile.linkedin or "",
            "github": profile.github or "",
            "portfolio": profile.portfolio or "",
            "address": profile.address or "",
        }

    @staticmethod
    def _build_summary(
        profile_analysis: dict[str, Any],
        optimized_content: dict[str, Any],
    ) -> dict[str, str]:
        summary = optimized_content.get("optimized_summary") or profile_analysis.get(
            "professional_summary", ""
        )
        objective = optimized_content.get("career_objective") or profile_analysis.get(
            "career_objective", ""
        )
        return {
            "professional_summary": summary,
            "career_objective": objective,
        }

    def _build_skills(
        self,
        profile: CVProfile,
        profile_analysis: dict[str, Any],
        ats_analysis_obj: Any,
        optimized_content: dict[str, Any],
        language: str = "en",
    ) -> list[dict[str, Any]]:
        """
        Return the candidate's categorised skills — **only skills the
        candidate genuinely has**. Job keywords they lack are never added here
        (they go to ``cv_data["additional_skills"]`` as suggestions, constraint
        #6). One LLM call ONLY when the profile has no pre-categorised skills.
        """
        # Start from profile's existing categories (already structured)
        if profile.skills:
            categories: dict[str, list[str]] = {
                cat.category: list(cat.skills) for cat in profile.skills
            }
        else:
            # No categories defined – ask the LLM to categorise (one call)
            all_skills = list(profile_analysis.get("key_skills", []))
            categories = self._categorise_skills_with_gemini(all_skills, language=language)

        return [
            {"category": cat, "skills": skills}
            for cat, skills in categories.items()
            if skills
        ]

    @staticmethod
    def _categorise_skills_with_gemini(skills: list[str], language: str = "en") -> dict[str, list[str]]:
        """Single Gemini call to categorise a flat skills list."""
        if not skills:
            return {}
        from services.cv import prompt_kit as pk

        skills_str = ", ".join(skills)
        lang = {"fr": "French", "en": "English"}.get(language, "English")
        prompt = f"""{pk.role("an expert CV / ATS writer")}

OBJECTIVE: sort the candidate's skill list into professional résumé groups.
This is a GROUPING task only — the skills themselves are already final.

{pk.fence("SKILLS", skills_str)}

RULES:
- Category NAMES in {lang}, ATS-friendly (e.g. "Programming Languages",
  "Frameworks & Libraries", "Cloud & DevOps", "Databases", "Tools & Platforms",
  "Data & Analytics", "Languages", "Soft Skills", "Other"). Create categories as
  needed.
- Copy every skill CHARACTER FOR CHARACTER. Do NOT rename, translate, expand an
  abbreviation, fix spelling, split or merge any skill.
- Do NOT add a skill. Do NOT drop a skill. Every input skill appears exactly
  once, in exactly one category (best fit). Keep one of an exact duplicate pair.
- The union of all category arrays must equal the input list (minus exact dups).

{pk.JSON_ONLY}
Shape: {{ "Category Name": ["skill", "skill"], ... }}"""
        raw = call_gemini(prompt, request_type="skill_categorisation")
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)

        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, list)}
        except json.JSONDecodeError:
            logger.warning("[CVGenerator] Skill categorisation JSON parse failed.")

        return {"Technical Skills": skills}
    @staticmethod
    def _build_experience(
        profile: CVProfile,
        optimized_content: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Merge original experience with ATS-optimised achievement bullets.
        """
        # Build a lookup: "position @ company" -> optimised bullets
        opt_map: dict[str, list[str]] = {}
        for item in optimized_content.get("optimized_achievements", []):
            key = item.get("original_role", "").lower()
            if key:
                opt_map[key] = item.get("bullets", [])

        result: list[dict[str, Any]] = []
        for exp in profile.experience:
            role_key = f"{exp.position} @ {exp.company}".lower()
            bullets = (
                opt_map.get(role_key)
                or exp.achievements
                or ([exp.description] if exp.description else [])
            )
            result.append(
                {
                    "company": exp.company,
                    "position": exp.position,
                    "period": exp.period or "",
                    "location": exp.location or "",
                    "bullets": bullets,
                    "technologies": exp.technologies,
                }
            )
        return result

    @staticmethod
    def _build_projects(profile: CVProfile) -> list[dict[str, Any]]:
        return [
            {
                "title": proj.title,
                "description": proj.description,
                "technologies": proj.technologies,
                "github": proj.github or "",
                "demo": proj.demo or "",
            }
            for proj in profile.projects
        ]

    @staticmethod
    def _build_education(profile: CVProfile) -> list[dict[str, Any]]:
        return [
            {
                "institution": edu.institution,
                "degree": edu.degree,
                "field": edu.field,
                "start_date": edu.start_date,
                "end_date": edu.end_date,
                "gpa": edu.gpa,
                "location": edu.location or "",
            }
            for edu in profile.education
        ]

    @staticmethod
    def _build_certifications(profile: CVProfile) -> list[dict[str, Any]]:
        return [
            {
                "name": cert.name,
                "issuer": cert.issuer,
                "year": cert.year,
            }
            for cert in profile.certifications
        ]

    @staticmethod
    def _build_languages(profile: CVProfile) -> list[dict[str, Any]]:
        return [
            {"language": lang.language, "level": lang.level}
            for lang in profile.languages
        ]
