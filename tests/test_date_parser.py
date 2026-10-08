"""Unit tests: services.cv.date_parser (local, no LLM, never invents)."""

from __future__ import annotations

from datetime import datetime

import pytest

from services.cv.date_parser import compute_experience_period, resolve_education_dates

YEAR = datetime.now().year


# ---------------------------------------------------------------------------
# EDUCATION — dates kept verbatim, "Présent" is NEVER turned into a year (§8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "start, end, expected",
    [
        ("2022", "2024", ("2022", "2024")),
        ("Septembre 2022", "Juin 2024", ("Septembre 2022", "Juin 2024")),
        ("2022", "Présent", ("2022", "Présent")),
        ("2022", "Present", ("2022", "Present")),
        ("2022", "en cours", ("2022", "Présent")),
        ("Depuis 2022", None, ("2022", "Présent")),
        ("Since 2019", "", ("2019", "Présent")),
        (None, None, (None, None)),
        ("", "  ", (None, None)),
        (None, "2020", (None, "2020")),
    ],
)
def test_resolve_education_dates(start, end, expected):
    assert resolve_education_dates(start, end) == expected


def test_education_present_not_converted_to_year():
    _, end = resolve_education_dates("2025", "Présent")
    assert end == "Présent"
    assert str(YEAR) not in end


# ---------------------------------------------------------------------------
# EXPERIENCE — `period` is a DURATION (§9)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, language, expected",
    [
        # a duration written in the CV is kept as written
        ("Stage de trois mois", "fr", "3 mois"),
        ("Stage de deux mois", "fr", "2 mois"),
        ("Internship - 2 months", "en", "2 months"),
        ("6 months", "en", "6 months"),
        # two calendar dates -> computed
        ("Jan 2023 - Mar 2024", "fr", "1 an 3 mois"),
        ("Jan 2023 - Mar 2024", "en", "1 year 3 months"),
        ("2022 - 2024", "fr", "2 ans"),
        ("2023 - 2024", "fr", "1 an"),
        # nothing temporal -> None
        ("Développement d'une application", "fr", None),
        ("", "fr", None),
        (None, "fr", None),
    ],
)
def test_compute_experience_period(raw, language, expected):
    assert compute_experience_period(raw, language) == expected


def test_ongoing_role_runs_to_today():
    # "2020 - Present" -> a multi-year duration ending now
    out = compute_experience_period("2020 - Present", "en")
    assert out is not None and "year" in out
    out_fr = compute_experience_period("Depuis 2021", "fr")
    assert out_fr is not None and "an" in out_fr


def test_period_never_invents_calendar_dates():
    # a bare duration must never come back as a made-up month range
    out = compute_experience_period("Stage de trois mois", "fr")
    assert out == "3 mois"
    assert "202" not in out
