"""Phase 16 — ``to_utc`` across every date shape the providers actually see.
A missing date is always preferred over a fabricated one."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.providers.parsing import to_utc

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("value, expected_date", [
    ("2026-08-20T09:00:00Z", (2026, 8, 20)),
    ("2026-08-20T09:00:00.000Z", (2026, 8, 20)),
    ("2026-08-20T09:00:00+02:00", (2026, 8, 20)),
    ("2026-08-20 09:00:00", (2026, 8, 20)),
    ("2026-08-20", (2026, 8, 20)),
    ("Wed, 20 Aug 2026 09:00:00 +0000", (2026, 8, 20)),
    ("Aug 20, 2026", (2026, 8, 20)),
    ("20 August 2026", (2026, 8, 20)),
    ("20/08/2026", (2026, 8, 20)),
])
def test_absolute_formats(value, expected_date):
    dt = to_utc(value)
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == expected_date
    assert dt.tzinfo is not None


@pytest.mark.parametrize("value", [1788300000, 1788300000.0, 1788300000000, "1788300000"])
def test_epoch_seconds_and_millis(value):
    dt = to_utc(value)
    assert dt is not None and dt.year == 2026


@pytest.mark.parametrize("value, approx_days_ago", [
    ("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 5 Days Ago", 5),
    ("Posted 30+ Days Ago", 30), ("3 weeks ago", 21), ("2 months ago", 60),
    ("il y a 3 jours", 3), ("il y a 2 semaines", 14),
])
def test_explicit_relative_dates(value, approx_days_ago):
    dt = to_utc(value)
    assert dt is not None
    delta = (NOW - dt).total_seconds() / 86400
    assert abs(delta - approx_days_ago) < 1.5


@pytest.mark.parametrize("value", [
    None, "", 0, "recently", "new", "n/a", "not a date", "posted", "-",
    "2099-01-01",          # implausible future -> data error
    "1998-06-01",          # before year 2000 -> data error
    "9" * 20,              # nonsense
    "0000-00-00",
])
def test_unreliable_values_return_none(value):
    assert to_utc(value) is None


def test_naive_datetime_gets_utc():
    dt = to_utc(datetime(2026, 8, 20, 12, 0, 0))
    assert dt is not None and dt.tzinfo == timezone.utc


def test_future_within_skew_is_kept():
    # a timezone-skewed "posted just now" a few hours ahead is fine
    soon = NOW + timedelta(hours=6)
    assert to_utc(soon.isoformat()) is not None
