"""
Usage-event recording — privacy-safe, best-effort, never blocks a request.

Records counters + ids only (no search text, no CV content, no message
bodies). ``USAGE_EVENTS_ENABLED`` turns it off entirely; old rows are pruned
after ``USAGE_EVENT_RETENTION_DAYS``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from config import settings
from models import UsageEvent
from models.usage_event import EVENT_KINDS

logger = logging.getLogger(__name__)

_MAX_META_KEYS = 8


def _safe_meta(meta: dict | None) -> dict | None:
    if not isinstance(meta, dict) or not meta:
        return None
    out: dict = {}
    for k, v in list(meta.items())[:_MAX_META_KEYS]:
        if isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[str(k)[:40]] = v
        elif isinstance(v, str):
            out[str(k)[:40]] = v[:60]
    return out or None


def record(db: Session, user_id: str | None, kind: str, meta: dict | None = None) -> None:
    """Fire-and-forget. Any failure is swallowed."""
    if not settings.USAGE_EVENTS_ENABLED or not user_id or kind not in EVENT_KINDS:
        return
    try:
        db.add(UsageEvent(user_id=user_id, kind=kind, meta=_safe_meta(meta)))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — telemetry never breaks a request
        logger.debug("[usage] record failed: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def prune(db: Session) -> int:
    """Delete rows older than the retention window. Returns the count."""
    days = max(1, settings.USAGE_EVENT_RETENTION_DAYS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        res = db.execute(delete(UsageEvent).where(UsageEvent.created_at < cutoff))
        db.commit()
        n = res.rowcount or 0
        if n:
            logger.info("[usage] pruned %d event(s) older than %d days", n, days)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[usage] prune failed: %s", exc)
        db.rollback()
        return 0
