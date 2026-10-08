"""
LOT 5 — profile_analysis || ats_keyword_extraction run concurrently in
run_cv_pipeline. Both keep their own profiling span; the result is identical
to the sequential version; a hidden dependency would show up as a wrong result.
"""

from __future__ import annotations

import json
import time

from cv_models import GenerateCVRequest
from services import profiling
from services.generation_service import _analysis_and_keywords_parallel, run_cv_pipeline


def test_both_spans_recorded_and_result_matches_sequential(mock_llm, cv_profile_dict):
    req = GenerateCVRequest(**cv_profile_dict)

    with profiling.profile_request("cv_generation"):
        result = run_cv_pipeline(req, "cv-par-1")

    spans = {s["name"] for s in profiling.recent(1)["profiles"][0]["spans"]}
    assert result.pdf_bytes[:4] == b"%PDF"
    assert {"profile_analysis", "keyword_extraction", "matching", "ats_optimization"} <= spans


def test_runs_concurrently_not_serially(mock_llm, cv_profile_dict):
    """Each LLM call sleeps 0.3s; parallel wall time must be well under 0.6s."""
    def _slow_router(prompt, *, request_type, use_cache=True, **kw):
        from tests.conftest import _default_llm_router
        time.sleep(0.3)
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)

    mock_llm.set(_slow_router)
    profile = GenerateCVRequest(**cv_profile_dict).cv_profile

    t0 = time.perf_counter()
    an, kw = _analysis_and_keywords_parallel(profile, "Python backend role", "en")
    elapsed = time.perf_counter() - t0

    assert an and kw
    assert elapsed < 0.55, f"expected concurrent (~0.3s), got {elapsed:.2f}s"


def test_parallel_result_equals_manual_sequential(mock_llm, cv_profile_dict):
    from services.generation_service import _cached_keywords, _cached_profile_analysis

    profile = GenerateCVRequest(**cv_profile_dict).cv_profile
    par_an, par_kw = _analysis_and_keywords_parallel(profile, "Python role", "en")
    seq_an = _cached_profile_analysis(profile, "en")
    seq_kw = _cached_keywords("Python role", "en")
    assert par_an == seq_an
    assert par_kw == seq_kw
