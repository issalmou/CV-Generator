"""Board-list architecture: bundled file + operator file + env CSV, merged,
de-duplicated (case-insensitively), validated and capped."""

from __future__ import annotations

import json

import pytest

import config as config_mod
from config import settings


@pytest.fixture(autouse=True)
def _bundled_on(monkeypatch):
    # this module *does* exercise the shipped lists — opt back in
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", True)
    for attr in ("GREENHOUSE_BOARDS", "GREENHOUSE_BOARDS_FILE", "LEVER_BOARDS",
                 "ASHBY_BOARDS", "SMARTRECRUITERS_BOARDS", "RIPPLING_BOARDS"):
        monkeypatch.setattr(settings, attr, "")
    config_mod._read_token_file.cache_clear()
    yield
    config_mod._read_token_file.cache_clear()


def test_bundled_lists_load_and_are_non_trivial():
    assert len(settings.greenhouse_boards_list) >= 20
    assert len(settings.lever_boards_list) >= 10
    assert len(settings.ashby_boards_list) >= 10
    assert "stripe" in settings.greenhouse_boards_list
    assert "netflix" in settings.lever_boards_list


def test_bundled_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    assert settings.greenhouse_boards_list == []
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "acme")
    assert settings.greenhouse_boards_list == ["acme"]


def test_env_csv_merges_on_top_of_bundled(monkeypatch):
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "acme-co,widgets-inc")
    lst = settings.greenhouse_boards_list
    assert "stripe" in lst and "acme-co" in lst and "widgets-inc" in lst


def test_dedup_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", "Acme,ACME,acme,acme-2")
    assert settings.greenhouse_boards_list == ["acme", "acme-2"]


def test_smartrecruiters_preserves_case_but_dedups(monkeypatch):
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    monkeypatch.setattr(settings, "SMARTRECRUITERS_BOARDS", "Visa1,visa1,BoschGroup")
    assert settings.smartrecruiters_boards_list == ["Visa1", "BoschGroup"]


def test_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    monkeypatch.setattr(settings, "ATS_MAX_BOARDS", 5)
    monkeypatch.setattr(settings, "GREENHOUSE_BOARDS", ",".join(f"co-{i}" for i in range(50)))
    assert len(settings.greenhouse_boards_list) == 5


def test_operator_file_is_merged(monkeypatch, tmp_path):
    f = tmp_path / "my_boards.txt"
    f.write_text("# my list\nfoo-corp\nbar-inc   # trailing comment\n\nbaz\n", encoding="utf-8")
    config_mod._read_token_file.cache_clear()
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    monkeypatch.setattr(settings, "LEVER_BOARDS_FILE", str(f))
    monkeypatch.setattr(settings, "LEVER_BOARDS", "extra-co")
    assert settings.lever_boards_list == ["foo-corp", "bar-inc", "baz", "extra-co"]


def test_missing_file_contributes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
    monkeypatch.setattr(settings, "ASHBY_BOARDS_FILE", "/no/such/file.txt")
    monkeypatch.setattr(settings, "ASHBY_BOARDS", "real-co")
    assert settings.ashby_boards_list == ["real-co"]


def test_token_file_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("\n# header\n\n  alpha  \nbeta\n#comment\n", encoding="utf-8")
    config_mod._read_token_file.cache_clear()
    assert config_mod._read_token_file(str(f)) == ("alpha", "beta")


def test_phenom_can_load_from_file(monkeypatch, tmp_path):
    entries = [{"company": "Acme", "endpoint": "https://careers.acme.com/widgets", "payload": {}}]
    f = tmp_path / "phenom.json"
    f.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(settings, "PHENOM_BOARDS", "")
    monkeypatch.setattr(settings, "PHENOM_BOARDS_FILE", str(f))
    assert settings.phenom_boards_list == entries


def test_bundled_lists_enable_the_slug_ats_providers():
    """In production config (bundled on, nothing else set) the slug-based ATS
    are enabled; the tenant-specific ones stay disabled."""
    from services.providers.ashby_provider import AshbyProvider
    from services.providers.greenhouse_provider import GreenhouseProvider
    from services.providers.lever_provider import LeverProvider
    from services.providers.rippling_provider import RipplingProvider
    from services.providers.smartrecruiters_provider import SmartRecruitersProvider
    from services.providers.talentbrew_provider import TalentBrewProvider
    from services.providers.workday_provider import WorkdayProvider

    assert GreenhouseProvider().enabled is True
    assert LeverProvider().enabled is True
    assert AshbyProvider().enabled is True
    assert SmartRecruitersProvider().enabled is True
    assert RipplingProvider().enabled is True
    assert WorkdayProvider().enabled is False
    assert TalentBrewProvider().enabled is False


def test_bundled_files_contain_no_traversal_tokens():
    # every shipped token must be a clean slug (defence-in-depth against a
    # bad edit landing in the repo)
    from services.providers.ats_common import valid_slug
    for name in ("greenhouse", "lever", "ashby", "rippling"):
        for tok in config_mod._read_token_file(
            str(config_mod._BUNDLED_BOARDS_DIR / f"{name}.txt")
        ):
            assert valid_slug(tok), f"{name}.txt: {tok!r}"
