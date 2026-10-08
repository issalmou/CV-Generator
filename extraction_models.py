"""
CV Assistant - CV Generation
Pydantic models for the resume EXTRACTION API contract.

These models back POST /api/extract-cv. They are intentionally separate
from `cv_models.CVProfile`:

- `CVProfile` (cv_models.py) is the contract for POST /api/generate-cv. It
  has several *required* fields (name, email, company, position, ...)
  because by the time a profile reaches that endpoint, the frontend has
  already shown the user a validation form and the data is assumed
  complete.
- `ExtractedCVProfile` (this module) is the contract for the raw output
  of resume extraction, *before* any human validation. Every field is
  optional. A missing value is always returned as `None` (or an empty
  list/string for collection types) — NEVER as a fabricated placeholder
  such as "Unknown Company" or a guessed year. This is a hard product
  requirement: the system must never invent dates, employers, degrees,
  technologies or certifications.

The frontend is expected to render `ExtractedCVProfile` in an editable
form, let the user fill in any blanks, then submit the *completed* data
back as a `cv_models.CVProfile` to POST /api/generate-cv.
"""

from typing import Optional

from pydantic import BaseModel, Field

from cv_models import SupportedLanguage


class ExtractedEducation(BaseModel):
    """
    A single education entry as found on the source document. Never fabricated.

    Dates come ONLY from this entry's own text in the EDUCATION section
    (never borrowed from an experience or another entry). `start_date` /
    `end_date` are kept close to the raw wording ("2022", "Septembre 2022").
    When the entry says "... - Présent" / "Depuis 2022" / "... - Present",
    `end_date` is resolved to the *current year* (obtained dynamically via
    `datetime.now().year`, never hard-coded). When no date is written at
    all, both stay `None`.
    """
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    start_date: Optional[str] = Field(
        None, description="Start date as written in this entry ('2022', 'Septembre 2022'). None if absent."
    )
    end_date: Optional[str] = Field(
        None,
        description=(
            "End date as written; an open-ended entry ('- Présent', 'Depuis 2022') "
            "resolves to the current year. None if no date is present."
        ),
    )
    gpa: Optional[str] = Field(
        None,
        description="GPA / mention / honours exactly as written ('3.9/4.0', 'Mention Très Bien', 'First Class Honours'). None if absent.",
    )
    location: Optional[str] = None


class ExtractedExperience(BaseModel):
    """
    A single professional experience entry as found on the source document.

    `period` is the date range exactly as identifiable in this role's own
    text ("Janvier 2024 - Juin 2024", "2023 - 2024", "Jan 2024 - Present").
    "Depuis <date>" / "Since <date>" is normalised to "<date> - Présent" /
    "<date> - Present" but "Présent"/"Present" is otherwise kept verbatim.
    `period` is `None` when no range is identifiable — never invented.
    """
    company: Optional[str] = None
    position: Optional[str] = None
    period: Optional[str] = Field(
        None,
        description=(
            "Duration of the role. A duration stated in the CV is kept as "
            "written ('3 mois'); when the CV gives two dates the duration is "
            "computed from them ('1 an 3 mois'); an ongoing role runs to "
            "today. None when the CV carries no temporal information — never "
            "invented."
        ),
    )
    location: Optional[str] = None
    description: Optional[str] = None
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(
        default_factory=list,
        description="Individual technologies exactly as written, one string each. Never grouped, never invented.",
    )


class ExtractedProject(BaseModel):
    """A single personal/professional project entry."""
    title: Optional[str] = None
    description: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)
    github: Optional[str] = None
    demo: Optional[str] = None


class ExtractedLanguage(BaseModel):
    """A spoken/written language proficiency entry."""
    language: Optional[str] = None
    level: Optional[str] = None


class ExtractedCertification(BaseModel):
    """A single certification entry. `year` is None when not stated — never guessed."""
    name: Optional[str] = None
    issuer: Optional[str] = None
    year: Optional[int] = None


class ExtractedCVProfile(BaseModel):
    """
    Raw candidate profile as extracted from a resume file, before human
    validation. Every field defaults to None/[] when not found in the
    source document — nothing here is ever invented.
    """
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    address: Optional[str] = None
    nationality: Optional[str] = None
    professional_summary: Optional[str] = None

    education: list[ExtractedEducation] = Field(default_factory=list)
    experience: list[ExtractedExperience] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "Flat list of every skill / technology found in the SKILLS "
            "section, in order of appearance, exact duplicates removed. "
            "NO categories — grouping (if wanted) is a later step. Each "
            "value is one skill, kept as written; nothing invented."
        ),
    )
    languages: list[ExtractedLanguage] = Field(default_factory=list)
    certifications: list[ExtractedCertification] = Field(default_factory=list)
    interests: list[str] = Field(
        default_factory=list,
        description="One entry per interest/hobby, exactly as written on the resume.",
    )
    personal_qualities: list[str] = Field(
        default_factory=list,
        description=(
            "One entry per personal quality / soft skill, exactly as written. "
            "Never contains contact info, a name, a job title, a skill or a "
            "technology."
        ),
    )


class ExtractionConfidence(BaseModel):
    """
    Local, heuristic confidence scores (0-100) per resume section.
    Computed by services.cv.confidence_scorer.ConfidenceScorer — never by
    an LLM, so the score reflects how much of each section was actually
    found on the document rather than the model's self-reported certainty.
    """
    contact: int = Field(0, ge=0, le=100)
    experience: int = Field(0, ge=0, le=100)
    education: int = Field(0, ge=0, le=100)
    projects: int = Field(0, ge=0, le=100)
    skills: int = Field(0, ge=0, le=100)


class ValidationIssueModel(BaseModel):
    """A single local-validation finding (never produced by an LLM)."""
    field: str
    message: str
    severity: str = Field(..., description="'error' | 'warning' | 'info'")


class ExtractCVResponse(BaseModel):
    """Response from POST /api/extract-cv."""
    status: str = Field(..., description="'success' or 'error'")
    language: SupportedLanguage = Field(
        ...,
        description=(
            "Effective language: the value passed in the optional `language` "
            "form field when present, otherwise `detected_language`. Drives "
            "the LLM section prompts."
        ),
    )
    detected_language: Optional[SupportedLanguage] = Field(
        None,
        description=(
            "Language auto-detected from the resume's own content, "
            "independently of any caller-supplied `language`. Compare with "
            "`language` to warn the user on a mismatch."
        ),
    )
    cv_profile: ExtractedCVProfile
    confidence_scores: ExtractionConfidence
    validation_issues: list[ValidationIssueModel] = Field(default_factory=list)
    duplicates_removed: dict[str, int] = Field(default_factory=dict)
    sections_detected: list[str] = Field(default_factory=list)
    message: Optional[str] = None
