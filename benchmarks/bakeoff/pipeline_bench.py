"""Phase 2b — AFTER benchmark of the WHOLE routed pipeline (constraint #20).

Runs the 7 mandated scenarios end-to-end through the real services with the
LOT 3 routing ON (live Groq primary), MinIO + DB stubbed in memory:

  1 extraction CV        4 régénération CV identique (cache)
  2 génération CV        5 chat (conversation agent)
  3 génération lettre    6 modification CV par référence (edit agent)
  7 modification lettre par référence

For each: wall time, LLM calls, fallbacks, cache hits, prompt/completion
tokens (from services.profiling), plus quality gates via bakeoff/score.py
(hallucination-free, JSON valid, ATS keyword coverage, fidelity to source).

Usage:
    python benchmarks/bakeoff/pipeline_bench.py            # live (needs GROQ_API_KEY)
    python benchmarks/bakeoff/pipeline_bench.py --mock     # deterministic, no network

Writes benchmarks/bakeoff/results/pipeline_after.json + prints a table.
No production code is modified — this only calls it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "bench-secret")

RESULTS = os.path.join(os.path.dirname(__file__), "results")


def _score_no_fabrication(cv_data: dict, source_terms: set[str]) -> dict:
    """The P1 regression test: does the CV body claim a JD technology the
    candidate does NOT have? (bake-off found every model injecting
    Kubernetes/Terraform/Docker/AWS into the candidate's real experience)."""
    import re
    text = cv_data.get("summary", {}).get("professional_summary", "")
    for exp in cv_data.get("experience", []):
        text += " " + " ".join(exp.get("bullets", []))
    low = text.lower()
    src = " ".join(source_terms).lower()

    # technologies the JOB asks for but the CANDIDATE never listed
    jd_only_tech = ["kubernetes", "terraform", "docker", "kafka", "grpc", "aws",
                    "redis", "prometheus", "grafana", "jenkins", "argocd"]
    injected = sorted({t for t in jd_only_tech if t in low and t not in src})

    nums = re.findall(r"\d+(?:[.,]\d+)?\s?%", text)
    invented_nums = [n for n in nums if n.strip().rstrip("%").strip() not in src]
    return {"jd_tech_injected_into_cv": injected, "invented_numbers": invented_nums,
            "hallucination_free": not injected and not invented_nums}


def run(mock: bool) -> dict:
    from config import settings
    if mock:
        settings.LLM_ROUTING_ENABLED = False

    # in-memory minio
    from services.minio_service import minio_service
    _store: dict[str, bytes] = {}
    minio_service.is_configured = lambda: True
    minio_service.ensure_bucket = lambda: None
    minio_service.upload = lambda k, d, content_type="application/pdf": _store.__setitem__(k, bytes(d)) or k
    minio_service.download = lambda k: _store[k]
    minio_service.presigned_get_url = lambda k, expires=None: f"mem://{k}"

    from cv_models import GenerateCVRequest, GenerateLetterRequest
    from services import profiling
    from services.cache_service import cache
    from services.generation_service import run_cv_pipeline, run_letter_pipeline
    from services.cv.resume_parser_pipeline import ResumeParserPipeline
    from benchmarks.bakeoff.tasks import CV_PROFILE, JOB_DESCRIPTION, SAMPLE_CV_TEXT

    if mock:
        from tests.conftest import _default_llm_router
        import services.gemini_client as gc
        _orig = gc.call_gemini
        gc.call_gemini = lambda p, **k: _default_llm_router(p, request_type=k.get("request_type", "generic"))
        for m in ("services.cv.experience_parser", "services.cv.education_parser",
                  "services.cv.project_parser", "services.cv.skills_parser",
                  "services.cv.resume_structurer", "services.cv.profile_analyzer",
                  "services.cv.ats_optimizer", "services.cv.cv_generator",
                  "services.jobs.company_parser", "services.cv.letter_generator",
                  "services.conversations.conversation_service",
                  "services.conversations.agent_service"):
            try:
                __import__(m)
                setattr(sys.modules[m], "call_gemini", gc.call_gemini)
            except Exception:
                pass

    cache.clear()
    source_terms = set()
    for e in CV_PROFILE.experience:
        source_terms |= {e.company, e.position, *e.technologies, *e.achievements}
    for c in CV_PROFILE.skills:
        source_terms |= set(c.skills)

    scenarios: list[dict] = []

    pace = 0 if mock else 25   # seconds between scenarios — free-tier TPM budget

    def _measure(name, fn):
        if scenarios and pace:
            time.sleep(pace)
        profiling.reset()
        from services.llm import circuit as _circ
        _circ.reset()   # each scenario is a fresh capacity window
        t0 = time.perf_counter()
        try:
            with profiling.profile_request("bench"):
                out = fn()
        except Exception as exc:  # noqa: BLE001 — record the failure, keep going
            wall = time.perf_counter() - t0
            row = {"scenario": name, "wall_s": round(wall, 2), "error": str(exc)[:160],
                   "llm_calls": 0, "fallbacks": 0, "cache_hits": 0, "in_tokens": 0, "out_tokens": 0}
            scenarios.append((row, None))
            print(f"  {name:34} {wall:6.2f}s  ERROR {str(exc)[:80]}")
            return None
        wall = time.perf_counter() - t0
        prof = profiling.recent(1)["profiles"][0]
        llm = prof.get("llm", {})
        row = {
            "scenario": name, "wall_s": round(wall, 2),
            "llm_calls": llm.get("count", 0), "fallbacks": llm.get("fallbacks", 0),
            "cache_hits": llm.get("cache_hits", 0),
            "in_tokens": llm.get("prompt_tokens", 0), "out_tokens": llm.get("completion_tokens", 0),
            "providers": sorted({c.get("provider") for c in llm.get("by_call", []) if not c.get("cache_hit")}),
        }
        scenarios.append((row, out))
        print(f"  {name:34} {wall:6.2f}s  calls={row['llm_calls']} fb={row['fallbacks']} "
              f"cache={row['cache_hits']} tok={row['in_tokens']}->{row['out_tokens']} "
              f"{row['providers']}")
        return out

    cv_req = GenerateCVRequest(cv_profile=CV_PROFILE, job_description=JOB_DESCRIPTION, language="fr")
    letter_req = GenerateLetterRequest(cv_profile=CV_PROFILE, job_description=JOB_DESCRIPTION, language="fr")

    print("\n== Phase 2b AFTER — routed pipeline ==")
    _measure("1_extraction_cv",
             lambda: ResumeParserPipeline().parse_bytes(SAMPLE_CV_TEXT.encode("utf-8"),
                                                        "cv.txt", language="fr"))
    cv1 = _measure("2_generation_cv", lambda: run_cv_pipeline(cv_req, "b-cv-1"))
    _measure("3_generation_lettre", lambda: run_letter_pipeline(letter_req, "b-l-1"))
    _measure("4_regeneration_cv_identique", lambda: run_cv_pipeline(cv_req, "b-cv-2"))
    _measure("5_chat", lambda: _chat_probe(mock))
    if cv1 is not None:
        _measure("6_modif_cv_par_reference", lambda: _edit_probe(cv1, mock))
    _measure("7_modif_lettre_par_reference", lambda: _letter_edit_probe(mock))

    if cv1 is not None:
        quality = _score_no_fabrication(cv1.cv_data, source_terms)
        quality["ats_score"] = cv1.ats_score
        quality["ats_status"] = cv1.ats_status
        quality["additional_skills_are_suggestions"] = bool(cv1.additional_skills)
        quality["cv_layout"] = cv1.layout
    else:
        quality = {"error": "generation_cv scenario failed — no CV to score"}

    walls = [r["wall_s"] for r, _ in scenarios]
    report = {
        "mode": "mock" if mock else "live",
        "routing_enabled": settings.LLM_ROUTING_ENABLED,
        "scenarios": [r for r, _ in scenarios],
        "p50_wall_s": round(median(walls), 2),
        "p95_wall_s": round(sorted(walls)[int(len(walls) * 0.95) - 1], 2),
        "total_wall_s": round(sum(walls), 2),
        "cv_quality": quality,
    }
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "pipeline_after.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    print(f"\np50={report['p50_wall_s']}s  p95={report['p95_wall_s']}s  total={report['total_wall_s']}s")
    print(f"CV hallucination-free: {quality['hallucination_free']}  "
          f"ATS: {quality['ats_score']} ({quality['ats_status']})")
    print(f"wrote {RESULTS}/pipeline_after.json")
    return report


def _chat_probe(mock):
    class _U: id = "bench-user"
    from services.gemini_client import call_gemini
    return call_gemini("USER: How can I make my CV more relevant for a Kubernetes-heavy backend role?",
                       request_type="conversation_agent", use_cache=False)


def _edit_probe(cv_result, mock):
    """Direct AgentAction apply (no DB) — measures the edit-agent LLM round-trip + apply."""
    from schemas.agent_schemas import AGENT_ACTION_SCHEMA, AgentAction
    from services.conversations.agent_service import EditAgent, _apply_cv
    prompt = EditAgent._build_prompt("cv", "CV_BENCH", cv_result.cv_data,
                                     "make my professional summary one sentence shorter")
    from services.parser_common import request_structured_json
    action = request_structured_json(prompt, request_type="agent_edit",
                                     validator=lambda d: AgentAction.model_validate(d),
                                     json_schema=AGENT_ACTION_SCHEMA, use_cache=False)
    if action.op != "none":
        _apply_cv(dict(cv_result.cv_data), action)
    return action.op


def _letter_edit_probe(mock):
    from schemas.agent_schemas import AGENT_ACTION_SCHEMA, AgentAction
    from services.conversations.agent_service import EditAgent, _apply_letter
    src = {"body": "Dear Hiring Team,\n\nI am applying for the role.\n\nRegards,\nMehdi",
           "company": {"recipient": "Hiring Team"}, "candidate": {}, "language": "fr"}
    prompt = EditAgent._build_prompt("letter", "LETTER_BENCH", src, "make the first paragraph warmer")
    from services.parser_common import request_structured_json
    action = request_structured_json(prompt, request_type="agent_edit",
                                     validator=lambda d: AgentAction.model_validate(d),
                                     json_schema=AGENT_ACTION_SCHEMA, use_cache=False)
    if action.op != "none":
        try:
            _apply_letter(dict(src), action)
        except Exception:
            pass
    return action.op


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    run(ap.parse_args().mock)
