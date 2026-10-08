"""
CV Assistant - CV Generation
Service: ATSOptimizer

Responsibilities:
- Extract ATS keywords from a job description (one Gemini call)
- Calculate a match score between the candidate profile and the job
- Propose optimised content (no redundant Gemini calls; reuses profile analysis)

Gemini calls in this module: maximum 2 (keyword extraction + content optimisation).
If no job description is provided, all methods return sensible defaults without
any Gemini call.
"""

import json
import logging
import re
from typing import Any

from cv_models import ATSAnalysis, CVProfile
from schemas.llm_schemas import json_schema_for, validator_for
from services.gemini_client import call_gemini  # noqa: F401 — kept for test monkeypatching
from services.parser_common import (
    StructuredOutputError,
    language_directive,
    request_structured_json,
)

logger = logging.getLogger(__name__)

# ATS optimisation outcome — surfaced to the frontend (constraint #10) so a
# user always knows whether the LLM optimisation actually ran.
ATS_APPLIED = "applied"            # LLM optimisation succeeded
ATS_LOCAL_FALLBACK = "local_fallback"  # LLM failed validation -> safe local reformulation
ATS_SKIPPED = "skipped"            # no job description / already well matched -> local only
ATS_CACHE = "cache"               # served from the application cache

# ATS-friendly action verbs for local fallback reformulation (no Gemini needed)
_ACTION_VERBS: list[str] = [
    "Designed", "Developed", "Implemented", "Architected", "Optimized",
    "Delivered", "Led", "Managed", "Automated", "Reduced", "Improved",
    "Built", "Deployed", "Integrated", "Analysed", "Migrated", "Scaled",
    "Streamlined", "Coordinated", "Mentored",
]


