"""
CV Assistant - CV Generation
Shared helpers for the section-level resume parsers.

`services/experience_parser.py`, `services/education_parser.py` and
`services/project_parser.py` each turn ONE already-split resume section
into structured JSON via a single focused LLM call. They share:

- the hard anti-hallucination contract embedded in every prompt,
- the FR/EN language directive,
- the defensive JSON-response parser.

Keeping these here (rather than duplicating them in three modules) means
the no-fabrication rules are defined in exactly one place and can be
audited at a glance.

None of this module calls the LLM directly — the parsers do, via
``services.gemini_client.call_gemini``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

T = TypeVar("T")

# Single source of truth for the anti-hallucination rules injected into
# every section-parser prompt. Changing the contract means changing it
# here, once. The wording is deliberately blunt and repetitive — it is the
# single most important instruction in the whole extraction pipeline
# (spec: FIDELITY TO SOURCE > COMPLETENESS > INTERPRETATION).
ANTI_HALLUCINATION_RULES: str = """
ABSOLUTE RULES — READ TWICE:
- You must extract ONLY information explicitly supported by the provided text.
- Never infer, guess, complete, or invent missing information.
- If a value is not explicitly available, return null (or [] for lists).
- Do NOT use world knowledge.
- Do NOT infer dates from context.
- Do NOT infer locations from institution or company names.
- Do NOT infer technologies from job titles or descriptions.
- Do NOT add a framework/library just because it is commonly used with a
  technology that IS mentioned.
- Do NOT invent, round, or DERIVE a number. If the text says "from 800ms to
  210ms", keep that wording — never turn it into "~74% faster" or "big
  improvement". A percentage/KPI/headcount/amount that is not written does
  not exist.
- Copy every metric, date and proper noun (company, school, tool, technology
  names) EXACTLY as written — do not reformat, normalise or translate them.
- It is always better to return null than a value that is not clearly in
  the text.
- Return raw JSON only: no markdown, no code fences, no commentary.

COMPLETENESS (without inventing):
- Extract EVERY entry that is genuinely in the section, even a messy or
  partial one — a missing field on a real entry is null, but do not drop the
  entry. Fidelity first, completeness second, interpretation never.

