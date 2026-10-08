"""Schemas for ``/api/conversations`` — persistent conversational memory.

Every route is owner-scoped (``current_user.id``); none of these models ever
carry a client-supplied ``user_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.dashboard_jobs import ApplyOutcome, SelectedJobOut
from schemas.recommendation import IntelligentRecommendation


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    id: UUID
    title: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    total: int
    limit: int
    offset: int
    conversations: list[ConversationOut]


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class MessageList(BaseModel):
    total: int
    limit: int
    offset: int
    messages: list[MessageOut]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class EditedDocument(BaseModel):
    """Set when the edit agent created a new version of a document this turn."""
    reference: str
    version: int
    kind: str            # "cv" | "letter"
    download_url: str


class RecommendedAction(BaseModel):
    """A suggestion the frontend renders as a button / prompt — NEVER an
    executed action. Phase 6: contextual, from the full vocabulary, emitted
    only when it has real value (`services/recommendations.py`):

      review_match · improve_summary · review_experience · confirm_skill ·
      review_uncertain_skill · generate_targeted_cv · generate_cover_letter ·
      review_cover_letter · review_selected_jobs · apply_to_selected_jobs ·
      review_job · request_information

    Applying anything always needs an explicit user confirmation."""
    type: str
    skill: str | None = None
    reason: str
    question: str | None = None       # the exact question to put to the user
    job_ids: list[str] = Field(default_factory=list)   # for job-scoped actions


class PendingConfirmation(BaseModel):
    """An action the agent PROPOSED and froze — a deterministic "yes" on the
    next turn executes exactly this. The LLM cannot trigger it. ``job_ids`` are
    always already-resolved real job offer ids at this point (ordinals like
    "1" are resolved to a real id before a proposal is ever returned)."""
    kind: str                         # "apply"
    job_ids: list[UUID] = Field(default_factory=list)
    summary: str = ""


class MessageSendResponse(BaseModel):
    conversation_id: UUID
    user_message: MessageOut
    assistant_message: MessageOut
    # non-null when the message named a CV_/LETTER_ reference and the edit
    # agent applied a change (LOT 9)
    document: EditedDocument | None = None
    # Phase 4 — the agent proposes; the user decides. Kept as-is (Phase 6
    # closed-ish vocabulary) — still populated by the deterministic
    # apply-proposal / request_information paths, unrelated to §recommendations.
    recommended_actions: list[RecommendedAction] = []
    # Phase 7 — intelligent, contextual, any-topic suggestions (additive,
    # extensible `action`, validated + ranked, 0-3). See
    # services/conversations/recommendation_engine.py.
    recommendations: list[IntelligentRecommendation] = []
    # Phase 6 — populated on a job-search turn: the jobs now on the dashboard.
    jobs_found: list[SelectedJobOut] = []
    # Phase 6 — populated after a CONFIRMED "apply to selected jobs": one row per job.
    applications: list[ApplyOutcome] = []
    # Phase 6 — set when the agent proposed an apply and is waiting for a yes.
    pending_confirmation: PendingConfirmation | None = None
