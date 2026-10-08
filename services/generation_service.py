"""
Service: generation orchestration.

This module owns the shared service singletons and the CV / cover-letter /
extraction pipelines. It was split out of ``main.py`` so ``cv_router`` can
call the pipelines without importing ``main`` (which would be circular,
since ``main`` includes ``cv_router``).

Application cache (``services.cache_service.cache``) is applied here, at
coarse semantic boundaries, ABOVE ``gemini_client``'s own prompt cache:

    run_extraction   -> whole extraction result, keyed on file bytes + language
    run_cv_pipeline  -> profile analysis / ATS keywords / content optimisation,
                        each cached on a hash of their semantic inputs

``calculate_match_score`` and every ``render_to_bytes`` call are pure-local
and never cached. ``gemini_client.py`` is untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from fastapi.responses import JSONResponse

import services.gemini_client as gemini_client
from cv_models import ATSAnalysis, GenerateCVRequest, GenerateLetterRequest
from services import profiling
from services.cv.ats_optimizer import ATSOptimizer
from services.cache_service import cache
from services.cv.cv_generator import CVGenerator
from services.jobs.company_parser import JobCompanyParser
from services.cv.letter_generator import LetterGenerator
from services.cv.letter_pdf_generator import LetterPDFGenerator
from services.minio_service import minio_service
from services.cv.pdf_generator import PDFGenerator
from services.cv.profile_analyzer import ProfileAnalyzer
from services.cv.resume_parser_pipeline import ResumeParseResult, ResumeParserPipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service instances (stateless, thread-safe)
# ---------------------------------------------------------------------------

_pipeline = ResumeParserPipeline()
_profile_analyzer = ProfileAnalyzer()
_ats_optimizer = ATSOptimizer()
_cv_generator = CVGenerator()
_pdf_generator = PDFGenerator()
_job_company_parser = JobCompanyParser()
_letter_generator = LetterGenerator()
_letter_pdf_generator = LetterPDFGenerator()


# ---------------------------------------------------------------------------
# Availability guards (shared by the routers)
# ---------------------------------------------------------------------------


def require_llm() -> JSONResponse | None:
    """503 JSON response when the LLM backend has no API key; ``None`` otherwise."""
    if gemini_client.is_configured():
        return None
    logger.error("[Generation] LLM backend not configured (NVIDIA_API_KEY missing).")
    return JSONResponse(
        status_code=503,
        content={"status": "error", "message": "LLM API key not configured."},
    )


def require_minio() -> JSONResponse | None:
    """503 JSON response when object storage is not configured; ``None`` otherwise."""
    if minio_service.is_configured():
        return None
    logger.error("[Generation] MinIO not configured (MINIO_ENDPOINT missing).")
    return JSONResponse(
        status_code=503,
        content={"status": "error", "message": "Object storage is not configured."},
    )


def llm_error_response(exc: Exception, *, trace_id: str) -> JSONResponse | None:
    """
    Map a known pipeline exception to the project's standard error response,
    or ``None`` when the caller should fall through to its own 500.

    429 — LLM rate limit reached / no model available (``RuntimeError``)
    500 — file-system / PDF rendering error (``OSError``)
    """
    if isinstance(exc, RuntimeError):
        logger.error("[Generation] Rate limit / LLM unavailable | id=%s | %s", trace_id, exc)
        return JSONResponse(status_code=429, content={"status": "error", "message": str(exc)})
    if isinstance(exc, OSError):
        logger.error("[Generation] File system / render error | id=%s | %s", trace_id, exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Failed to render PDF output."},
        )
    return None


# ---------------------------------------------------------------------------
# Extraction (cache-wrapped)
# ---------------------------------------------------------------------------


def run_extraction(content: bytes, filename: str, language: str | None) -> ResumeParseResult:
    """
    Parse an uploaded résumé. Identical bytes + language served from the
    application cache — zero LLM calls on a repeat.
    """
    file_sha = hashlib.sha256(content).hexdigest()
    with profiling.profile_request("extraction", request_id=file_sha[:12]):
        key = cache.key("extract", file_sha, language or "auto")

        cached = cache.get(key)
        if cached is not None:
            logger.info("[Generation] extraction cache HIT | key=%s", key)
            profiling.record_semantic_cache_hit("extraction_full", 0.0)
            return ResumeParseResult.from_dict(cached)

        with profiling.span("pipeline_parse_bytes"):
            result = _pipeline.parse_bytes(content, filename, language=language)
        cache.set(key, result.to_dict())
        return result


# ---------------------------------------------------------------------------
# CV generation pipeline (cache-wrapped LLM steps)
# ---------------------------------------------------------------------------


def _cached_profile_analysis(profile, language: str) -> dict[str, Any]:
    _t0 = time.perf_counter()
    key = cache.key("profile", "analysis", cache.digest(profile.model_dump()), language)
    cached = cache.get(key)
    if cached is not None:
        logger.info("[Generation] profile-analysis cache HIT")
        profiling.record_semantic_cache_hit("profile_analysis", (time.perf_counter() - _t0) * 1000.0)
        return cached
    analysis = _profile_analyzer.analyze_profile(profile, language=language)
    cache.set(key, analysis)
    return analysis


def _cached_keywords(job_description: str, language: str) -> dict[str, list[str]]:
    if not job_description.strip():
        return _ats_optimizer.extract_keywords(job_description)
    _t0 = time.perf_counter()
    key = cache.key("ats", "keywords", cache.digest(job_description), language)
    cached = cache.get(key)
    if cached is not None:
        logger.info("[Generation] ats-keywords cache HIT")
        profiling.record_semantic_cache_hit("ats_keyword_extraction", (time.perf_counter() - _t0) * 1000.0)
        return cached
    keywords = _ats_optimizer.extract_keywords(job_description)
    cache.set(key, keywords)
    return keywords


def _cached_optimized_content(
    profile, profile_analysis, ats_analysis, job_description: str, language: str
) -> dict[str, Any]:
    _t0 = time.perf_counter()
    key = cache.key(
        "ats",
        "optimize",
        cache.digest(profile.model_dump()),
        cache.digest(job_description),
        language,
    )
    cached = cache.get(key)
    if cached is not None:
        logger.info("[Generation] content-optimisation cache HIT")
        profiling.record_semantic_cache_hit("ats_content_optimization", (time.perf_counter() - _t0) * 1000.0)
        # the content is real prior-run output; tell the frontend it was reused
        if cached.get("_status") in (None, "applied"):
            cached = {**cached, "_status": "cache"}
        return cached
    optimized = _ats_optimizer.optimize_content(
        profile, profile_analysis, ats_analysis, job_description, language=language
    )
    cache.set(key, optimized)
    return optimized


@dataclass
class CVPipelineResult:
    """Everything a CV generation produced. Replaces the old
    ``(pdf_bytes, ats_score, ats_analysis)`` tuple so ``additional_skills``
    (suggestions — never in the PDF), the ATS optimisation ``ats_status`` and
    the assembled ``cv_data`` (the structured source of truth for versioning /
    the edit agent) travel with it."""
    pdf_bytes: bytes
    ats_score: float
    ats_analysis: ATSAnalysis
    additional_skills: list[str] = field(default_factory=list)
    ats_status: str = "skipped"
    cv_data: dict[str, Any] = field(default_factory=dict)
    layout: dict[str, Any] = field(default_factory=dict)   # LOT 6 — adaptive fit info
    # Phase 4 — populated ONLY when a job description was supplied (Case B).
    skill_analysis: dict[str, Any] | None = None            # {matched, missing, uncertain}
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)  # suggestions only


def _analysis_and_keywords_parallel(profile, job_description: str, language: str):
    """Run ``_cached_profile_analysis`` and ``_cached_keywords`` concurrently.

    They are genuinely independent — one reads the profile, the other the job
    description, and neither consumes the other's output (the join point is
    ``calculate_match_score``). ``profiling.bind`` re-applies the request
    profile inside each worker so their spans + ``call_gemini`` calls still
    attach to this run. Worker exceptions (rate limit, no model) propagate
    unchanged — the fast-path is not a place to swallow errors."""
    def _analysis():
        with profiling.span("profile_analysis"):
            return _cached_profile_analysis(profile, language)

    def _keywords():
        with profiling.span("keyword_extraction"):
            return _cached_keywords(job_description, language)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cvpipe") as ex:
        f_a = ex.submit(profiling.bind(_analysis))
        f_k = ex.submit(profiling.bind(_keywords))
        # .result() re-raises any worker exception here, after both have settled
        return f_a.result(), f_k.result()


def run_cv_pipeline(request: GenerateCVRequest, cv_id: str) -> CVPipelineResult:
    """Full CV pipeline. Returns a :class:`CVPipelineResult`."""
    profile = request.cv_profile
    job_description = request.job_description or ""
    language = request.language
    logger.info("[Pipeline] START | cv_id=%s | candidate=%s | language=%s", cv_id, profile.name, language)

    # LOT 5 — profile_analysis and ats_keyword_extraction are independent
    # (one reads the profile, the other the job description; neither reads the
    # other's output). They run concurrently; `matching` below is the join
    # point that first needs both. Each keeps its own profiling span.
    profile_analysis, keywords = _analysis_and_keywords_parallel(
        profile, job_description, language
    )
    with profiling.span("matching"):
        ats_analysis = _ats_optimizer.calculate_match_score(profile, keywords, profile_analysis)
    with profiling.span("ats_optimization"):
        optimized_content = _cached_optimized_content(
            profile, profile_analysis, ats_analysis, job_description, language
        )
    with profiling.span("cv_assembly"):
        cv_data = _cv_generator.generate_cv(
            profile, profile_analysis, ats_analysis, optimized_content, language=language
        )
    with profiling.span("pdf_rendering"):
        pdf_bytes = _pdf_generator.render_to_bytes(cv_data, language=language)

    # Phase 4 — Case B: a job description was given -> explicit skill analysis.
    # Case A (no JD): NO analysis, no invented targeting.
    # Phase 6 — recommendations are contextual (services/recommendations.py), no
    # longer a fixed list. The DB-aware parts (does a matching letter exist?) are
    # added by the API layer, which has the request's session; here we compute
    # the JD-only ones the pipeline can determine on its own.
    skill_analysis = None
    recommended = []
    if job_description.strip():
        from services.cv import skill_analysis as _sa
        from services.recommendations import RecommendationContext, build as _build_reco
        with profiling.span("skill_analysis"):
            analysis = _sa.analyze(profile, keywords)
        skill_analysis = analysis.model_dump()
        recommended = [a.model_dump() for a in _build_reco(RecommendationContext(
            has_job_description=True,
            ats_score=ats_analysis.ats_score,
            skill_analysis=analysis,
            summary_text=cv_data.get("summary", {}).get("professional_summary", ""),
            experience_bullet_counts=[len(e.get("bullets") or []) for e in cv_data.get("experience", [])],
        ))]

    logger.info(
        "[Pipeline] DONE | cv_id=%s | ats_score=%.1f | pdf_bytes=%d | ats_status=%s | "
        "suggested=%d | matched=%s missing=%s",
        cv_id, ats_analysis.ats_score, len(pdf_bytes),
        cv_data.get("ats_optimization"), len(cv_data.get("additional_skills", [])),
        len(skill_analysis["matched"]) if skill_analysis else 0,
        len(skill_analysis["missing"]) if skill_analysis else 0,
    )
    return CVPipelineResult(
        pdf_bytes=pdf_bytes,
        ats_score=ats_analysis.ats_score,
        ats_analysis=ats_analysis,
        additional_skills=list(cv_data.get("additional_skills", [])),
        ats_status=cv_data.get("ats_optimization", "skipped"),
        cv_data=cv_data,
        layout=dict(cv_data.get("_layout", {})),
        skill_analysis=skill_analysis,
        recommended_actions=recommended,
    )


def render_cv_from_structured(cv_data: dict, language: str) -> bytes:
    """Re-render a CV PDF from an edited ``structured_source`` (the edit agent
    path — LOT 9). No LLM call: the structured source IS the truth."""
    return _pdf_generator.render_to_bytes(cv_data, language=language)


async def run_cv_pipeline_async(request: GenerateCVRequest, cv_id: str) -> CVPipelineResult:
    loop = asyncio.get_event_loop()
    # profiling.bind re-applies the router's RequestProfile inside the worker
    # thread so the pipeline's spans + call_gemini calls attach to it.
    return await loop.run_in_executor(
        None, profiling.bind(partial(run_cv_pipeline, request, cv_id))
    )


# ---------------------------------------------------------------------------
# Cover-letter pipeline
# ---------------------------------------------------------------------------


def _letter_cache_key(request: GenerateLetterRequest) -> str:
    """hash(cv_profile + job_description + language + prompt version) —
    a regenerated identical letter is served from cache; bumping
    ``LETTER_PROMPT_VERSION`` invalidates every entry (constraint #12)."""
    from config import settings as _s
    return cache.key(
        "letter", "v", str(_s.LETTER_PROMPT_VERSION),
        cache.digest(request.cv_profile.model_dump()),
        cache.digest(request.job_description),
        request.language,
        cache.digest({"r": request.recipient_name or "", "a": request.company_address or ""}),
    )


@dataclass
class LetterPipelineResult:
    """Rendered PDF + the structured source (the letter body + addressing
    context) the edit agent needs — the PDF is only a render of this."""
    pdf_bytes: bytes
    structured: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False


def run_letter_pipeline(request: GenerateLetterRequest, letter_id: str) -> LetterPipelineResult:
    """Cover-letter pipeline. Returns a :class:`LetterPipelineResult`.

    Cache-wrapped (LOT 7): an identical (profile + job description + language +
    prompt version) request returns the previously rendered PDF + structured
    source with zero LLM calls."""
    import base64

    language = request.language
    logger.info(
        "[Letter] START | letter_id=%s | candidate=%s | language=%s",
        letter_id, request.cv_profile.name, language,
    )

    _key = _letter_cache_key(request)
    _t0 = time.perf_counter()
    cached = cache.get(_key)
    if isinstance(cached, dict) and cached.get("pdf_b64"):
        logger.info("[Letter] cache HIT | letter_id=%s", letter_id)
        profiling.record_semantic_cache_hit("cover_letter_full", (time.perf_counter() - _t0) * 1000.0)
        return LetterPipelineResult(
            pdf_bytes=base64.b64decode(cached["pdf_b64"]),
            structured=cached.get("structured", {}),
            from_cache=True,
        )

    with profiling.span("job_company_extraction"):
        company = _job_company_parser.extract(request.job_description, language=language)
    if request.recipient_name:
        company["recipient"] = request.recipient_name.strip()
    if request.company_address:
        company["company_address"] = request.company_address.strip()

    with profiling.span("cover_letter_generation"):
        body = _letter_generator.generate(
            request.cv_profile.model_dump(),
            request.job_description,
            company,
            language=language,
        )

    candidate = {
        "name": request.cv_profile.name,
        "email": request.cv_profile.email,
        "phone": request.cv_profile.phone,
        "address": request.cv_profile.address or "",
    }
    with profiling.span("pdf_rendering"):
        pdf_bytes = _letter_pdf_generator.render_to_bytes(candidate, company, body, language=language)

    structured = {"body": body, "company": company, "candidate": candidate, "language": language}
    from config import settings as _s
    cache.set(_key, {"pdf_b64": base64.b64encode(pdf_bytes).decode("ascii"),
                     "structured": structured},
              ttl=_s.LETTER_CACHE_TTL)
    logger.info("[Letter] DONE | letter_id=%s | pdf_bytes=%d", letter_id, len(pdf_bytes))
    return LetterPipelineResult(pdf_bytes=pdf_bytes, structured=structured)


def render_letter_from_structured(structured: dict, language: str) -> bytes:
    """Re-render a letter PDF from an edited ``structured_source`` (the edit
    agent path — LOT 9). No LLM call: the structured source IS the truth."""
    return _letter_pdf_generator.render_to_bytes(
        structured.get("candidate", {}), structured.get("company", {}),
        structured.get("body", ""), language=language,
    )


async def run_letter_pipeline_async(request: GenerateLetterRequest, letter_id: str) -> LetterPipelineResult:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, profiling.bind(partial(run_letter_pipeline, request, letter_id))
    )
