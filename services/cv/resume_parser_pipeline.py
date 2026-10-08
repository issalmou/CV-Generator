"""
CV Assistant - CV Generation
Service: ResumeParserPipeline

Responsibilities:
- Orchestrate the full resume-parsing pipeline, end to end:

    PDF/DOCX
        -> ResumeTextExtractor      (raw text + PDF link annotations, no AI)
        -> link_extractor           (classify URLs; a PDF hyperlink beats any text guess)
        -> SectionSplitter          (named sections + language detection, no AI)
        -> ContactExtractor         (name/email/phone; linkedin/github/portfolio from the
                                      link annotation first, regex fallback — no AI)
        -> _clean_summary / _clean_interests / _clean_personal_qualities  (no AI)
        -> LocalSectionExtractor    (certifications + languages, regex, no AI —
                                      language levels kept verbatim)
        -> ResumeStructurer         (Gemini, one section at a time —
                                      experience / education / projects / skills,
                                      plus a Level 5 whole-resume fallback
                                      ONLY when deterministic splitting failed)
        -> source_grounding         (drop any LLM value not in its section source text)
        -> date_parser helpers      (resolve_education_dates: education dates kept
                                      VERBATIM — "Présent" is never a year;
                                      compute_experience_period: `period` = a duration)
        -> ResumeValidator          (local validation + dedup, no AI)
        -> ConfidenceScorer         (local scoring, no AI)
        -> ExtractedCVProfile + confidence + validation report

This is the ONLY module that knows about all the extraction services. It
does not touch cv_generator.py, pdf_generator.py, gemini_client.py or the
ATS pipeline — those are reached only after the frontend has validated the
extracted data and resubmitted it as a `cv_models.CVProfile` to
POST /api/generate-cv.

Gemini usage in this module: one section-scoped call each for
experience, education, projects and skills (a section that is empty is
skipped), PLUS at most 1 additional whole-resume fallback call in the
rare case described below. Contact, certifications and languages are
always fully local. The skills call has its own local regex fallback
(services.cv.skills_parser) so a skills result is never lost.

Every Gemini call receives ONE section's text only — never the whole
resume — so a date or a technology from one section can never bleed into
another.

Level 5 fallback — exact trigger condition
---------------------------------------------
Deterministic section splitting can fail in one specific, detectable way
on unusual layouts: a recognised EDUCATION or EXPERIENCE header IS found
by SectionSplitter, but its body comes back empty because the very next
line in the reconstructed text is itself another header (most commonly
caused by a layout the PDF reader still couldn't fully untangle, or an
edge-case template). This is fundamentally different from a resume that
genuinely has no education/experience section at all — in that case, no
education/experience header is detected in the first place, and the
fallback must NOT fire (calling Gemini on a skills-only resume would just
waste a call and risk fabricating content the candidate never wrote).

The fallback therefore fires if and only if:
    education_text == "" AND experience_text == ""
    AND (an "education" or "experience" header WAS detected by the splitter)

When it fires, `ResumeStructurer.parse_fallback(raw_text)` is called once
on the FULL resume text, and any education/experience/projects entries it
recovers are merged into the pipeline's results (certifications/skills
recovered by the fallback are merged too, but only to fill gaps — local
extraction results are never overwritten if they already found content).

Hard anti-hallucination contract
---------------------------------
This module NEVER fabricates a placeholder value (no "Unknown Company",
no "Unknown Institution", no invented year, no sentinel 0/"Not specified").
When a field is genuinely absent from the source document, the
corresponding field on `ExtractedCVProfile` is left as `None` (or an empty
list for collection fields). Every such gap is also recorded in
`ResumeParseResult.validation_issues` and reflected in the confidence
scores, so the caller can prompt the user to fill it in via the frontend
validation form, instead of unknowingly shipping a fabricated CV.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from services import profiling

from extraction_models import (
    ExtractedCertification,
    ExtractedCVProfile,
    ExtractedEducation,
    ExtractedExperience,
    ExtractedLanguage,
    ExtractedProject,
)
from services.cv.confidence_scorer import ConfidenceScorer
from services.cv.contact_extractor import ContactExtractor
from services.cv.date_parser import compute_experience_period, resolve_education_dates
from services.cv.link_extractor import classify_links, links_from_text
from services.cv.local_section_extractor import LocalSectionExtractor
from services.cv.resume_structurer import ResumeStructurer
from services.cv.resume_text_extractor import ResumeTextExtractor
from services.cv.resume_validator import ResumeValidator, ValidationReport
from services.cv.section_splitter import SectionSplitter, detect_language
from services.cv.text_cleaner import split_list_items
from services.cv.source_grounding import (
    ground_education_dates,
    ground_education_degree,
    ground_project_links,
    ground_experience_period,
    ground_skills,
    ground_technologies,
    split_degree_field,
    strip_company_derived_location,
)

# A "junk" line — one that belongs to the header / contact block and must
# never survive into `professional_summary`. On two-column layouts the
# section splitter routinely folds the sidebar (name, phone, email,
# address, LinkedIn) into the "Profile" section.
_SUMMARY_JUNK_PATTERNS = (
    re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),          # email
    re.compile(r"https?://|linkedin\.com|github\.com|\bwww\.|\.dev\b|\.io\b", re.IGNORECASE),
    re.compile(r"^\s*(linkedin|github|portfolio|e-?mail|t[ée]l|phone|tel|mobile|gsm)\s*:?\s*$", re.IGNORECASE),
    re.compile(r"(?:\+?\d[\d\s.\-()/]{6,}\d)"),                                # phone-ish (7+ digits)
    # address-ish: a street/place keyword, or a number followed by place words
    re.compile(
        r"\b(rue|avenue|av\.|bd|boulevard|bloc|blvd|résidence|residence|quartier|"
        r"hay\b|lot\b|imm\b|appartement|apt\b|b\.?p\.?|code\s+postal|n[°º]\s*\d|nr\s*\d|"
        r"ville\b|city\b|street\b|road\b)",
        re.IGNORECASE,
    ),
)

_LIST_HEADER_WORDS = {
    "compétences", "competences", "skills", "langues", "languages", "langage",
    "language", "expérience", "experience", "formation", "education", "projets",
    "projects", "certifications", "centres d'intérêt", "interests", "loisirs",
    "hobbies", "profil", "profile", "résumé", "resume", "summary", "contact",
    "qualités", "qualites", "personal qualities", "e-mail", "email", "tel",
    "téléphone", "telephone",
}
# A line that reads as a job title, not a personal quality.
_JOB_TITLE_HINT = re.compile(
    r"\b(d[ée]veloppeur|developer|ing[ée]nieur|engineer|manager|consultant|"
    r"analyst[e]?|architect[e]?|technicien|designer|full[\s\-]?stack|front[\s\-]?end|"
    r"back[\s\-]?end|stagiaire|intern|freelance|lead|chef\s+de\s+projet)\b",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


@dataclass
class ResumeParseResult:
    """Full result of parsing a resume file into an ExtractedCVProfile."""
    cv_profile: ExtractedCVProfile
    # `language` is the EFFECTIVE language used for LLM prompts and returned
    # on the API response: the caller-supplied value when one was passed to
    # POST /api/extract-cv, otherwise `detected_language`.
    # `detected_language` is always the language auto-detected from the
    # document's own content, regardless of what the caller passed — the
    # frontend can compare the two and warn on a mismatch.
    language: str = "en"
    detected_language: str = "en"
    confidence: dict[str, int] = field(default_factory=dict)
    validation_issues: list[dict[str, str]] = field(default_factory=list)
    duplicates_removed: dict[str, int] = field(default_factory=dict)
    sections_detected: list[str] = field(default_factory=list)
    gemini_fallback_used: bool = field(
        default=False,
        metadata={"description": "True if the Level 5 whole-resume fallback parse was triggered."},
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cv_profile": self.cv_profile.model_dump(),
            "language": self.language,
            "detected_language": self.detected_language,
            "confidence": self.confidence,
            "validation_issues": self.validation_issues,
            "duplicates_removed": self.duplicates_removed,
            "sections_detected": self.sections_detected,
            "gemini_fallback_used": self.gemini_fallback_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResumeParseResult":
        """Rebuild a result from ``to_dict()`` output (used by the app cache)."""
        return cls(
            cv_profile=ExtractedCVProfile.model_validate(data["cv_profile"]),
            language=data.get("language", "en"),
            detected_language=data.get("detected_language", "en"),
            confidence=data.get("confidence", {}) or {},
            validation_issues=data.get("validation_issues", []) or [],
            duplicates_removed=data.get("duplicates_removed", {}) or {},
            sections_detected=data.get("sections_detected", []) or [],
            gemini_fallback_used=data.get("gemini_fallback_used", False),
        )


class ResumeParserPipeline:
    """
    Orchestrates text extraction, section splitting, local regex extraction,
    targeted Gemini structuring, validation and confidence scoring into a
    final ExtractedCVProfile.
    """

    def __init__(self) -> None:
        self._text_extractor = ResumeTextExtractor()
        self._splitter = SectionSplitter()
        self._contact_extractor = ContactExtractor()
        self._local_extractor = LocalSectionExtractor()
        self._structurer = ResumeStructurer()
        self._validator = ResumeValidator()
        self._scorer = ConfidenceScorer()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_bytes(
        self, content: bytes, filename: str, *, language: str | None = None
    ) -> ResumeParseResult:
        """
        Parse in-memory résumé bytes (an uploaded file) into an
        ExtractedCVProfile + metadata.

        Also reads the file's link annotations (PDF ``/Link`` annotations,
        DOCX hyperlink relationships) so a LinkedIn / GitHub / portfolio
        URL that exists only as a hyperlink — not as visible text — is
        still recovered (spec §4 / §24).
        """
        with profiling.span("file_text_extraction"):
            doc = self._text_extractor.extract_document_from_bytes(content, filename)
        classified = classify_links(pdf_links=doc.links, text_urls=links_from_text(doc.text))
        return self.parse_text(
            doc.text,
            language=language,
            links=classified,
            layout_types=doc.layout_types,
            low_layout_confidence=doc.low_layout_confidence,
        )

    def parse_text(
        self,
        raw_text: str,
        *,
        language: str | None = None,
        links: "object | None" = None,
        layout_types: list[str] | None = None,
        low_layout_confidence: bool = False,
    ) -> ResumeParseResult:
        """
        Run the full pipeline starting from already-extracted raw text.

        Parameters
        ----------
        raw_text:
            Already-extracted resume text.
        language:
            Optional caller-supplied language hint (``"fr"`` / ``"en"``).
            When given it becomes the *effective* language (used for the
            LLM section prompts and returned as ``ResumeParseResult.language``).
            The document's own language is detected regardless and returned
            as ``detected_language`` so the caller can spot a mismatch.

        Exposed separately from ``parse_bytes`` so callers that already have
        raw text (e.g. pasted into a textarea) can skip the text-extraction
        stage entirely.
        """
        logger.info(
            "[ResumeParserPipeline] START | input_chars=%d | language_hint=%s",
            len(raw_text or ""), language,
        )

        detected_language = detect_language(raw_text or "")
        effective_language = language if language in ("fr", "en") else detected_language
        if language and language != detected_language:
            logger.info(
                "[ResumeParserPipeline] Caller language '%s' differs from detected '%s' "
                "— using caller value for prompts/response, surfacing both.",
                language, detected_language,
            )

        # Links: annotations from the file (via parse_bytes) merged with any
        # URL written in the text. Never None for the deterministic path.
        if links is None:
            links = classify_links(text_urls=links_from_text(raw_text))

        with profiling.span("section_split_and_local_extraction"):
            split_result = self._splitter.split_detailed(raw_text)
            sections = split_result.sections
            sections_detected = [canonical for _, canonical in split_result.detected_headers]
            detected_canonical_names = {canonical for _, canonical in split_result.detected_headers}

            # ---------------- Local extraction (no AI) ----------------
            contact = self._contact_extractor.extract(
                sections.get("contact", ""), raw_text, links=links
            )
            certifications = self._local_extractor.extract_certifications(
                sections.get("certifications", "")
            )
            languages = self._local_extractor.extract_languages(sections.get("languages", ""))
            summary_text = self._clean_summary(sections.get("summary", ""), contact)
            interests_list = self._clean_interests(sections.get("interests", ""), contact)
            personal_qualities_list = self._clean_personal_qualities(
                sections.get("personal_qualities", ""), contact
            )

        # ---------------- Gemini structuring (section-scoped calls) ----------------
        # Each block is sent to Gemini on its own — never the whole resume —
        # so a date/skill from one section can never contaminate another.
        # The four calls are independent, so they run concurrently: wall
        # time is ~one LLM round-trip instead of four. `call_gemini` is
        # already thread-safe (locked cache / rate-limiter / stats), and an
        # empty section returns instantly without a call.
        _jobs = {
            "experience": (self._structurer.parse_experience, sections.get("experience", "")),
            "education": (self._structurer.parse_education, sections.get("education", "")),
            "projects": (self._structurer.parse_projects, sections.get("projects", "")),
            "skills": (self._structurer.parse_skills, sections.get("skills", "")),
        }
        with profiling.span("llm_structuring_parallel"):
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv-parse") as _pool:
                # profiling.bind re-applies the request profile inside each worker
                # thread so the 4 section calls record into the same profile.
                _futures = {
                    name: _pool.submit(profiling.bind(fn), text, language=effective_language)
                    for name, (fn, text) in _jobs.items()
                }
                experience_raw = _futures["experience"].result()
                education_raw = _futures["education"].result()
                projects_raw = _futures["projects"].result()
                skills = _futures["skills"].result()

        # ---------------- Level 5: whole-resume fallback (conditional) ----------------
        gemini_fallback_used = False
        if self._should_trigger_fallback(
            education_raw=education_raw,
            experience_raw=experience_raw,
            detected_canonical_names=detected_canonical_names,
        ):
            logger.info(
                "[ResumeParserPipeline] Education/experience header detected but body "
                "empty after deterministic extraction — triggering Level 5 fallback."
            )
            fallback = self._structurer.parse_fallback(raw_text, language=effective_language)
            gemini_fallback_used = True

            if not education_raw:
                education_raw = fallback.education
            if not experience_raw:
                experience_raw = fallback.experience
            if not projects_raw:
                projects_raw = fallback.projects
            if not certifications:
                certifications = fallback.certifications
            if not skills and fallback.skills:
                skills = list(fallback.skills)

        # ---------------- Post-Gemini source grounding (local, no AI) ----------------
        # Every LLM-produced value is checked back against ITS OWN section
        # text; anything not supported by the source is removed (spec §12).
        # Runs on the RAW dates (before "Présent" -> current-year resolution)
        # so a genuinely-open-ended entry is not mistaken for a borrowed date.
        _t_ground = time.perf_counter()
        report = ValidationReport()

        # ---------------- Layout diagnostics (spec §15) ----------------
        _has_two_col = bool(layout_types and "TWO_COLUMNS" in layout_types)
        if low_layout_confidence and _has_two_col:
            report.add(
                "layout",
                "Two-column layout detected with low text reconstruction confidence. "
                "Some fields may be incomplete or mis-ordered — please review carefully.",
                severity="warning",
            )
        elif low_layout_confidence:
            report.add(
                "layout",
                "Complex multi-zone / designer layout detected; the reading order of the "
                "reconstructed text is uncertain. Please review every field carefully.",
                severity="warning",
            )
        elif _has_two_col:
            report.add(
                "layout",
                "Two-column layout detected and reconstructed.",
                severity="info",
            )

        education_text = sections.get("education", "")
        experience_text = sections.get("experience", "")
        projects_text = sections.get("projects", "")
        skills_text = sections.get("skills", "")

        education_raw = split_degree_field(education_raw)
        education_raw = ground_education_degree(education_raw, education_text)
        education_raw, edu_dates_dropped = ground_education_dates(education_raw, education_text)
        experience_raw, exp_period_dropped = ground_experience_period(experience_raw, experience_text)
        experience_raw, loc_dropped = strip_company_derived_location(experience_raw)
        experience_raw, exp_tech_dropped = ground_technologies(
            experience_raw, experience_text, section="experience"
        )
        projects_raw, proj_tech_dropped = ground_technologies(
            projects_raw, projects_text, section="projects"
        )
        # Attach a repo / demo URL to the project it belongs to — from the
        # projects text AND from PDF link annotations that classification
        # could not place in a contact slot (spec §4 / §24). The contact
        # linkedin/github/portfolio URLs are deliberately NOT in this pool.
        projects_raw = ground_project_links(
            projects_raw, projects_text, list(getattr(links, "others", []) or [])
        )
        # skills: flat list[str] — split any grouped item, keep order, then
        # drop anything not present in the SKILLS section text.
        skills = _clean_tech_list(skills)
        skills, skills_dropped = ground_skills(skills, skills_text)

        for label, dropped in (
            ("education dates", edu_dates_dropped),
            ("experience periods", exp_period_dropped),
            ("company-derived locations", loc_dropped),
        ):
            if dropped:
                report.add(
                    "grounding",
                    f"Removed {dropped} {label} not supported by the source text.",
                    severity="info",
                )
        for label, items in (
            ("experience technologies", exp_tech_dropped),
            ("project technologies", proj_tech_dropped),
            ("skills", skills_dropped),
        ):
            if items:
                report.add(
                    "grounding",
                    f"Removed {len(items)} {label} not found in the source text: {', '.join(items[:8])}",
                    severity="info",
                )

        # ---------------- Local date resolution (no AI) ----------------
        # AFTER grounding: only genuine, source-backed dates reach this point.
        #   Education: kept verbatim — "2022 - Présent" -> ("2022", "Présent").
        #   Experience `period`: a DURATION — a stated duration is kept
        #   ("Stage de trois mois" -> "3 mois"), two dates are turned into a
        #   computed duration ("Jan 2023 - Mar 2024" -> "1 an 3 mois").
        for entry in education_raw:
            entry["start_date"], entry["end_date"] = resolve_education_dates(
                entry.get("start_date"), entry.get("end_date")
            )
        for entry in experience_raw:
            entry["period"] = compute_experience_period(
                entry.get("period") or entry.get("period_text"),
                language=effective_language,
            )

        # ---------------- Validation (local, no AI) ----------------
        contact = self._validator.validate_contact(contact, report)
        experience_raw = self._validator.validate_experience(experience_raw, report)
        education_raw = self._validator.validate_education(education_raw, report)
        projects_raw = self._validator.validate_projects(projects_raw, report)

        # ---------------- Confidence scoring (local, no AI) ----------------
        confidence = self._scorer.compute_all(
            contact=contact,
            experience=experience_raw,
            education=education_raw,
            projects=projects_raw,
            skills=skills,
            report=report,
        )
        if low_layout_confidence:
            # A badly-reconstructed two-column PDF means every section may be
            # partly wrong — reflect that in every score (spec §15).
            confidence = {k: min(v, 55) for k, v in confidence.items()}

        # ---------------- Assemble ExtractedCVProfile (never fabricates) ----------------
        cv_profile = self._build_extracted_profile(
            contact=contact,
            summary_text=summary_text,
            experience_raw=experience_raw,
            education_raw=education_raw,
            projects_raw=projects_raw,
            skills=skills,
            certifications=certifications,
            languages=languages,
            interests_list=interests_list,
            personal_qualities_list=personal_qualities_list,
        )
        profiling.add_span_ms("grounding_validation_scoring", (time.perf_counter() - _t_ground) * 1000.0)

        logger.info(
            "[ResumeParserPipeline] DONE | language=%s | detected=%s | experience=%d | "
            "education=%d | projects=%d | skills=%d | certifications=%d | "
            "languages=%d | issues=%d | fallback_used=%s",
            effective_language, detected_language, len(experience_raw), len(education_raw),
            len(projects_raw), len(skills), len(certifications), len(languages),
            len(report.issues), gemini_fallback_used,
        )

        return ResumeParseResult(
            cv_profile=cv_profile,
            language=effective_language,
            detected_language=detected_language,
            confidence=confidence,
            validation_issues=[i.to_dict() for i in report.issues],
            duplicates_removed=dict(report.duplicates_removed),
            sections_detected=sections_detected,
            gemini_fallback_used=gemini_fallback_used,
        )

    # ------------------------------------------------------------------
    # Level 5 fallback trigger condition
    # ------------------------------------------------------------------

    @staticmethod
    def _should_trigger_fallback(
        *,
        education_raw: list[dict[str, Any]],
        experience_raw: list[dict[str, Any]],
        detected_canonical_names: set[str],
    ) -> bool:
        """
        Decide whether the Level 5 whole-resume fallback should run.

        See the module docstring ("Level 5 fallback — exact trigger
        condition") for the full rationale. In short: only fire when an
        education or experience HEADER was found but BOTH bodies came back
        empty — the signature of a layout/extraction failure, never of a
        resume that genuinely lacks those sections.
        """
        both_empty = not education_raw and not experience_raw
        if not both_empty:
            return False

        header_seen = bool(detected_canonical_names & {"education", "experience"})
        return header_seen

    # ------------------------------------------------------------------
    # ExtractedCVProfile assembly — NEVER fabricates a value. Every
    # missing field stays None / empty so the frontend validation form
    # can clearly flag it to the user instead of silently hiding it
    # behind a fake placeholder.
    # ------------------------------------------------------------------

    def _build_extracted_profile(
        self,
        *,
        contact: dict[str, Any],
        summary_text: str | None,
        experience_raw: list[dict[str, Any]],
        education_raw: list[dict[str, Any]],
        projects_raw: list[dict[str, Any]],
        skills: list[str],
        certifications: list[dict[str, Any]],
        languages: list[dict[str, Any]],
        interests_list: list[str],
        personal_qualities_list: list[str],
    ) -> ExtractedCVProfile:
        return ExtractedCVProfile(
            name=contact.get("name") or None,
            email=contact.get("email") or None,
            phone=contact.get("phone") or None,
            linkedin=contact.get("linkedin") or None,
            github=contact.get("github") or None,
            portfolio=contact.get("portfolio") or None,
            address=contact.get("address") or None,
            nationality=contact.get("nationality") or None,
            professional_summary=summary_text,
            education=[self._to_education(e) for e in education_raw],
            experience=[self._to_experience(e) for e in experience_raw],
            projects=[self._to_project(p) for p in projects_raw],
            skills=list(skills),
            languages=[self._to_language(l) for l in languages],
            certifications=[self._to_certification(c) for c in certifications],
            interests=interests_list,
            personal_qualities=personal_qualities_list,
        )

    @staticmethod
    def _to_experience(entry: dict[str, Any]) -> ExtractedExperience:
        # `period` was already normalised in the pipeline's date-resolution
        # step; here we only shape the entry into the Pydantic model.
        return ExtractedExperience(
            company=entry.get("company") or None,
            position=entry.get("position") or None,
            period=entry.get("period") or None,
            location=entry.get("location") or None,
            description=entry.get("description") or None,
            achievements=entry.get("achievements") or [],
            technologies=_clean_tech_list(entry.get("technologies")),
        )

    @staticmethod
    def _to_education(entry: dict[str, Any]) -> ExtractedEducation:
        # start_date / end_date were already resolved in the pipeline's
        # date-resolution step (open-ended -> current year).
        start_date = entry.get("start_date") or None
        end_date = entry.get("end_date") or None

        # GPA / mention / honours: kept verbatim as text ("3.9/4.0",
        # "Mention Très Bien", "First Class Honours") — never coerced to a
        # bare number, which would drop the scale and any wording.
        gpa_raw = entry.get("gpa")
        gpa = str(gpa_raw).strip() if gpa_raw not in (None, "", []) else None

        return ExtractedEducation(
            institution=entry.get("institution") or None,
            degree=entry.get("degree") or None,
            field=entry.get("field") or None,
            start_date=start_date,
            end_date=end_date,
            gpa=gpa,
            location=entry.get("location") or None,
        )

    @staticmethod
    def _to_project(entry: dict[str, Any]) -> ExtractedProject:
        return ExtractedProject(
            title=entry.get("title") or None,
            description=entry.get("description") or None,
            technologies=_clean_tech_list(entry.get("technologies")),
            github=entry.get("github") or None,
            demo=entry.get("demo") or None,
        )

    @staticmethod
    def _to_certification(entry: dict[str, Any]) -> ExtractedCertification:
        return ExtractedCertification(
            name=entry.get("name") or None,
            issuer=entry.get("issuer") or None,
            # `year` is only ever set when explicitly found in the source
            # text. A missing year stays None — never a guessed/sentinel value.
            year=entry.get("year") or None,
        )

    @staticmethod
    def _to_language(entry: dict[str, Any]) -> ExtractedLanguage:
        return ExtractedLanguage(
            language=entry.get("language") or None,
            # `level` is kept verbatim ("Langue maternelle", "Courant") and
            # stays None when no proficiency was stated — never guessed.
            level=entry.get("level") or None,
        )

    # ------------------------------------------------------------------
    # professional_summary cleanup (see spec §8/§9)
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_summary(raw_summary: str, contact: dict[str, Any]) -> str | None:
        """
        Return ONLY the real prose of the "Profile / Summary / À propos"
        section (spec §1 / §9).

        On two-column templates the section splitter routinely folds the
        sidebar (name, phone, email, address, LinkedIn, ...) into the
        "Profile" block — always as a run of lines that is *contiguous* and
        sits before or after the actual summary sentence(s). Strategy:

        1. classify every line as JUNK (contact / address / link / bare
           social token / mostly-digits / the candidate's own name) or PROSE;
        2. keep the single longest run of consecutive PROSE lines;
        3. if that run has no real sentence in it, return ``None`` — the
           summary is never reconstructed or stitched from fragments.
        """
        if not raw_summary or not raw_summary.strip():
            return None

        contact_tokens = {
            str(contact.get(k)).strip().lower()
            for k in ("name", "email", "phone", "linkedin", "github", "portfolio", "address")
            if contact.get(k)
        }
        name_parts = {
            p.lower() for p in str(contact.get("name") or "").split() if len(p) > 1
        }

        def is_junk(line: str) -> bool:
            low = line.lower().strip(" .·-–—|")
            if not low:
                return True
            if low in contact_tokens:
                return True
            if any(p.search(line) for p in _SUMMARY_JUNK_PATTERNS):
                return True
            words = low.split()
            # a 1-2 word line that is entirely name parts is the name block
            if 1 <= len(words) <= 2 and words and all(w in name_parts for w in words):
                return True
            # ALL-CAPS 1-3 word line = a header / name fragment
            if line.isupper() and len(words) <= 3:
                return True
            # mostly digits / punctuation
            letters = sum(c.isalpha() for c in line)
            if letters < 3:
                return True
            return False

        # Longest run of consecutive non-junk lines.
        best: list[str] = []
        current: list[str] = []
        for line in raw_summary.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if is_junk(stripped):
                if len(" ".join(current)) > len(" ".join(best)):
                    best = current
                current = []
            else:
                current.append(stripped)
        if len(" ".join(current)) > len(" ".join(best)):
            best = current

        cleaned = re.sub(r"\s{2,}", " ", " ".join(best).strip())
        # Need a genuine sentence: enough letters AND at least a few words.
        if sum(c.isalpha() for c in cleaned) < 25 or len(cleaned.split()) < 5:
            return None
        return cleaned

    # ------------------------------------------------------------------
    # interests / personal_qualities -> list[str] (spec §12 / §13)
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_interests(raw_text: str, contact: dict[str, Any]) -> list[str]:
        """
        Split the interests block into items (wrapped lines rejoined),
        dropping any contact / name / header fragment that a two-column
        layout folded into it (spec §12). A job title is never an interest,
        so title-looking fragments are dropped too.
        """
        return ResumeParserPipeline._clean_freeform_list(raw_text, contact, drop_titles=True)

    @staticmethod
    def _clean_personal_qualities(raw_text: str, contact: dict[str, Any]) -> list[str]:
        """
        Split the personal-qualities block into items, dropping anything
        that is actually contact info, a name, a job title, an
        email/phone/URL or a section-header word (spec §13).
        """
        return ResumeParserPipeline._clean_freeform_list(raw_text, contact, drop_titles=True)

    @staticmethod
    def _clean_freeform_list(
        raw_text: str, contact: dict[str, Any], *, drop_titles: bool
    ) -> list[str]:
        banned = {
            str(contact.get(k)).strip().lower()
            for k in ("name", "email", "phone", "linkedin", "github", "portfolio", "address")
            if contact.get(k)
        }
        name_parts = {p.lower() for p in str(contact.get("name") or "").split() if len(p) > 1}
        out: list[str] = []
        for item in split_list_items(raw_text):
            low = item.strip().lower()
            if not (2 <= len(item) <= 80):
                continue
            if low in banned or low in _LIST_HEADER_WORDS:
                continue
            if any(p.search(item) for p in _SUMMARY_JUNK_PATTERNS):
                continue
            if re.search(r"\b(19|20)\d{2}\b", item):          # a date fragment
                continue
            words = low.split()
            if words and all(w in name_parts for w in words):  # the candidate's name
                continue
            if item.isupper() and len(words) <= 2:             # "ISSALMOU" fragment
                continue
            if drop_titles and _JOB_TITLE_HINT.search(low):     # "Développeur Full Stack"
                continue
            if item not in out:
                out.append(item)
            if len(out) >= 20:
                break
        return out


def _clean_tech_list(values: Any) -> list[str]:
    """
    Force a technology list to be individual, de-duplicated strings.

    Splits any ``"a, b, c"`` item the model may still have returned as one
    string, trims, drops blanks — but never adds anything.
    """
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        for piece in re.split(r"\s*[,/;]\s*| • ", value):
            piece = piece.strip(" .")
            if piece and piece not in out:
                out.append(piece)
    return out
