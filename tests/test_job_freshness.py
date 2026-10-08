"""Freshness classification + tri-state revalidation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models import JobOffer
from schemas.jobs import FreshnessStatus
from services.jobs.freshness import classify, recompute, revalidate
from services.providers.base import RevalidationResult

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _offer(**kw):
    defaults = dict(
        source="linkedin", source_job_id="1", content_hash="h" + str(id(kw)),
        source_url="https://x/1", title="DS", is_active=True,
        first_seen_at=NOW, scraped_at=NOW, last_verified_at=NOW, freshness="fresh",
    )
    defaults.update(kw)
    return JobOffer(**defaults)


def test_fresh_when_recently_verified():
    o = _offer(last_verified_at=NOW - timedelta(hours=2))
    assert classify(o, NOW) == FreshnessStatus.fresh


def test_stale_when_verification_is_old():
    o = _offer(last_verified_at=NOW - timedelta(days=3))
    assert classify(o, NOW) == FreshnessStatus.stale


def test_expired_when_past_expires_at():
    o = _offer(expires_at=NOW - timedelta(days=1))
    assert classify(o, NOW) == FreshnessStatus.expired


def test_expired_when_inactive():
    o = _offer(is_active=False)
    assert classify(o, NOW) == FreshnessStatus.expired


def test_expired_when_verification_too_old_to_trust():
    o = _offer(last_verified_at=NOW - timedelta(days=40))
    assert classify(o, NOW) == FreshnessStatus.expired


def test_unknown_without_any_dates():
    o = _offer(last_verified_at=None, posted_at=None, expires_at=None)
    assert classify(o, NOW) == FreshnessStatus.unknown


# ---------------------------------------------------------------------------
# revalidation
# ---------------------------------------------------------------------------

class _Provider:
    def __init__(self, state):
        self._state = state
    def revalidate(self, offer):
        return RevalidationResult(self._state)


def test_revalidate_gone_deactivates(db):
    o = _offer(last_verified_at=NOW - timedelta(days=3))
    db.add(o)
    db.commit()
    status = revalidate(db, o, _Provider("gone"))
    assert status == FreshnessStatus.expired
    assert o.is_active is False
    assert o.last_verification_attempt_at is not None


def test_revalidate_alive_refreshes(db):
    o = _offer(last_verified_at=NOW - timedelta(days=3))
    db.add(o)
    db.commit()
    status = revalidate(db, o, _Provider("alive"))
    assert status == FreshnessStatus.fresh
    assert o.is_active is True


def test_revalidate_unknown_keeps_previous_state(db):
    """A temporary provider outage must NOT kill a job."""
    o = _offer(last_verified_at=NOW - timedelta(days=3), is_active=True)
    db.add(o)
    db.commit()
    revalidate(db, o, _Provider("unknown"))
    assert o.is_active is True                       # not deactivated
    assert o.freshness in ("stale", "expired")       # recomputed, not forced
    assert o.last_verification_attempt_at is not None


def test_revalidate_provider_exception_is_swallowed(db):
    o = _offer()
    db.add(o)
    db.commit()

    class _Boom:
        def revalidate(self, offer):
            raise RuntimeError("provider down")

    status = revalidate(db, o, _Boom())
    assert isinstance(status, FreshnessStatus)
    assert o.is_active is True


def test_recompute_writes_freshness_field():
    o = _offer(last_verified_at=NOW - timedelta(days=3))
    recompute(o, NOW)
    assert o.freshness == "stale"
