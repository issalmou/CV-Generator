"""
LOT 7 — cover-letter cache. Key = hash(cv_profile + job_description + language
+ LETTER_PROMPT_VERSION). Identical regeneration -> zero LLM calls; a change of
language or prompt version -> a MISS.
"""

from __future__ import annotations

import pytest

from cv_models import GenerateLetterRequest
from services import gemini_client
from services.cache_service import cache
from services.generation_service import _letter_cache_key, run_letter_pipeline


@pytest.fixture
def letter_request(cv_profile_dict):
    return GenerateLetterRequest(
        cv_profile=cv_profile_dict["cv_profile"],
        job_description="Backend Engineer at Globex — Python, FastAPI.",
        language="en",
    )


def _llm_calls(mock_llm):
    return list(mock_llm.calls)


def test_identical_regeneration_is_a_cache_hit(mock_llm, letter_request):
    cache.clear()
    r1 = run_letter_pipeline(letter_request, "L1")
    n_after_first = len(mock_llm.calls)
    assert n_after_first > 0
    assert r1.from_cache is False

    r2 = run_letter_pipeline(letter_request, "L2")
    assert r2.pdf_bytes == r1.pdf_bytes
    assert r2.from_cache is True
    assert r2.structured == r1.structured
    assert len(mock_llm.calls) == n_after_first     # no new LLM calls


def test_language_change_is_a_miss(mock_llm, cv_profile_dict):
    cache.clear()
    en = GenerateLetterRequest(cv_profile=cv_profile_dict["cv_profile"],
                               job_description="JD", language="en")
    fr = GenerateLetterRequest(cv_profile=cv_profile_dict["cv_profile"],
                               job_description="JD", language="fr")
    assert _letter_cache_key(en) != _letter_cache_key(fr)

    run_letter_pipeline(en, "L1")
    n = len(mock_llm.calls)
    run_letter_pipeline(fr, "L2")
    assert len(mock_llm.calls) > n                   # fr regenerated


def test_prompt_version_bump_invalidates(mock_llm, letter_request, monkeypatch):
    cache.clear()
    from config import settings
    k1 = _letter_cache_key(letter_request)
    monkeypatch.setattr(settings, "LETTER_PROMPT_VERSION", 999)
    k2 = _letter_cache_key(letter_request)
    assert k1 != k2


def test_recipient_override_changes_key(cv_profile_dict):
    base = GenerateLetterRequest(cv_profile=cv_profile_dict["cv_profile"],
                                 job_description="JD", language="en")
    with_recipient = GenerateLetterRequest(cv_profile=cv_profile_dict["cv_profile"],
                                           job_description="JD", language="en",
                                           recipient_name="Ms. Dupont")
    assert _letter_cache_key(base) != _letter_cache_key(with_recipient)
