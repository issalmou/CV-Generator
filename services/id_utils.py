"""Shared helpers for business-entity ids (Phase 6 finalisation).

Every business entity (``User``, ``Conversation``, ``JobOffer``,
``JobApplication``, ``SavedJob``, ``GeneratedCV``, ``GeneratedLetter``, ...) is
keyed by a UUID string (``String(36)``, ``default=lambda: str(uuid4())`` — see
the model modules). This module is the one place that validates a
caller-supplied id *looks like* a UUID before it is used to query the
database.

Route path parameters intentionally stay declared as ``str`` (not FastAPI's
built-in ``UUID`` converter): a malformed id has always resolved to the
route's normal "not found" response here (``404``), and changing that to a
blanket ``422`` would be a breaking, undocumented API change for every
existing "unknown id" test. ``require_uuid_or_404`` gives the same up-front
format rejection (never even reaches the database for a clearly-invalid id)
while preserving that exact status code.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status


def is_valid_uuid(value: str | None) -> bool:
    """True only for a syntactically valid UUID string (any version/variant)."""
    if not value or not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def require_uuid_or_404(value: str, *, detail: str = "Not found.") -> str:
    """Return ``value`` unchanged if it is a valid UUID string, else raise the
    same ``404`` the route would have raised on a genuine "no such row"."""
    if not is_valid_uuid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return value
