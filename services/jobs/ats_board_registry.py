"""
ATS board resolution — the file/env board lists **plus** the superadmin-managed
``ats_boards`` DB overlay.

``resolved_tokens(provider, base_tokens)`` = ``base_tokens`` (from
``data/ats_boards/*.txt`` + ``<PROVIDER>_BOARDS*``) with the DB rows applied:
enabled rows are added, disabled rows are removed. Deduped case-insensitively
and capped at ``ATS_MAX_BOARDS``.

Design constraints honoured:
- best-effort: a DB error (or the table not existing yet) → the base list,
  unchanged. The provider never fails because of this.
- cheap: the overlay is cached (``ATS_BOARD_OVERLAY_TTL``, default 60 s) so a
  search does not hit the DB once per provider. ``invalidate()`` is called on
  every admin write.
- providers stay DB-free in spirit: this is the one module that reads the
  overlay, and only for configuration.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from config import settings
from services.cache_service import cache

logger = logging.getLogger(__name__)

_CACHE_KEY = "ats:board_overlay"


def _load_overlay() -> dict[str, dict[str, list[str]]]:
    """``{provider: {"add": [...], "remove": [...]}}`` from the DB. Cached."""
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    overlay: dict[str, dict[str, list[str]]] = {}
    try:
        from sqlalchemy import select

        from database import SessionLocal
        from models import AtsBoard

        with SessionLocal() as db:
            for row in db.scalars(select(AtsBoard)):
                slot = overlay.setdefault(row.provider, {"add": [], "remove": []})
                (slot["add"] if row.enabled else slot["remove"]).append(row.token)
    except Exception as exc:  # noqa: BLE001 — overlay is optional, never fatal
        logger.debug("[ats_boards] overlay unavailable (%s) — using file/env only", exc)
        overlay = {}

    cache.set(_CACHE_KEY, overlay, ttl=settings.ATS_BOARD_OVERLAY_TTL)
    return overlay


def invalidate() -> None:
    cache.delete(_CACHE_KEY)


def resolved_tokens(
    provider: str,
    base_tokens: list[str],
    validator: Callable[[str], bool] | None = None,
) -> list[str]:
    """Merge the file/env ``base_tokens`` for ``provider`` with the DB overlay."""
    overlay = _load_overlay().get(provider, {})
    remove = {t.lower() for t in overlay.get("remove", [])}

    merged: list[str] = list(base_tokens) + list(overlay.get("add", []))
    out: list[str] = []
    seen: set[str] = set()
    for token in merged:
        t = (token or "").strip()
        low = t.lower()
        if not t or low in seen or low in remove:
            continue
        if validator is not None and not validator(t):
            continue
        seen.add(low)
        out.append(t)
        if len(out) >= settings.ATS_MAX_BOARDS:
            break
    return out