REGLES ABSOLUES (francais) :
- N'extraire QUE ce qui est explicitement present dans le texte fourni.
- Ne jamais deviner, completer, deduire ou inventer (y compris un pourcentage
  ou un chiffre calcule a partir d'autres chiffres).
- Si une valeur n'est pas explicitement disponible : retourner null.
- Ne pas deduire une date, une localisation ou une technologie du contexte.
- Mieux vaut null qu'une valeur incorrecte, mais ne pas omettre une entree reelle.
"""

# Human-readable language names, used to build the response-language
# directive. Kept minimal on purpose — the pipeline only supports fr/en.
LANGUAGE_NAMES: dict[str, str] = {"fr": "French", "en": "English"}

# Injected into every prompt that returns a technology / tool list
# (experience, projects, skills). Two jobs: (1) keep each item a separate
# string, (2) repair names the PDF extractor split across a line break.
TECH_LIST_RULES: str = """
TECHNOLOGY / TOOL LISTS:
- Return every technology, tool, framework, library or language as its
  OWN separate string. Never put several into one string
  ("Python, React" is wrong; ["Python", "React"] is right).
- Never create sub-groups/categories inside a technology list.
- Repair names the PDF extractor split across a line break, using the
  surrounding context: "scikit-\\nlearn" -> "scikit-learn",
  "Open\\nCV" -> "OpenCV", "Power\\nBI" -> "Power BI",
  "React\\nJS" -> "React JS". Do NOT merge two words when that would
  change the meaning — only rejoin an obviously-split single name.
- Only list technologies that literally appear in the given text. Never
  add a technology the candidate did not write.
"""


def language_directive(language: str) -> str:
    """
    Build the one-line directive that tells the model which language to
    write any *free-text* field in (description, achievements).

    The candidate's own facts are never translated — only prose the model
    itself writes (e.g. a normalised one-line role description) follows
    this directive. Proper nouns stay untouched (also enforced by
    :data:`ANTI_HALLUCINATION_RULES`).
    """
    name = LANGUAGE_NAMES.get(language, "English")
    return (
        f"OUTPUT LANGUAGE: write any free-text field (description, "
        f"achievements) in {name}. Keep proper nouns (company, school, "
        f"tool and technology names) and all dates exactly as written."
    )


def strip_code_fences(raw: str) -> str:
    """Remove a leading ```json / ``` fence and a trailing ``` fence, if present."""
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


class StructuredOutputError(RuntimeError):
    """The LLM's JSON output failed schema validation on the first attempt AND
    after one repair retry. Raised so the caller decides — never a silent
    fabrication. ``raw`` is the last response text (for an explicit,
    logged lenient-salvage attempt by the caller — salvaging the model's own
    words is not fabrication)."""

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def _loads_lenient(raw: str) -> Any:
    """``json.loads`` after stripping code fences and any prose around the
    first ``{`` / last ``}`` (some models still wrap JSON in a sentence)."""
    clean = strip_code_fences(raw)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    start = clean.find("{")
    end = clean.rfind("}")
    if 0 <= start < end:
        return json.loads(clean[start:end + 1])
    raise json.JSONDecodeError("no JSON object found", clean, 0)


def request_structured_json(
    prompt: str,
    *,
    request_type: str,
    validator: Callable[[Any], T],
    json_schema: dict | None = None,
    use_cache: bool = True,
    repair: bool = True,
) -> T:
    """Run a structured LLM call and return the *validated* object.

    1. one call with ``temperature=0`` and the strict ``json_schema`` enforced
       when the routed model supports it;
    2. ``validator(parsed_json)`` — a Pydantic ``model_validate`` callable that
       raises ``ValidationError`` / ``ValueError`` on a bad shape;
    3. on failure, exactly ONE repair retry: the same prompt plus the concrete
       validation error and a reminder that missing data stays ``null`` / ``[]``
       (never invent a value to satisfy the schema);
    4. still invalid -> :class:`StructuredOutputError` (the caller chooses a
       safe, explicitly-logged fallback — never fabricated data).

    Import is local to avoid a module-load cycle with ``gemini_client``.
    """
    from services.gemini_client import call_gemini

    raw = call_gemini(prompt, request_type=request_type, use_cache=use_cache,
                      json_schema=json_schema, temperature=0.0)
    first_err: Exception
    try:
        return validator(_loads_lenient(raw))
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as first:
        first_err = first
        if not repair:
            raise StructuredOutputError(f"{request_type}: {first}", raw=raw) from first

    repair_prompt = (
        f"{prompt}\n\n"
        "=== YOUR PREVIOUS RESPONSE WAS REJECTED ===\n"
        f"It did not match the required JSON schema. Error:\n{first_err}\n\n"
        "Return ONLY corrected raw JSON that matches the schema exactly. "
        "Do NOT add, infer or invent any value to satisfy a required field — "
        "use null (or [] for lists) for anything not explicitly in the source. "
        "No markdown, no code fences, no commentary."
    )
    raw2 = call_gemini(repair_prompt, request_type=request_type, use_cache=False,
                       json_schema=json_schema, temperature=0.0)
    try:
        return validator(_loads_lenient(raw2))
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as second:
        raise StructuredOutputError(
            f"{request_type}: invalid JSON after one repair retry ({second})",
            raw=raw2 or raw,
        ) from second


def parse_list_response(
    raw: str,
    *,
    expected_key: str,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """
    Parse a ``{"<expected_key>": [ {...}, ... ]}`` LLM response defensively.

    Accepts three shapes for robustness against model drift:
    1. ``{"<expected_key>": [...]}``  (the requested shape)
    2. a bare list ``[...]``
    3. anything else -> logged and treated as "nothing found"

    On ANY parsing failure this returns ``[]`` rather than raising or
    fabricating — an empty section is always safer than a hallucinated
    entry.
    """
    clean = strip_code_fences(raw)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning(
            "[parser] JSON parse failed for '%s' - returning empty list. raw=%r",
            expected_key,
            raw[:300],
        )
        return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get(expected_key, [])
        if not isinstance(items, list):
            logger.warning(
                "[parser] Expected a list for key '%s', got %s - returning empty list.",
                expected_key,
                type(items).__name__,
            )
            return []
    else:
        logger.warning(
            "[parser] Unexpected JSON root type for '%s': %s",
            expected_key,
            type(data).__name__,
        )
        return []

    clean_items = [item for item in items if isinstance(item, dict)]
    dropped = len(items) - len(clean_items)
    if dropped:
        logger.warning(
            "[parser] Dropped %d malformed (non-object) entr%s from '%s'.",
            dropped,
            "y" if dropped == 1 else "ies",
            expected_key,
        )
    return clean_items
