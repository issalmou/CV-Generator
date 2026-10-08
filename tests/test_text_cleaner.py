"""Unit tests: services.cv.text_cleaner (spec §6, §11, §12, §13)."""

from __future__ import annotations

import pytest

from services.cv.text_cleaner import join_wrapped_lines, split_list_items


def test_join_wrapped_sentence():
    src = "Gestion du temps et des\npriorités"
    assert join_wrapped_lines(src) == "Gestion du temps et des priorités"


def test_join_does_not_cross_section_boundary():
    src = "FORMATION\nUniversité X\n2020"
    assert join_wrapped_lines(src) == src  # unchanged


def test_join_keeps_separate_capitalised_items():
    src = "Sport\nMusique\nVoyage"
    assert join_wrapped_lines(src) == src


@pytest.mark.parametrize(
    "src, expected",
    [
        ("Sport\nMusique\nVoyage\nExploration", ["Sport", "Musique", "Voyage", "Exploration"]),
        ("Sport, Musique, Voyage", ["Sport", "Musique", "Voyage"]),
        ("• Sport\n• Musique", ["Sport", "Musique"]),
        (
            "Esprit d'analyse\nGestion du temps et des\npriorités\nTravail en équipe",
            ["Esprit d'analyse", "Gestion du temps et des priorités", "Travail en équipe"],
        ),
        ("", []),
    ],
)
def test_split_list_items(src, expected):
    assert split_list_items(src) == expected


def test_split_list_items_dedupes_and_keeps_order():
    assert split_list_items("Python\nPython\nSQL") == ["Python", "SQL"]
