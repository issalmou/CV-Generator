"""
CV Assistant - CV Generation
Service: ConfidenceScorer

Responsibilities:
- Compute a 0-100 confidence score per resume section, based purely on
  local signals: field completeness, validation issues raised by
  ResumeValidator, and presence/quality of raw source text.
- 100% local. NEVER calls Gemini / any LLM.

The score is a heuristic, not a statistical model — it's meant to give the
caller (and ultimately the end user) a quick signal of "how much should I
trust this section", not a precise probability.
"""

import logging
from typing import Any

from services.cv.resume_validator import ValidationReport

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """Computes local confidence scores (0-100) for each resume section."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def score_contact(self, contact: dict[str, Any], report: ValidationReport) -> int:
        """
        Score the contact section.

        Weights: name (30), email (30), phone (15), linkedin (10),
        github (10), portfolio (5). Penalised by validation errors/warnings
        on contact fields.
        """
        weights = {
            "name": 30, "email": 30, "phone": 15,
            "linkedin": 10, "github": 10, "portfolio": 5,
        }
        score = sum(weight for field_, weight in weights.items() if contact.get(field_))
        score = self._apply_issue_penalty(score, report, fields=set(weights.keys()))
        return self._clamp(score)

    def score_experience(self, experience: list[dict[str, Any]], report: ValidationReport) -> int:
        """Score the experience section based on field completeness across all entries."""
        if not experience:
            return 0
        return self._score_entry_list(
            experience,
            report,
            section_prefix="experience",
            # `period` is expected for a real role (a bare duration such as
            # "3 mois" counts) — its absence should visibly lower the score.
            required_fields=("company", "position", "period"),
            optional_fields=("location", "description", "achievements", "technologies"),
        )

    def score_education(self, education: list[dict[str, Any]], report: ValidationReport) -> int:
        """Score the education section based on field completeness across all entries."""
        if not education:
            return 0
        return self._score_entry_list(
            education,
            report,
            section_prefix="education",
            required_fields=("institution", "degree"),
            optional_fields=("field", "start_date", "end_date", "gpa", "location"),
        )

    def score_projects(self, projects: list[dict[str, Any]], report: ValidationReport) -> int:
        """Score the projects section based on field completeness across all entries."""
        if not projects:
            return 0
        return self._score_entry_list(
            projects,
            report,
            section_prefix="projects",
            required_fields=("title", "description"),
            optional_fields=("technologies", "github", "demo"),
        )

    def score_skills(self, skills: list[str]) -> int:
        """
        Score the skills section from the size of the flat skill list.
        (`skills` is now ``list[str]`` — no categories.)
        """
        total_skills = len(skills or [])
        if total_skills == 0:
            return 0
        if total_skills < 3:
            return 60
        if total_skills < 6:
            return 85
        return 97

    def compute_all(
        self,
        *,
        contact: dict[str, Any],
        experience: list[dict[str, Any]],
        education: list[dict[str, Any]],
        projects: list[dict[str, Any]],
        skills: list[str],
        report: ValidationReport,
    ) -> dict[str, int]:
        """Convenience method returning all confidence scores in one call."""
        return {
            "contact_confidence": self.score_contact(contact, report),
            "experience_confidence": self.score_experience(experience, report),
            "education_confidence": self.score_education(education, report),
            "projects_confidence": self.score_projects(projects, report),
            "skills_confidence": self.score_skills(skills),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _score_entry_list(
        self,
        entries: list[dict[str, Any]],
        report: ValidationReport,
        *,
        section_prefix: str,
        required_fields: tuple[str, ...],
        optional_fields: tuple[str, ...],
    ) -> int:
        per_entry_scores: list[int] = []
        required_weight = 70
        optional_weight = 30

        for entry in entries:
            required_present = sum(1 for f in required_fields if entry.get(f))
            optional_present = sum(1 for f in optional_fields if entry.get(f))

            required_score = (required_present / len(required_fields)) * required_weight
            optional_score = (
                (optional_present / len(optional_fields)) * optional_weight
                if optional_fields else 0
            )
            per_entry_scores.append(required_score + optional_score)

        avg_score = sum(per_entry_scores) / len(per_entry_scores) if per_entry_scores else 0

        # Penalise based on validation issues whose field path starts with this section
        error_count = sum(
            1 for issue in report.issues
            if issue.field.startswith(section_prefix) and issue.severity == "error"
        )
        warning_count = sum(
            1 for issue in report.issues
            if issue.field.startswith(section_prefix) and issue.severity == "warning"
        )

        avg_score -= error_count * 10
        avg_score -= warning_count * 3

        return self._clamp(round(avg_score))

    @staticmethod
    def _apply_issue_penalty(
        score: float, report: ValidationReport, *, fields: set[str]
    ) -> float:
        """Subtract a fixed penalty per validation issue whose field is in ``fields``."""
        for issue in report.issues:
            if issue.field in fields:
                if issue.severity == "error":
                    score -= 15
                elif issue.severity == "warning":
                    score -= 5
        return score

    @staticmethod
    def _clamp(value: float) -> int:
        return max(0, min(100, int(round(value))))
