"""Structured-action contract for the conversation agent (Phase 6).

The non-document turn of `POST /api/conversations/{id}/messages` asks the LLM
for ONE :class:`ConversationDirective` (strict JSON, temperature 0). The backend
then dispatches **deterministically**:

* ``chat``               → just say ``reply``;
* ``search_jobs``        → run ``JobSearchService`` internally, persist the top
  results as the user's selection;
* ``list_jobs``          → (re)show jobs from the search already run — the next
  batch of 5, in order. Never re-runs the search, never re-ranks.
* ``apply_jobs``         → **propose only** — freeze the resolved job set on the
  conversation and ask the user to confirm. The LLM can NEVER trigger an
  application; a separate deterministic "yes" on the next turn does that.
* ``request_information`` → surface ``question`` as a recommended action.

The vocabulary of ``intent`` is closed (same discipline as
``schemas.agent_schemas``): a field the LLM cannot express is a thing it
cannot make the backend do. ``recommendations`` (Phase 7) is the deliberate
exception — each item's ``action`` is a free string, not a closed enum, so
the agent can suggest next steps about any topic it discusses (CV, letter,
job search, a specific offer, the user's profile, an application, or the
conversation itself), not just job-search actions. Safety does not come
from restricting the vocabulary here: every recommendation is validated
(ownership, state-consistency, a safety denylist) and ranked/capped by
``services/conversations/recommendation_engine.py`` before it ever reaches
an API response — never executed directly, never by the LLM.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from schemas.recommendation import INTELLIGENT_RECOMMENDATION_SCHEMA, IntelligentRecommendation

INTENTS = ("chat", "search_jobs", "list_jobs", "apply_jobs", "request_information")
APPLY_SCOPES = ("all", "ids", "filter")


class ApplyDirective(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scope: str = "ids"                 # all | ids | filter
    job_ids: list[str] = Field(default_factory=list)
    filter: str | None = None          # keyword to match against selected job titles

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, v: Any) -> str:
        v = str(v or "ids").strip().lower()
        return v if v in APPLY_SCOPES else "ids"

    @field_validator("job_ids", mode="before")
    @classmethod
    def _ids(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


class QuestionDirective(BaseModel):
    model_config = ConfigDict(extra="ignore")
    topic: str = ""
    question: str = ""
    reason: str = ""


class ConversationDirective(BaseModel):
    """One turn's proposed action. ``intent="chat"`` = no action, just reply."""

    model_config = ConfigDict(extra="ignore")

    reply: str = ""
    intent: str = "chat"
    # a partial JobSearchContext — only the fields the user just stated.
    search_patch: dict[str, Any] | None = None
    apply: ApplyDirective | None = None
    question: QuestionDirective | None = None
    # Phase 7 — 0-3 contextual next-step suggestions, same call, any topic.
    # Raw LLM output; never trusted as-is — see recommendation_engine.validate_and_rank.
    recommendations: list[IntelligentRecommendation] = Field(default_factory=list)

    @field_validator("intent", mode="before")
    @classmethod
    def _intent(cls, v: Any) -> str:
        v = str(v or "chat").strip()
        return v if v in INTENTS else "chat"

    @field_validator("recommendations", mode="before")
    @classmethod
    def _recommendations(cls, v: Any) -> list:
        """Audit fix: a single malformed item (out-of-range confidence, a
        missing required field, a bare ``null`` in the array, ...) used to
        raise ``ValidationError`` here, which propagates out of
        ``ConversationDirective.model_validate`` and fails the ENTIRE
        directive — discarding the turn's intent/search_patch/apply along
        with it, not just the one bad recommendation. Recommendations are a
        best-effort addition; one bad item must degrade to "drop it", never
        to "fail the whole message"."""
        if not isinstance(v, list):
            return []
        out: list[IntelligentRecommendation] = []
        for item in v[:20]:
            try:
                out.append(IntelligentRecommendation.model_validate(item))
            except (ValidationError, ValueError, TypeError):
                continue
            if len(out) >= 10:   # generous upper bound before validate_and_rank caps to 3
                break
        return out


# Strict JSON schema for providers that enforce one (mirrors AGENT_ACTION_SCHEMA).
CONVERSATION_DIRECTIVE_SCHEMA: dict[str, Any] = {
    "name": "conversation_directive",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply", "intent", "search_patch", "apply", "question", "recommendations"],
        "properties": {
            "reply": {"type": "string"},
            "intent": {"type": "string", "enum": list(INTENTS)},
            "recommendations": {
                "type": "array",
                "maxItems": 3,
                "description": "0-3 contextual next-step suggestions, ranked by usefulness. "
                               "action is a free label (not a fixed enum) — cover the topic "
                               "actually relevant right now (CV, letter, job search, a "
                               "specific offer, the user's profile, an application, or the "
                               "conversation itself), never a generic fixed list.",
                "items": INTELLIGENT_RECOMMENDATION_SCHEMA,
            },
            "search_patch": {
                "type": ["object", "null"],
                "description": "partial job-search preferences the user just stated "
                               "(query, location, remote_type, job_type, skills, "
                               "experience_level, salary_min, …)",
                "additionalProperties": True,
            },
            "apply": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["scope", "job_ids", "filter"],
                "properties": {
                    "scope": {"type": "string", "enum": list(APPLY_SCOPES)},
                    "job_ids": {"type": "array", "items": {"type": "string"}},
                    "filter": {"type": ["string", "null"]},
                },
            },
            "question": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["topic", "question", "reason"],
                "properties": {
                    "topic": {"type": "string"},
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}
