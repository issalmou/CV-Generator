"""
CV Assistant - CV Generation
Pydantic models for CV data structures and API contracts.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

# Single source of truth for the two languages supported by the whole
# pipeline (extraction + generation + PDF rendering). Used everywhere a
# `language` field is required so FastAPI/Pydantic reject any other value
# with a clear 422 error instead of silently falling back to English.
SupportedLanguage = Literal["fr", "en"]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class Education(BaseModel):
    """
    Educational background entry.

    Uses the same date shape as the extraction contract
    (`extraction_models.ExtractedEducation`): `start_date` / `end_date`
    strings kept close to the CV wording, both optional. There is no
    `graduation_year` — the extraction step is the source of truth and it
    never derives a bare year.
    """
    institution: str = Field(..., description="University or school name")
    degree: str = Field(..., description="Degree type (Bachelor, Master, PhD, etc.)")
    field: str = Field(..., description="Field of study")
    start_date: Optional[str] = Field(
        None, description="Start date as written ('2022', 'Septembre 2022'). Optional."
    )
    end_date: Optional[str] = Field(
        None, description="End date as written; open-ended entries are resolved to the current year. Optional."
    )
    gpa: Optional[float] = Field(None, description="GPA (optional)")
    location: Optional[str] = Field(None, description="City, Country")


class Experience(BaseModel):
    """
    Professional experience entry.

    Uses the same date shape as the extraction contract
    (`extraction_models.ExtractedExperience`): a single free-text `period`
    string ("Janvier 2024 - Juin 2024", "Jan 2024 - Present"), optional.
    There are no separate `start_date` / `end_date` fields.
    """
    company: str = Field(..., description="Company name")
    position: str = Field(..., description="Job title / role")
    period: Optional[str] = Field(
        None, description="Date range as written, e.g. 'Janvier 2024 - Juin 2024'. Optional."
    )
    location: Optional[str] = Field(None, description="City, Country or Remote")
    description: Optional[str] = Field(None, description="Short role description")
    achievements: list[str] = Field(default_factory=list, description="Key achievements / bullet points")
    technologies: list[str] = Field(default_factory=list, description="Technologies used in this role")


class Project(BaseModel):
    """Personal or professional project."""
    title: str = Field(..., description="Project name")
    description: str = Field(..., description="What the project does / impact")
    technologies: list[str] = Field(default_factory=list, description="Stack used")
    github: Optional[str] = Field(None, description="GitHub repository URL")
    demo: Optional[str] = Field(None, description="Live demo URL")


class Certification(BaseModel):
    """Professional certification."""
    name: str = Field(..., description="Certification title")
    issuer: str = Field(..., description="Issuing organization")
    year: int = Field(..., description="Year obtained")


class SkillCategory(BaseModel):
    """A named group of skills."""
    category: str = Field(..., description="Category label (e.g. 'Programming Languages')")
    skills: list[str] = Field(..., description="List of skills in this category")


class Language(BaseModel):
    """Spoken / written language proficiency."""
    language: str = Field(..., description="Language name")
    level: str = Field(..., description="Proficiency level (Native, Fluent, Advanced, Intermediate, Basic)")


# ---------------------------------------------------------------------------
# Main profile model
# ---------------------------------------------------------------------------

class CVProfile(BaseModel):
    """Complete candidate profile used to generate the CV."""
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Professional email address")
    phone: str = Field(..., description="Phone number with country code")
    linkedin: Optional[str] = Field(None, description="LinkedIn profile URL")
    github: Optional[str] = Field(None, description="GitHub profile URL")
    portfolio: Optional[str] = Field(None, description="Personal website / portfolio URL")
    address: Optional[str] = Field(None, description="City, Country")
    nationality: Optional[str] = Field(None, description="Nationality (optional)")
    professional_summary: Optional[str] = Field(
        None,
        description="Existing summary; if omitted Gemini generates one"
    )
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[SkillCategory] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class GenerateCVRequest(BaseModel):
    """Payload for POST /api/generate-cv."""
    cv_profile: CVProfile
    job_description: Optional[str] = Field(
        None,
        description="Target job description for ATS optimisation (optional)",
        max_length=20000,
    )
    language: SupportedLanguage = Field(
        "en",
        description=(
            "Target language for the generated CV: 'fr' (French) or 'en' "
            "(English). Drives the summary, ATS optimisation wording, "
            "section titles and the final PDF — the source cv_profile "
            "content itself is never translated, only newly generated text."
        ),
    )
    reference: Optional[str] = Field(
        None,
        description=(
            "Existing document reference (e.g. 'CV_A8F42K'). When given, this "
            "generation is stored as a NEW VERSION of that document (same "
            "reference, version + 1); the older versions are kept. Omit to "
            "start a new document."
        ),
        max_length=32,
    )


class GenerateLetterRequest(BaseModel):
    """Payload for POST /api/generate-letter."""
    cv_profile: CVProfile
    job_description: str = Field(
        ...,
        description="Job posting the cover letter responds to (required).",
        min_length=1,
        max_length=20000,
    )
    language: SupportedLanguage = Field(
        "en",
        description=(
            "Language the cover letter is written in: 'fr' or 'en'. The "
            "cv_profile content is never translated — only the newly "
            "written letter text and the PDF labels follow this."
        ),
    )
    recipient_name: Optional[str] = Field(
        None,
        description="Override the addressee parsed from the job description (e.g. a named hiring manager).",
    )
    company_address: Optional[str] = Field(
        None,
        description="Override the company postal address parsed from the job description.",
    )
    reference: Optional[str] = Field(
        None,
        description=(
            "Existing letter reference (e.g. 'LETTER_91BC72'). When given, this "
            "is stored as a NEW VERSION of that letter; older versions are kept."
        ),
        max_length=32,
    )


class ATSAnalysis(BaseModel):
    """ATS keyword matching analysis."""
    extracted_keywords: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    keywords_to_highlight: list[str] = Field(default_factory=list)
    ats_score: float = Field(0.0, ge=0.0, le=100.0)


class HealthResponse(BaseModel):
    """GET /api/health response."""
    status: str
    service: str
    version: str
    gemini_configured: bool