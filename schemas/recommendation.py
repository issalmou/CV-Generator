"""``IntelligentRecommendation`` — the extensible, contextual recommendation
shape produced by the conversation agent (Phase 7).

Unlike ``schemas.conversation.RecommendedAction`` (closed-ish vocabulary,
kept unchanged for backward compatibility — see that module), ``action``
here is a **free string**: the LLM is not restricted to a fixed enum of
10-15 topics, so a recommendation can concern the CV, a cover letter, the
job search, a specific offer, the user's profile, an application, or the
conversation itself.

This schema is a *proposal* the frontend renders as a suggestion — never an
executed action (see ``services/conversations/recommendation_engine.py``,
which validates/ranks these before they ever reach the API response; there
is no code path anywhere that dispatches on ``action`` to actually run
something).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PRIORITIES = ("low", "medium", "high")


class IntelligentRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Extensible on purpose — e.g. "adapt_cv_to_job", "clarify_user_preference",
    # "analyze_job", "review_application", "widen_search_location", ... Never
    # validated against a closed enum; validated for *safety* and *ownership*
    # instead (services/conversations/recommendation_engine.py).
    action: str
    title: str = ""
    message: str = ""
    reason: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    requires_information: bool = False
    question: str | None = None
    # job | cv | letter | application | search_preferences | conversation | None
    target_type: str | None = None
    target_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def _non_empty_action(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("action must not be empty")
        return v[:80]

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, v: Any) -> str:
        v = str(v or "medium").strip().lower()
        return v if v in PRIORITIES else "medium"


# Strict JSON schema fragment for providers that enforce one — embedded inside
# CONVERSATION_DIRECTIVE_SCHEMA (schemas/conversation_directive.py) so the
# whole ConversationDirective, recommendations included, is still produced by
# exactly ONE call_gemini call per turn.
INTELLIGENT_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "title", "message", "reason", "priority", "confidence",
        "requires_confirmation", "requires_information", "question",
        "target_type", "target_id", "parameters",
    ],
    "properties": {
        "action": {"type": "string"},
        "title": {"type": "string"},
        "message": {"type": "string"},
        "reason": {"type": "string"},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "confidence": {"type": "number"},
        "requires_confirmation": {"type": "boolean"},
        "requires_information": {"type": "boolean"},
        "question": {"type": ["string", "null"]},
        "target_type": {"type": ["string", "null"]},
        "target_id": {"type": ["string", "null"]},
        "parameters": {"type": "object", "additionalProperties": True},
    },
}
