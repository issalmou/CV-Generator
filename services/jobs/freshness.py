"""
Freshness classification + revalidation.

The database is an index, not a source of truth about liveness. Every read
recomputes freshness from the row's timestamps; a `stale` row can be
revalidated against its provider.

Revalidation is **tri-state**:
- ``gone``    — the provider says the listing 404'd / was removed  -> deactivate.
- ``alive``   — still listed                                       -> bump ``last_verified_at``.
- ``unknown`` — the provider itself errored / was blocked          -> keep the
                previous state, only record the attempt. A temporary outage
                must never turn a job "expired".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config import settings
from models import JobOffer
from schemas.jobs import FreshnessStatus

logger = logging.getLogger(__name__)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify(offer: JobOffer, now: datetime | None = None) -> FreshnessStatus:
    now = now or datetime.now(timezone.utc)
    expires_at = _aware(offer.expires_at)
    verified = _aware(offer.last_verified_at)
    posted = _aware(offer.posted_at)

    if not offer.is_active:
        return FreshnessStatus.expired
    if expires_at is not None and expires_at < now:
        return FreshnessStatus.expired

    if verified is not None:
        age = now - verified
        if age <= timedelta(hours=settings.JOB_FRESH_TTL_HOURS):
            return FreshnessStatus.fresh
        if age <= timedelta(days=settings.JOB_STALE_TTL_DAYS):
            return FreshnessStatus.stale
        # too old to trust and we could not confirm it — treat as expired
        return FreshnessStatus.expired

    if posted is None and expires_at is None:
        return FreshnessStatus.unknown
    return FreshnessStatus.stale


def recompute(offer: JobOffer, now: datetime | None = None) -> FreshnessStatus:
    status = classify(offer, now)
    offer.freshness = status.value
    return status


def revalidate(db, offer: JobOffer, provider) -> FreshnessStatus:
    """Ask the provider whether ``offer`` is still live and persist the outcome.
    Best-effort — never raises."""
    now = datetime.now(timezone.utc)
    offer.last_verification_attempt_at = now
    try:
        result = provider.revalidate(offer)
    except Exception as exc:  # noqa: BLE001 — revalidation must not break a read
        logger.warning("[freshness] revalidate %s failed: %s", offer.id, exc)
        db.commit()
        return recompute(offer, now)

    if result.state == "gone":
        offer.is_active = False
        offer.freshness = FreshnessStatus.expired.value
        logger.info("[freshness] offer %s confirmed gone", offer.id)
    elif result.state == "alive":
        offer.last_verified_at = now
        offer.is_active = True
        if getattr(result, "expires_at", None):
            offer.expires_at = result.expires_at
        offer.freshness = FreshnessStatus.fresh.value
    else:  # unknown — keep previous is_active / expires_at
        recompute(offer, now)

    db.commit()
    return FreshnessStatus(offer.freshness)
