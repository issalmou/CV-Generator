"""Schemas for ``/api/generate-cv``, ``/api/generate-letter`` and the
``/api/cvs`` · ``/api/letters`` management routes.

The generation endpoints no longer stream a PDF — they persist it to MinIO
and return JSON metadata plus a short-lived presigned download URL.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from schemas.skill_analysis import RecommendedAction, SkillAnalysis


class GenerateCVJsonResponse(BaseModel):
    status: str = "success"
    generated_cv_id: UUID
    # Stable document reference (e.g. "CV_A8F42K") — unchanged across versions.
    reference: str | None = None
    version: int = 1
    ats_score: float | None = None
    language: str
    filename: str
    download_url: str
    expires_in: int
    # Job keywords the candidate does NOT have — SUGGESTIONS shown separately by
    # the frontend, never written into the CV, never presented as owned skills
    # (constraint #6). The user may later accept them via the UI / edit agent.
    additional_skills: list[str] = []
    # ATS content-optimisation outcome (constraint #10):
    #   applied         — the LLM optimisation ran and validated
    #   local_fallback  — LLM output failed validation -> safe local reformulation
    #   skipped         — no job description / already well matched
    #   cache           — reused from a previous identical run
    ats_optimization: str = "skipped"
    # CV one-page layout outcome (constraint #7). fit_one_page=False means the
    # content could not fit one page above the readability floor and flowed to
    # `pages` pages — NOTHING was dropped.
    layout: dict = {}
    # Phase 4 — skill analysis vs the job description. **null when no
    # job_description was sent** (Case A: a general CV, no targeting).
    #   matched   — job skills present in the CV
    #   missing   — job skills absent from the info provided SO FAR (NOT "cannot do")
    #   uncertain — fuzzy matches to confirm
    skill_analysis: SkillAnalysis | None = None
    # Suggestion buttons for the frontend — never actions already performed.
    recommended_actions: list[RecommendedAction] = []


class GenerateLetterJsonResponse(BaseModel):
    status: str = "success"
    generated_letter_id: UUID
    reference: str | None = None
    version: int = 1
    language: str
    filename: str
    download_url: str
    expires_in: int


class CVListItem(BaseModel):
    id: UUID
    reference: str | None = None
    version: int = 1
    filename: str
    language: str
    ats_score: float | None = None
    created_at: datetime


class LetterListItem(BaseModel):
    id: UUID
    reference: str | None = None
    version: int = 1
    filename: str
    language: str
    created_at: datetime


class DocumentVersionItem(BaseModel):
    """One version of a document (CV or letter), newest listed last."""
    id: UUID
    reference: str
    version: int
    language: str
    filename: str
    ats_score: float | None = None
    created_at: datetime
    is_latest: bool = False


class DownloadUrlResponse(BaseModel):
    status: str = "success"
    download_url: str
    expires_in: int
