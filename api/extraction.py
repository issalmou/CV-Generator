"""
CV Assistant - CV Generation
API router: resume EXTRACTION endpoints.

Exposes:
    POST /api/extract-cv   — multipart file upload (PDF/DOCX/TXT). The
                              sole extraction endpoint. Returns the exact
                              validation-ready JSON contract consumed by
                              the frontend's review form.

This endpoint does not generate a PDF. It only extracts and validates
data so the frontend can show an editable form before the candidate ever
submits a completed `cv_models.CVProfile` to POST /api/generate-cv.

Language handling
------------------
The endpoint accepts an OPTIONAL ``language`` form field (``"fr"`` or
``"en"``). When supplied it is the *effective* language: it drives the
LLM section prompts and is echoed back as ``language`` on the response.
Independently, the pipeline always auto-detects the document's own
language from its section headers and marker words and returns that as
``detected_language`` — so the frontend can compare the two and warn the
user if, say, they picked "en" but uploaded a French CV. When the field
is omitted, ``language`` mirrors ``detected_language``.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from config import settings
from cv_models import SupportedLanguage
from dependencies.auth import get_current_user
from extraction_models import ExtractCVResponse, ExtractionConfidence
from models import User
from services.generation_service import run_extraction
from services.cv.resume_parser_pipeline import ResumeParseResult
from services.cv.resume_text_extractor import TextExtractionError, UnsupportedFileTypeError

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE


def _validate_language(language: Optional[str]) -> Optional[SupportedLanguage]:
    """Normalise the optional ``language`` form field; 400 on an unsupported value."""
    if language is None or language == "":
        return None
    normalised = language.strip().lower()
    if normalised not in ("fr", "en"):
        raise HTTPException(
            status_code=422,
            detail="language must be 'fr' or 'en' when provided.",
        )
    return normalised  # type: ignore[return-value]


def _result_to_response(result: ResumeParseResult, *, message: str) -> ExtractCVResponse:
    """
    Map the internal ResumeParseResult onto the public API contract.

    `language` is the effective language (caller-supplied when present,
    otherwise the auto-detected one). `detected_language` is always what
    the pipeline detected from the document itself.
    """
    confidence = ExtractionConfidence(
        contact=result.confidence.get("contact_confidence", 0),
        experience=result.confidence.get("experience_confidence", 0),
        education=result.confidence.get("education_confidence", 0),
        projects=result.confidence.get("projects_confidence", 0),
        skills=result.confidence.get("skills_confidence", 0),
    )
    return ExtractCVResponse(
        status="success",
        language=result.language,
        detected_language=result.detected_language,
        cv_profile=result.cv_profile,
        confidence_scores=confidence,
        validation_issues=result.validation_issues,
        duplicates_removed=result.duplicates_removed,
        sections_detected=result.sections_detected,
        message=message,
    )


@router.post(
    "/api/extract-cv",
    response_model=ExtractCVResponse,
    summary="Extract structured data from an uploaded resume (PDF/DOCX/TXT) for frontend validation",
    tags=["CV Extraction"],
)
async def extract_cv(
    file: UploadFile = File(..., description="Resume file, PDF, DOCX or TXT"),
    language: Optional[str] = Form(
        None,
        description="Optional target language: 'fr' or 'en'. Omit to use the auto-detected language.",
    ),
    current_user: User = Depends(get_current_user),
) -> ExtractCVResponse:
    """
    Parse an uploaded resume file through the local-first extraction
    pipeline (layout-aware text extraction -> section split -> regex ->
    targeted Gemini structuring -> validation -> confidence scoring) and
    return a structured, NEVER-FABRICATED profile for the frontend to
    render as an editable validation form.

    PDF text extraction is layout-aware: single-column and two-column /
    sidebar / Canva / Europass-style resumes are both supported, since
    reading order is reconstructed from each line's coordinates rather
    than the PDF's internal content-stream order.

    At most 3 Gemini calls are made for a normal resume (experience /
    education / projects), plus at most 1 additional fallback call in the
    rare case where deterministic section splitting detected an
    education/experience header but could not recover its body (see
    ResumeParserPipeline for the exact trigger condition). Contact info,
    skills, certifications and languages are extracted 100% locally.
    Missing fields are returned as `null`/empty — never as a guessed
    value.

    `language` is optional. When provided ('fr'/'en') it drives the LLM
    section prompts and is echoed as `language`; the document's own
    language is detected regardless and returned as `detected_language`.
    """
    filename = file.filename or "resume"
    requested_language = _validate_language(language)
    logger.info(
        "[API] POST /api/extract-cv | filename=%s | language=%s",
        filename, requested_language or "(auto)",
    )

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB).")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = run_extraction(content, filename, requested_language)
    except UnsupportedFileTypeError as exc:
        logger.warning("[API] Unsupported file type | filename=%s | %s", filename, exc)
        return JSONResponse(
            status_code=415,
            content={"status": "error", "message": str(exc)},
        )
    except TextExtractionError as exc:
        logger.error("[API] Text extraction failed | filename=%s | %s", filename, exc)
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": str(exc)},
        )
    except Exception:
        logger.exception("[API] Unexpected error extracting resume | filename=%s", filename)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Resume extraction failed."},
        )

    return _result_to_response(result, message="Resume extracted successfully.")