class ATSOptimizer:
    """Keyword extraction, match scoring and content optimisation for ATS compliance."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract_keywords(self, job_description: str) -> dict[str, list[str]]:
        """
        Extract categorised ATS keywords from the job description.

        Returns a dict with keys:
        - technical_skills
        - business_skills
        - tools
        - frameworks
        - certifications
        - degrees
        - soft_skills

        Uses one Gemini call; result is cached automatically by gemini_client.
        """
        if not job_description or not job_description.strip():
            logger.info("[ATSOptimizer] No job description provided – skipping keyword extraction.")
            return self._empty_keywords()

        logger.info("[ATSOptimizer] Extracting ATS keywords from job description.")
        prompt = self._build_keyword_extraction_prompt(job_description)
        try:
            model = request_structured_json(
                prompt, request_type="ats_keyword_extraction",
                validator=validator_for("ats_keyword_extraction"),
                json_schema=json_schema_for("ats_keyword_extraction"),
            )
            return model.model_dump()
        except StructuredOutputError as exc:
            logger.warning("[ATSOptimizer] keyword extraction failed validation (%s) — "
                           "salvaging model output.", exc)
            return self._parse_keywords(exc.raw)

    def calculate_match_score(
        self,
        profile: CVProfile,
        keywords: dict[str, list[str]],
        profile_analysis: dict[str, Any],
    ) -> ATSAnalysis:
        """
        Compare candidate skills against extracted keywords and compute the ATS score.

        This method is PURELY LOCAL – no Gemini call.

        Parameters
        ----------
        profile:
            Candidate profile.
        keywords:
            Output of ``extract_keywords``.
        profile_analysis:
            Output of ``ProfileAnalyzer.analyze_profile``.

        Returns
        -------
        ATSAnalysis
            Structured matching result with an ATS score 0-100.
        """
        all_keywords: list[str] = [
            kw
            for bucket in keywords.values()
            for kw in bucket
        ]
        if not all_keywords:
            return ATSAnalysis(ats_score=0.0)

        candidate_text = self._build_candidate_text(profile, profile_analysis)
        candidate_lower = candidate_text.lower()

        matched: list[str] = []
        missing: list[str] = []

        for kw in all_keywords:
            pattern = re.compile(re.escape(kw.lower()))
            if pattern.search(candidate_lower):
                matched.append(kw)
            else:
                missing.append(kw)

        score = round(len(matched) / len(all_keywords) * 100, 1) if all_keywords else 0.0

        # Keywords to highlight = high-value matched ones (technical + frameworks)
        high_value = set(keywords.get("technical_skills", []) + keywords.get("frameworks", []))
        to_highlight = [kw for kw in matched if kw in high_value]

        analysis = ATSAnalysis(
            extracted_keywords=all_keywords,
            matched_keywords=matched,
            missing_keywords=missing,
            keywords_to_highlight=to_highlight,
            ats_score=score,
        )
        logger.info(
            "[ATSOptimizer] Match score: %.1f%% | matched=%d/%d",
            score,
            len(matched),
            len(all_keywords),
        )
        return analysis

    def optimize_content(
        self,
        profile: CVProfile,
        profile_analysis: dict[str, Any],
        ats_analysis: ATSAnalysis,
        job_description: str,
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Return optimised CV content (experiences, projects, skills, summary).

        Strategy:
        - If ATS score >= 80 and no missing keywords: only local reformulation
          (no Gemini call saved).
        - Otherwise: one Gemini call to integrate missing keywords naturally.

        `language` ('fr' or 'en') controls only the language of newly
        generated text (summary, career objective, bullet rewrites). It
        never changes the underlying prompt logic or rules.

        Returns a dict consumed by cv_generator.generate_cv().
        """
        # Quick path: profile is already well matched
        if ats_analysis.ats_score >= 80 and not ats_analysis.missing_keywords:
            logger.info(
                "[ATSOptimizer] High ATS score (%.1f%%) – using local optimisation only.",
                ats_analysis.ats_score,
            )
            return self._local_optimize(profile, profile_analysis, status=ATS_SKIPPED)

        if not job_description or not job_description.strip():
            return self._local_optimize(profile, profile_analysis, status=ATS_SKIPPED)

        logger.info("[ATSOptimizer] Running LLM-assisted content optimisation | language=%s", language)
        prompt = self._build_optimization_prompt(
            profile, profile_analysis, ats_analysis, job_description, language=language
        )
        try:
            model = request_structured_json(
                prompt, request_type="ats_content_optimization",
                validator=validator_for("ats_content_optimization"),
                json_schema=json_schema_for("ats_content_optimization"),
            )
        except StructuredOutputError as exc:
            logger.error("[ATSOptimizer] content optimisation failed validation after "
                         "one repair (%s) — EXPLICIT local fallback (surfaced to caller).", exc)
            return self._local_optimize(profile, profile_analysis, status=ATS_LOCAL_FALLBACK)

        return {
            "optimized_summary": model.optimized_summary or profile_analysis.get("professional_summary", ""),
            "career_objective": model.career_objective or profile_analysis.get("career_objective", ""),
            "optimized_achievements": [r.model_dump() for r in model.optimized_achievements],
            # suggestions only — never merged into the CV body (constraint #6)
            "additional_skills": list(model.additional_skills),
            "_status": ATS_APPLIED,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_keywords() -> dict[str, list[str]]:
        return {
            "technical_skills": [],
            "business_skills": [],
            "tools": [],
            "frameworks": [],
            "certifications": [],
            "degrees": [],
            "soft_skills": [],
        }

    @staticmethod
    def _build_keyword_extraction_prompt(job_description: str) -> str:
        from services.cv import prompt_kit as pk

        return f"""{pk.role("an expert ATS analyst")}

OBJECTIVE: list the concrete skills, tools, technologies, certifications and
degrees this posting actually asks for, categorised. This feeds a candidate↔job
match — precision matters more than recall.

{pk.fence("JOB DESCRIPTION", job_description)}

RULES:
- Extract ONLY real requirements/qualifications. IGNORE company boilerplate,
  benefits, salary, culture words, location, and vague phrases
  ("fast-paced environment", "wear many hats").
- Keep each item's exact wording from the posting. One item per string. No
  duplicates across buckets (put a term in its single best bucket).
- "certifications" = named certs only (e.g. "AWS Certified Solutions Architect").
- "degrees" = named qualifications only (e.g. "BSc Computer Science").
- "soft_skills" only when the posting explicitly requires them.
- If a bucket has nothing, return an empty array — never invent to fill it.

{pk.JSON_ONLY}
Exact keys:
{{
  "technical_skills": [], "business_skills": [], "tools": [], "frameworks": [],
  "certifications": [], "degrees": [], "soft_skills": []
}}"""

    @staticmethod
    def _parse_keywords(raw: str) -> dict[str, list[str]]:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return {
                    k: [str(v) for v in lst] if isinstance(lst, list) else []
                    for k, lst in data.items()
                }
        except json.JSONDecodeError:
            logger.warning("[ATSOptimizer] Keyword JSON parse failed – returning empty.")
        return ATSOptimizer._empty_keywords()

    @staticmethod
    def _build_candidate_text(profile: CVProfile, analysis: dict[str, Any]) -> str:
        parts: list[str] = [
            analysis.get("professional_summary", ""),
            " ".join(analysis.get("key_skills", [])),
        ]
        for exp in profile.experience:
            parts.append(exp.position)
            parts.append(exp.description or "")
            parts.extend(exp.achievements)
            parts.extend(exp.technologies)
        for cat in profile.skills:
            parts.extend(cat.skills)
        for proj in profile.projects:
            parts.append(proj.description)
            parts.extend(proj.technologies)
        for cert in profile.certifications:
            parts.append(cert.name)
        for edu in profile.education:
            parts.append(edu.degree)
            parts.append(edu.field)
        return " ".join(p for p in parts if p)

    @staticmethod
    def _build_optimization_prompt(
        profile: CVProfile,
        analysis: dict[str, Any],
        ats: ATSAnalysis,
        job_description: str | None,
        language: str = "en",
    ) -> str:
        from services.cv import prompt_kit as pk

        # Constraint #7: NO silent experience[:4] / achievements[:3] slice.
        # Every role and every achievement is given to the optimiser. A very
        # large profile is bounded by a char budget with an EXPLICIT marker so
        # the model (and a reviewer) can see something was elided for context
        # size — never a hidden truncation.
        _BUDGET = 7000
        parts: list[str] = []
        used = 0
        for exp in profile.experience:
            line = f"\n- {exp.position} @ {exp.company}: " + "; ".join(exp.achievements)
            if used + len(line) > _BUDGET and parts:
                parts.append(f"\n- [context budget reached — {len(profile.experience) - len(parts)} "
                             f"more role(s) not shown in THIS prompt; they are still in the CV]")
                break
            parts.append(line)
            used += len(line)
        exp_text = "".join(parts)
    
        current_summary = analysis.get("professional_summary", "")
        data_block = (f"CURRENT PROFESSIONAL SUMMARY:\n{current_summary}\n\n"
                      f"EXPERIENCE (role -> the candidate's real achievements):{exp_text}")

        if job_description and job_description.strip():
            missing_str = ", ".join(ats.missing_keywords[:20]) or "(none)"
            highlight_str = ", ".join(ats.keywords_to_highlight[:15]) or "(none)"
            mode_block = f"""MODE: job-targeted.
{pk.target_job(job_description[:1200])}

KEYWORDS THE CANDIDATE ALREADY DEMONSTRATES — make their exact names visible in
real sentences of the summary and the relevant bullets: {highlight_str}

KEYWORDS THE JOB ASKS FOR BUT THAT ARE ABSENT FROM THE CANDIDATE DATA: {missing_str}
- You MUST NOT write any of these into optimized_summary, career_objective or
  any bullet. Claiming a skill the candidate never mentioned is a disqualifying fabrication.
- You MAY put up to 5 of them, verbatim, in "additional_skills" as SUGGESTIONS
  the user can choose to add later — never as skills already owned.

Mode rules:
- career_objective: aim it at this role, using ONLY the candidate's real background.
- optimized_achievements: reorder so the most job-relevant REAL bullets come first."""
        else:
            expertise = ", ".join(analysis.get("expertise_areas", [])[:5]) or "(not stated)"
            seniority = analysis.get("seniority_level") or ""
            mode_block = f"""MODE: general-purpose (no target job).
Candidate expertise areas: {expertise}{(chr(10) + 'Seniority: ' + seniority) if seniority else ''}
- Write a strong general summary of the candidate's real core expertise.
- career_objective: broad but specific to the candidate's real domain. Do NOT
  invent a target company or role."""

        return f"""{pk.role("an expert ATS résumé writer")}

OBJECTIVE: rewrite the professional summary, write a one-line career objective,
and rewrite the experience bullets so they read strongly and are ATS-friendly —
WITHOUT changing any fact. This is a wording pass, not a content pass.

{pk.language_line(language)}

{mode_block}

{pk.candidate_data(data_block)}

{pk.ANTI_FABRICATION}

{pk.COHERENCE}

{pk.RESULTS_ORIENTED}

{pk.ATS_NATURAL}

CONSTRAINTS:
1. optimized_summary: 2–4 sentences, ~45–80 words. Synthesises; does not copy a
   bullet verbatim.
2. career_objective: ONE sentence, no overlap with the summary's wording.
3. optimized_achievements: one entry per role that has bullets; "original_role"
   = "<position> @ <company>" copied exactly. Each bullet 12–26 words.
4. Keep every role's bullets attributed to THAT role. Do not merge roles, do not
   move a bullet between roles.
5. additional_skills: max 5, ONLY absent job keywords, verbatim, as suggestions.

{pk.JSON_ONLY}
Exact shape:
{{
  "optimized_summary": "...",
  "career_objective": "...",
  "optimized_achievements": [
    {{ "original_role": "Position @ Company", "bullets": ["...", "..."] }}
  ],
  "additional_skills": ["..."]
}}"""
    @staticmethod
    def _local_optimize(profile: CVProfile, analysis: dict[str, Any],
                        *, status: str = ATS_LOCAL_FALLBACK) -> dict[str, Any]:
        """Fast local reformulation without any LLM call. ``status`` is carried
        through so the caller (and ultimately the frontend) knows whether the
        LLM optimisation ran, was skipped, or failed over to this."""
        achievements: list[dict] = []
        for exp in profile.experience:
            bullets = []
            for i, ach in enumerate(exp.achievements):
                # Prepend a strong action verb if sentence doesn't already start with one
                if not any(ach.startswith(v) for v in _ACTION_VERBS):
                    verb = _ACTION_VERBS[i % len(_ACTION_VERBS)]
                    ach = f"{verb} {ach[0].lower()}{ach[1:]}" if ach else ach
                bullets.append(ach)
            if not bullets and exp.description:
                bullets.append(exp.description)
            achievements.append({
                "original_role": f"{exp.position} @ {exp.company}",
                "bullets": bullets,
            })

        return {
            "optimized_summary": analysis.get("professional_summary", ""),
            "career_objective": analysis.get("career_objective", ""),
            "optimized_achievements": achievements,
            "additional_skills": [],
            "_status": status,
        }