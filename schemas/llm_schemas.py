"""Structured-output contracts for every LLM ``request_type`` that returns JSON.

Two things live here, side by side, per request type:

* a **strict JSON Schema** (``additionalProperties: false``, every key
  ``required``, nullable fields typed ``["string", "null"]``) — passed to the
  provider as ``response_format={"type": "json_schema", ...}`` when the routed
  model supports it, so a capable model *cannot* return the wrong shape;
* a lenient **Pydantic validator** (``extra="ignore"``, coercing) — the second
  line of defence, used to validate *every* response (schema-capable or not)
  and to drive the single repair retry (``services.parser_common
  .request_structured_json``).

The prompts themselves are unchanged in spirit — they already describe these
shapes in prose. This module makes the contract machine-checkable and is the
single place the shapes are defined.

Nothing here calls the LLM. Nothing here fabricates: a validator never invents
a value to satisfy a required key — missing data stays ``None`` / ``[]``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_STR_OR_NULL = {"type": ["string", "null"]}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _object(props: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    """A strict object schema — all listed props required, no extras."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": required if required is not None else list(props),
    }


def _schema(name: str, root: dict[str, Any]) -> dict[str, Any]:
    """Wrap a schema body in the OpenAI ``response_format`` envelope."""
    return {"name": name, "strict": True, "schema": root}


