"""
LOT 1 — structured output + validation + one repair retry + anti-hallucination.

Covers:
- ``parser_common.request_structured_json``: happy path, single repair retry,
  double-failure -> ``StructuredOutputError`` carrying the raw text;
- ``call_gemini`` threads ``json_schema`` / ``temperature`` and folds them into
  the prompt cache key;
- provider ``_supported_response_format`` capability downgrade;
- the P1/P3/P5 prompt hardening (no JD-keyword injection, derived-metric ban,
  language directive, chat anti-fabrication rules);
- parsers salvage the model's own words on validation failure — never fabricate.
"""

from __future__ import annotations

import json

import pytest

from schemas.llm_schemas import json_schema_for, validator_for
from services import gemini_client
from services.parser_common import StructuredOutputError, request_structured_json


# ---------------------------------------------------------------------------
# request_structured_json
# ---------------------------------------------------------------------------

def _patch_calls(monkeypatch, responses):
    """Make call_gemini return successive canned strings; record kwargs."""
    seen: list[dict] = []
    it = iter(responses)

    def fake(prompt, *, request_type="generic", use_cache=True, json_schema=None, temperature=None):
        seen.append({"prompt": prompt, "request_type": request_type, "use_cache": use_cache,
                     "json_schema": json_schema, "temperature": temperature})
        return next(it)

    monkeypatch.setattr(gemini_client, "call_gemini", fake)
    import services.parser_common as pc  # request_structured_json imports it locally
    return seen


def test_structured_happy_path(monkeypatch):
    seen = _patch_calls(monkeypatch, [json.dumps({"skills": ["Python", "SQL"]})])
    out = request_structured_json(
        "prompt", request_type="resume_parse_skills",
        validator=validator_for("resume_parse_skills"),
        json_schema=json_schema_for("resume_parse_skills"),
    )
    assert out.skills == ["Python", "SQL"]
    assert len(seen) == 1
    assert seen[0]["temperature"] == 0.0
    assert seen[0]["json_schema"]["name"] == "skills"


def test_structured_single_repair_retry(monkeypatch):
    seen = _patch_calls(monkeypatch, ["not json at all", json.dumps({"skills": ["Go"]})])
    out = request_structured_json(
        "prompt", request_type="resume_parse_skills",
        validator=validator_for("resume_parse_skills"),
    )
    assert out.skills == ["Go"]
    assert len(seen) == 2
    assert "YOUR PREVIOUS RESPONSE WAS REJECTED" in seen[1]["prompt"]
    assert seen[1]["use_cache"] is False   # repair never reads/writes cache


def test_structured_double_failure_raises_with_raw(monkeypatch):
    _patch_calls(monkeypatch, ["still not json", "<html>nope</html>"])
    with pytest.raises(StructuredOutputError) as ei:
        request_structured_json(
            "prompt", request_type="resume_parse_skills",
            validator=validator_for("resume_parse_skills"),
        )
    assert ei.value.raw == "<html>nope</html>"


def test_structured_no_repair_flag(monkeypatch):
    seen = _patch_calls(monkeypatch, ["bad"])
    with pytest.raises(StructuredOutputError):
        request_structured_json(
            "p", request_type="resume_parse_skills",
            validator=validator_for("resume_parse_skills"), repair=False,
        )
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# call_gemini cache key
# ---------------------------------------------------------------------------

def test_cache_key_varies_with_schema_and_temperature():
    k_plain = gemini_client._cache_key("p", request_type="x")
    k_schema = gemini_client._cache_key("p", request_type="x", json_schema={"name": "s"})
    k_temp = gemini_client._cache_key("p", request_type="x", temperature=0.0)
    k_rt = gemini_client._cache_key("p", request_type="y")
    assert len({k_plain, k_schema, k_temp, k_rt}) == 4


# ---------------------------------------------------------------------------
# provider capability downgrade
# ---------------------------------------------------------------------------

def test_response_format_downgrade():
    from services.llm.providers import _supported_response_format

    schema = {"type": "json_schema", "json_schema": {"name": "x"}}
    obj = {"type": "json_object"}
    # schema-capable model keeps json_schema
    assert _supported_response_format("openai/gpt-oss-120b", schema) == schema
    # unknown model downgrades json_schema -> json_object
    assert _supported_response_format("some-new-model", schema) == obj
    # nemotron (no JSON mode) drops it entirely
    assert _supported_response_format("nvidia/nemotron-3-super-120b-a12b", schema) is None
    assert _supported_response_format("nvidia/nemotron-3-super-120b-a12b", obj) is None
    # no request -> nothing
    assert _supported_response_format("anything", None) is None


# ---------------------------------------------------------------------------
# P1 — ATS optimisation prompt no longer tells the model to inject JD keywords
# ---------------------------------------------------------------------------

