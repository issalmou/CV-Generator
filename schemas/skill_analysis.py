"""Skill analysis + recommended actions (Phase 4).

Returned by ``/api/generate-cv`` **only when a job description was supplied**
(Case B). It tells the frontend, for the skills the *job* asks for:

* ``matched``   — the job asks for it AND it is in the candidate's current info;
* ``missing``   — the job asks for it and it is **absent from the info the
  candidate has provided so far**. This does **NOT** mean the candidate cannot
  do it — only that it is not in the CV yet;
* ``uncertain`` — a fuzzy / partial match where the equivalence is not safe to
  assert (e.g. "Postgres" vs "PostgreSQL 14"). Treated as *not confirmed*.

``recommended_actions`` are **suggestions** the frontend can render as buttons —
never actions the backend has performed. Applying anything to the CV always
requires an explicit, confirmed user decision (via the edit agent).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillMatch(BaseModel):
    """One job skill and how it maps onto the candidate's current info."""
    skill: str                       # the job's wording, verbatim
    matched_to: str | None = None    # the candidate's own wording it matched (verbatim, not renamed)
    confidence: str = "exact"        # exact | alias | fuzzy


class SkillAnalysis(BaseModel):
    matched: list[SkillMatch] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)      # job skills not in the CV yet
    uncertain: list[SkillMatch] = Field(default_factory=list)

    @property
    def matched_skills(self) -> list[str]:
        return [m.skill for m in self.matched]


class RecommendedAction(BaseModel):
    """A suggestion, NOT an executed action. Phase 5: recommendations are
    **primarily tied to the missing skills** identified against the job
    description — not a generic list at every generation.

    Common ``type`` values:
      confirm_skill        — a job skill is not in the CV; ask the user if they use it
      provide_skill_context— the user says they use it; ask where/when so it can be added
      review_uncertain_skill — a fuzzy match to disambiguate
    """
    type: str
    skill: str | None = None
    reason: str
    # The exact question to put to the user (rendered as a prompt, not a button).
    question: str | None = None
