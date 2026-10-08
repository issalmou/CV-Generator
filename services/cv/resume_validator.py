"""
CV Assistant - CV Generation
Service: ResumeValidator

Responsibilities:
- Validate and clean extracted resume data: emails, URLs, dates,
  duplicate entries in experience/education/projects.
- 100% local. NEVER calls Gemini / any LLM.
- Produces both a cleaned dataset and a list of human-readable issues
  found, so the pipeline can decide what to surface to the user.

This module does not raise on invalid data — a resume parser must be
forgiving. Instead, it flags issues and lets the confidence-scoring layer
and the caller decide what to do with them.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation regexes
# ---------------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

_URL_PATTERN = re.compile(
    r"^(https?://)?([a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}(/[^\s]*)?$"
)

_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# A period expressed as a duration ("3 mois", "6 months", "1 an 3 mois").
_DURATION_RE = re.compile(
    r"^\s*(?:\d{1,3}\s+"
    r"(?:mois|months?|semaines?|weeks?|ans?|annees?|années?|years?|jours?|days?)\s*)+$",
    re.IGNORECASE,
)


@dataclass
class ValidationIssue:
    """A single validation finding."""
    field: str
    message: str
    severity: str = "warning"  # "warning" | "error" | "info"

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message, "severity": self.severity}


@dataclass
class ValidationReport:
    """Aggregate result of validating a parsed resume."""
    issues: list[ValidationIssue] = field(default_factory=list)
    duplicates_removed: dict[str, int] = field(default_factory=dict)

    def add(self, field_name: str, message: str, severity: str = "warning") -> None:
        self.issues.append(ValidationIssue(field=field_name, message=message, severity=severity))

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "duplicates_removed": dict(self.duplicates_removed),
        }


class ResumeValidator:
    """Validates and cleans extracted resume data. Purely local, no LLM calls."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def validate_contact(self, contact: dict[str, Any], report: ValidationReport) -> dict[str, Any]:
        """Validate email / phone / URLs in the contact dict; returns a cleaned copy."""
        cleaned = dict(contact)

        email = cleaned.get("email")
        if email and not _EMAIL_PATTERN.match(email.strip()):
            report.add("email", f"Email format looks invalid: '{email}'", severity="warning")

        phone = cleaned.get("phone")
        if phone:
            digits = sum(c.isdigit() for c in str(phone))
            if not (7 <= digits <= 15):
                report.add("phone", f"Phone number looks implausible: '{phone}'", severity="warning")

        for url_field in ("linkedin", "github", "portfolio"):
            url = cleaned.get(url_field)
            if url and not _URL_PATTERN.match(url.strip()):
                report.add(url_field, f"{url_field} URL looks invalid: '{url}'", severity="warning")

        address = cleaned.get("address")
        if address and ("@" in address or "http" in address.lower()):
            report.add(
                "address",
                f"Address looks like it captured contact/URL text: '{address}'",
                severity="warning",
            )

        if not cleaned.get("name"):
            report.add("name", "No name could be extracted.", severity="error")
        if not cleaned.get("email"):
            report.add("email", "No email could be extracted.", severity="error")

        return cleaned

    def validate_experience(
        self, experience: list[dict[str, Any]], report: ValidationReport
    ) -> list[dict[str, Any]]:
        """Validate experience entries: dates, required fields, duplicates."""
        deduped = self._deduplicate(
            experience,
            key_fn=lambda e: (
                (e.get("company") or "").strip().lower(),
                (e.get("position") or "").strip().lower(),
            ),
        )
        removed = len(experience) - len(deduped)
        if removed > 0:
            report.duplicates_removed["experience"] = removed
            report.add(
                "experience",
                f"Removed {removed} duplicate experience entr{'y' if removed == 1 else 'ies'}.",
                severity="info",
            )

        for idx, entry in enumerate(deduped):
            if not entry.get("company"):
                report.add(f"experience[{idx}].company", "Missing company name.", severity="warning")
            if not entry.get("position"):
                report.add(f"experience[{idx}].position", "Missing position/title.", severity="warning")
            period = entry.get("period")
            if not period:
                report.add(
                    f"experience[{idx}].period",
                    "No experience date or duration found in source document.",
                    severity="warning",
                )
            elif not _DURATION_RE.search(str(period)):
                # A calendar period should carry a recognisable year; a
                # bare duration ("3 mois") legitimately does not.
                self._check_date_text(str(period), f"experience[{idx}].period", report)
            entry["technologies"] = self._flatten_tech(
                entry.get("technologies"), f"experience[{idx}].technologies", report
            )

        return deduped

    def validate_education(
        self, education: list[dict[str, Any]], report: ValidationReport
    ) -> list[dict[str, Any]]:
        """Validate education entries: required fields, year sanity, duplicates."""
        deduped = self._deduplicate(
            education,
            key_fn=lambda e: (
                (e.get("institution") or "").strip().lower(),
                (e.get("degree") or "").strip().lower(),
                (e.get("field") or "").strip().lower(),
            ),
        )
        removed = len(education) - len(deduped)
        if removed > 0:
            report.duplicates_removed["education"] = removed
            report.add(
                "education",
                f"Removed {removed} duplicate education entr{'y' if removed == 1 else 'ies'}.",
                severity="info",
            )

        for idx, entry in enumerate(deduped):
            if not entry.get("institution"):
                report.add(f"education[{idx}].institution", "Missing institution name.", severity="warning")
            if not entry.get("degree"):
                report.add(f"education[{idx}].degree", "Missing degree.", severity="warning")

            start, end = entry.get("start_date"), entry.get("end_date")
            if start:
                self._check_date_text(str(start), f"education[{idx}].start_date", report)
            if end:
                self._check_date_text(str(end), f"education[{idx}].end_date", report)
            # end-before-start is a strong signal a date was mis-attributed
            # from another entry/section.
            sy, ey = self._year(start), self._year(end)
            if sy and ey and ey < sy:
                report.add(
                    f"education[{idx}].end_date",
                    f"End date ({end}) is before start date ({start}) — likely a mis-attributed date.",
                    severity="warning",
                )

        return deduped

    def validate_projects(
        self, projects: list[dict[str, Any]], report: ValidationReport
    ) -> list[dict[str, Any]]:
        """Validate project entries: required fields, URL sanity, duplicates."""
        deduped = self._deduplicate(
            projects,
            key_fn=lambda p: (p.get("title") or "").strip().lower(),
        )
        removed = len(projects) - len(deduped)
        if removed > 0:
            report.duplicates_removed["projects"] = removed
            report.add(
                "projects",
                f"Removed {removed} duplicate project entr{'y' if removed == 1 else 'ies'}.",
                severity="info",
            )

        for idx, entry in enumerate(deduped):
            if not entry.get("title"):
                report.add(f"projects[{idx}].title", "Missing project title.", severity="warning")
            for url_field in ("github", "demo"):
                url = entry.get(url_field)
                if url and not _URL_PATTERN.match(str(url).strip()):
                    report.add(
                        f"projects[{idx}].{url_field}",
                        f"{url_field} URL looks invalid: '{url}'",
                        severity="warning",
                    )
            entry["technologies"] = self._flatten_tech(
                entry.get("technologies"), f"projects[{idx}].technologies", report
            )

        return deduped

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(
        items: list[dict[str, Any]],
        key_fn,
    ) -> list[dict[str, Any]]:
        """Remove exact duplicate entries based on a normalised key, preserving order."""
        seen: set[tuple] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            key = key_fn(item)
            # Only treat as duplicate if the key has actual content (avoid
            # collapsing multiple genuinely-empty entries into one).
            if any(key) and key in seen:
                continue
            if any(key):
                seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _flatten_tech(
        values: Any, field_name: str, report: ValidationReport
    ) -> list[str]:
        """
        Guarantee a technology list is individual, de-duplicated strings.

        If an item still contains a separator (", " / " / " / ";"), it is
        split and an ``info`` issue is recorded. Nothing is ever added.
        """
        if not isinstance(values, list):
            return []
        out: list[str] = []
        split_any = False
        for value in values:
            if not isinstance(value, str):
                continue
            pieces = [p.strip(" .") for p in re.split(r"\s*[,/;]\s*", value)]
            pieces = [p for p in pieces if p]
            if len(pieces) > 1:
                split_any = True
            for piece in pieces:
                if piece not in out:
                    out.append(piece)
        if split_any:
            report.add(field_name, "Grouped technologies were split into individual items.", severity="info")
        return out

    @staticmethod
    def _year(text: Any) -> int | None:
        if not text:
            return None
        m = _YEAR_PATTERN.search(str(text))
        return int(m.group(0)) if m else None

    @staticmethod
    def _check_date_text(date_text: str, field_name: str, report: ValidationReport) -> None:
        """Sanity-check a raw date string without ever rewriting it."""
        text = str(date_text).strip()
        if not text:
            return

        lowered = text.lower()
        present_like = any(
            kw in lowered for kw in (
                "present", "présent", "presente", "current", "now", "ongoing",
                "actuel", "en cours", "depuis", "since", "maintenant", "aujourd",
                "à ce jour", "a ce jour", "expected", "prévu", "prevu", "attendu",
            )
        )
        if present_like:
            return

        years = _YEAR_PATTERN.findall(text)
        if not years:
            report.add(field_name, f"Date text has no recognisable year: '{text}'", severity="warning")
