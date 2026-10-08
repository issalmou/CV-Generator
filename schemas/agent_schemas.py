"""Structured-action contract for the conversational EDIT AGENT (LOT 9).

The LLM never touches the database or the PDF. It only proposes ONE
:class:`AgentAction` (strict JSON schema, temperature 0). The backend then
validates it, applies it deterministically to ``structured_source``, re-renders
the PDF and saves a new version.

A limited, well-defined vocabulary (constraint #18) — anything not listed here
cannot be expressed, so the agent cannot do something unexpected.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# CV actions
CV_ACTIONS = (
    "set_summary", "set_career_objective",
    "add_skill", "remove_skill", "rename_skill_category", "accept_suggested_skill",
    "add_bullet", "edit_bullet", "remove_bullet", "reorder_experience",
    "set_language",
)
# cover-letter actions
LETTER_ACTIONS = ("set_letter_body", "replace_letter_paragraph", "set_recipient")

ALL_ACTIONS = CV_ACTIONS + LETTER_ACTIONS + ("none",)


class AgentAction(BaseModel):
    """One proposed edit. ``op="none"`` = no change (just reply / ask)."""

    model_config = ConfigDict(extra="ignore")

    op: str = "none"
    # free-text payloads
    value: str | None = None            # summary / objective / bullet text / body / language / category
    category: str | None = None         # skill category (add_skill / rename target)
    skill: str | None = None            # skill name (add/remove/accept)
    from_name: str | None = None        # rename_skill_category source
    to_name: str | None = None          # rename_skill_category dest
    role_index: int | None = None       # which experience (0-based)
    bullet_index: int | None = None     # which bullet within the role (0-based)
    order: list[int] = Field(default_factory=list)   # reorder_experience
    recipient: str | None = None        # set_recipient
    reply: str = ""                     # a short human sentence to show the user

    @field_validator("op", mode="before")
    @classmethod
    def _known_op(cls, v: Any) -> str:
        v = str(v or "none").strip()
        return v if v in ALL_ACTIONS else "none"

    def touches_facts(self) -> str:
        """The candidate-facing text this action would introduce — what the
        anti-hallucination guard scans."""
        bits = [self.value or "", self.skill or "", self.recipient or ""]
        return " ".join(b for b in bits if b)


AGENT_ACTION_SCHEMA: dict[str, Any] = {
    "name": "agent_action",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "op", "value", "category", "skill", "from_name", "to_name",
            "role_index", "bullet_index", "order", "recipient", "reply",
        ],
        "properties": {
            "op": {"type": "string", "enum": list(ALL_ACTIONS)},
            "value": {"type": ["string", "null"]},
            "category": {"type": ["string", "null"]},
            "skill": {"type": ["string", "null"]},
            "from_name": {"type": ["string", "null"]},
            "to_name": {"type": ["string", "null"]},
            "role_index": {"type": ["integer", "null"]},
            "bullet_index": {"type": ["integer", "null"]},
            "order": {"type": "array", "items": {"type": "integer"}},
            "recipient": {"type": ["string", "null"]},
            "reply": {"type": "string"},
        },
    },
}