def test_ats_prompt_forbids_injecting_missing_keywords(mock_llm, cv_profile_dict):
    from cv_models import ATSAnalysis, CVProfile
    from services.cv.ats_optimizer import ATSOptimizer

    profile = CVProfile(**cv_profile_dict["cv_profile"])
    analysis = {"professional_summary": "Backend engineer.", "career_objective": ""}
    ats = ATSAnalysis(missing_keywords=["Kubernetes", "Terraform"],
                      keywords_to_highlight=["Python"], ats_score=40.0)
    ATSOptimizer().optimize_content(profile, analysis, ats,
                                    "We need Kubernetes and Terraform.", language="en")
    _, prompt = mock_llm.prompts[-1]
    low = prompt.lower()
    assert "integrate missing keywords" not in low
    assert "must not write any of these" in low
    assert "additional_skills" in low
    assert "disqualifying fabrication" in low
    # Phase 5 — the prompt now also forbids cross-section repetition + stuffing
    assert "no repetition" in low and "without stuffing" in low


def test_ats_status_surfaced(mock_llm, cv_profile_dict):
    from cv_models import ATSAnalysis, CVProfile
    from services.cv.ats_optimizer import ATS_APPLIED, ATS_LOCAL_FALLBACK, ATSOptimizer

    profile = CVProfile(**cv_profile_dict["cv_profile"])
    ats = ATSAnalysis(missing_keywords=["Kafka"], ats_score=30.0)
    ok = ATSOptimizer().optimize_content(profile, {"professional_summary": "x"}, ats,
                                         "Kafka role", language="en")
    assert ok["_status"] == ATS_APPLIED

    mock_llm.set(lambda p, *, request_type, use_cache, **kw: "not json")
    bad = ATSOptimizer().optimize_content(profile, {"professional_summary": "x"}, ats,
                                          "Kafka role", language="en")
    assert bad["_status"] == ATS_LOCAL_FALLBACK
    assert bad["additional_skills"] == []


# ---------------------------------------------------------------------------
# P3 — profile analysis: language directive + derived-metric ban + safe fallback
# ---------------------------------------------------------------------------

def test_profile_analysis_prompt_hardening(mock_llm, cv_profile_dict):
    from cv_models import CVProfile
    from services.cv.profile_analyzer import ProfileAnalyzer

    profile = CVProfile(**cv_profile_dict["cv_profile"])
    ProfileAnalyzer().analyze_profile(profile, language="fr")
    _, prompt = mock_llm.prompts[-1]
    assert "French" in prompt
    low = prompt.lower()
    assert "800ms -> 210ms" in low and ("74% faster" in low or "~74%" in low)
    assert "never derive a number the candidate did not state" in low
    assert "anti-fabrication" in low


def test_profile_analysis_local_fallback_is_conservative(mock_llm, cv_profile_dict):
    from cv_models import CVProfile
    from services.cv.profile_analyzer import ProfileAnalyzer

    mock_llm.set(lambda p, *, request_type, use_cache, **kw: "garbage")
    profile = CVProfile(**cv_profile_dict["cv_profile"])
    out = ProfileAnalyzer().analyze_profile(profile)
    assert out["seniority_level"] == ""
    assert out["years_of_experience"] == 0
    assert out["strengths"] == []


# ---------------------------------------------------------------------------
# P5 — conversation agent anti-fabrication system prompt
# ---------------------------------------------------------------------------

def test_conversation_agent_prompt_has_anti_fabrication_rules():
    from services.conversations.job_agent_service import _ANTI_FAB

    low = " ".join(_ANTI_FAB.lower().split())
    assert "anti-fabrication" in low
    assert "never automatically one of the user's skills" in low
    assert "you propose; the user decides" in low
    assert "request_information" in low


# ---------------------------------------------------------------------------
# parser salvage — no fabrication on validation failure
# ---------------------------------------------------------------------------

def test_experience_parser_accepts_prose_wrapped_json(mock_llm):
    from services.cv.experience_parser import ExperienceParser

    mock_llm.set(lambda p, *, request_type, use_cache, **kw:
                 'here you go: {"experience": [{"company": "Acme"}]} thanks')
    out = ExperienceParser().parse("Acme Corp, Engineer, 2021", language="en")
    assert out and out[0]["company"] == "Acme"
    assert out[0]["position"] is None   # normalised, not fabricated


def test_experience_parser_returns_empty_on_total_garbage(mock_llm):
    from services.cv.experience_parser import ExperienceParser

    mock_llm.set(lambda p, *, request_type, use_cache, **kw: "the model refused")
    out = ExperienceParser().parse("Acme Corp, Engineer, 2021", language="en")
    assert out == []   # never fabricated