class _Lenient(BaseModel):
    """Base for every validator: ignore unknown keys, coerce where sane."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# résumé section parsers
# ---------------------------------------------------------------------------

class ExperienceEntry(_Lenient):
    company: str | None = None
    position: str | None = None
    period: str | None = None
    location: str | None = None
    description: str | None = None
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)

    @field_validator("achievements", "technologies", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return v


class ExperienceList(_Lenient):
    experience: list[ExperienceEntry] = Field(default_factory=list)


class EducationEntry(_Lenient):
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    gpa: str | None = None
    location: str | None = None


class EducationList(_Lenient):
    education: list[EducationEntry] = Field(default_factory=list)


class ProjectEntry(_Lenient):
    title: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    github: str | None = None
    demo: str | None = None

    @field_validator("technologies", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return v


class ProjectList(_Lenient):
    projects: list[ProjectEntry] = Field(default_factory=list)


class SkillsList(_Lenient):
    skills: list[str] = Field(default_factory=list)

    @field_validator("skills", mode="before")
    @classmethod
    def _flatten(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        out: list[str] = []
        for item in v:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("skills"), list):
                out.extend(s for s in item["skills"] if isinstance(s, str))
        return out


class CertificationEntry(_Lenient):
    name: str | None = None
    issuer: str | None = None
    date_text: str | None = None


class FallbackResult(_Lenient):
    education: list[EducationEntry] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @field_validator("skills", mode="before")
    @classmethod
    def _flat(cls, v: Any) -> Any:
        return SkillsList._flatten(v)


_EXPERIENCE_ITEM = _object({
    "company": _STR_OR_NULL, "position": _STR_OR_NULL, "period": _STR_OR_NULL,
    "location": _STR_OR_NULL, "description": _STR_OR_NULL,
    "achievements": _STR_LIST, "technologies": _STR_LIST,
})
_EDUCATION_ITEM = _object({
    "institution": _STR_OR_NULL, "degree": _STR_OR_NULL, "field": _STR_OR_NULL,
    "start_date": _STR_OR_NULL, "end_date": _STR_OR_NULL, "gpa": _STR_OR_NULL,
    "location": _STR_OR_NULL,
})
_PROJECT_ITEM = _object({
    "title": _STR_OR_NULL, "description": _STR_OR_NULL,
    "technologies": _STR_LIST, "github": _STR_OR_NULL, "demo": _STR_OR_NULL,
})
_CERT_ITEM = _object({
    "name": _STR_OR_NULL, "issuer": _STR_OR_NULL, "date_text": _STR_OR_NULL,
})


# ---------------------------------------------------------------------------
# ATS
# ---------------------------------------------------------------------------

class ATSKeywords(_Lenient):
    technical_skills: list[str] = Field(default_factory=list)
    business_skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    degrees: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)


class OptimizedRole(_Lenient):
    original_role: str = ""
    bullets: list[str] = Field(default_factory=list)


class OptimizedContent(_Lenient):
    optimized_summary: str = ""
    career_objective: str = ""
    optimized_achievements: list[OptimizedRole] = Field(default_factory=list)
    additional_skills: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# profile analysis
# ---------------------------------------------------------------------------

class ProfileAnalysis(_Lenient):
    professional_summary: str = ""
    career_objective: str = ""
    key_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    expertise_areas: list[str] = Field(default_factory=list)
    years_of_experience: int = 0
    seniority_level: str = ""

    @field_validator("years_of_experience", mode="before")
    @classmethod
    def _int(cls, v: Any) -> int:
        try:
            return max(0, int(float(str(v).replace("+", "").strip() or 0)))
        except (TypeError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# cover-letter company parse
# ---------------------------------------------------------------------------

class LetterCompany(_Lenient):
    company_name: str | None = None
    position: str | None = None
    location: str | None = None
    recipient: str | None = None
    company_address: str | None = None


# ---------------------------------------------------------------------------
# registry:  request_type -> (json_schema envelope | None, validator)
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict[str, Any] | None] = {
    "resume_parse_experience": _schema("experience", _object({"experience": {"type": "array", "items": _EXPERIENCE_ITEM}})),
    "resume_parse_education": _schema("education", _object({"education": {"type": "array", "items": _EDUCATION_ITEM}})),
    "resume_parse_projects": _schema("projects", _object({"projects": {"type": "array", "items": _PROJECT_ITEM}})),
    "resume_parse_skills": _schema("skills", _object({"skills": _STR_LIST})),
    "resume_parse_fallback": _schema("fallback", _object({
        "education": {"type": "array", "items": _EDUCATION_ITEM},
        "experience": {"type": "array", "items": _EXPERIENCE_ITEM},
        "projects": {"type": "array", "items": _PROJECT_ITEM},
        "certifications": {"type": "array", "items": _CERT_ITEM},
        "skills": _STR_LIST,
    })),
    "ats_keyword_extraction": _schema("ats_keywords", _object({
        "technical_skills": _STR_LIST, "business_skills": _STR_LIST, "tools": _STR_LIST,
        "frameworks": _STR_LIST, "certifications": _STR_LIST, "degrees": _STR_LIST,
        "soft_skills": _STR_LIST,
    })),
    "ats_content_optimization": _schema("ats_optimized", _object({
        "optimized_summary": {"type": "string"},
        "career_objective": {"type": "string"},
        "optimized_achievements": {"type": "array", "items": _object({
            "original_role": {"type": "string"}, "bullets": _STR_LIST,
        })},
        "additional_skills": _STR_LIST,
    })),
    "profile_analysis": _schema("profile_analysis", _object({
        "professional_summary": {"type": "string"},
        "career_objective": {"type": "string"},
        "key_skills": _STR_LIST, "strengths": _STR_LIST, "expertise_areas": _STR_LIST,
        "years_of_experience": {"type": "integer"},
        "seniority_level": {"type": "string"},
    })),
    "letter_job_company": _schema("letter_company", _object({
        "company_name": _STR_OR_NULL, "position": _STR_OR_NULL, "location": _STR_OR_NULL,
        "recipient": _STR_OR_NULL, "company_address": _STR_OR_NULL,
    })),
    # dynamic-key objects — schema can't pin them; validated by Pydantic + json_object mode
    "skill_categorisation": None,
    "job_preference_extraction": None,
}

_VALIDATORS: dict[str, Any] = {
    "resume_parse_experience": lambda d: ExperienceList.model_validate(d),
    "resume_parse_education": lambda d: EducationList.model_validate(d),
    "resume_parse_projects": lambda d: ProjectList.model_validate(d),
    "resume_parse_skills": lambda d: SkillsList.model_validate(d),
    "resume_parse_fallback": lambda d: FallbackResult.model_validate(d),
    "ats_keyword_extraction": lambda d: ATSKeywords.model_validate(d),
    "ats_content_optimization": lambda d: OptimizedContent.model_validate(d),
    "profile_analysis": lambda d: ProfileAnalysis.model_validate(d),
    "letter_job_company": lambda d: LetterCompany.model_validate(d),
}


def json_schema_for(request_type: str) -> dict[str, Any] | None:
    """The strict ``response_format`` schema envelope for a request type, or
    ``None`` when the shape has dynamic keys (caller falls back to json_object)."""
    return _SCHEMAS.get(request_type)


def validator_for(request_type: str):
    """The Pydantic validation callable ``dict -> model`` for a request type,
    or ``None`` when there is no structured contract."""
    return _VALIDATORS.get(request_type)
