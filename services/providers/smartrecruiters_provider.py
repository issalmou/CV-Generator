"""
SmartRecruiters — public postings JSON API (no key, no browser).

``GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=N``
-> ``{"content": [ ... ], "totalFound": N}``. Paginates with limit/offset.

The *listing* rows carry title / location / department / employment type /
experience level / released date but **not** the description — that comes
from a bounded per-job detail call
(``.../postings/{id}`` -> ``jobAd.sections.*.text``), capped by
``ATS_ENRICH_MAX`` and the search deadline.

Company slugs are **case-sensitive** (``Visa1``, ``BoschGroup``) — they are
never lower-cased.
"""

from __future__ import annotations

import time

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import ExperienceLevel, JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import (
    MultiBoardATSProvider, looks_like_job, valid_id, valid_slug,
)
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, parse_posted_date,
)

_LIST = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
_DETAIL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
_PAGE = 100
_MAX_PAGES = 15                                    # 1500 postings / board hard cap

_EXPERIENCE = {
    "internship": ExperienceLevel.student,
    "student": ExperienceLevel.student,
    "entry level": ExperienceLevel.entry,
    "associate": ExperienceLevel.junior,
    "mid-senior level": ExperienceLevel.mid,
    "director": ExperienceLevel.lead,
    "executive": ExperienceLevel.lead,
}
_INTERN_EMPLOYMENT = {"intern", "internship", "trainee", "apprentice"}
_DETAIL_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


class SmartRecruitersProvider(MultiBoardATSProvider):
    name = "smartrecruiters"
    allowed_hosts = ("smartrecruiters.com",)
    application_priority = 55
    ats_label = "smartrecruiters"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("smartrecruiters", settings.smartrecruiters_boards_list, valid_slug)

    def _fetch_board(self, token, ctx: JobSearchContext, *, limit, deadline, keep):
        offers: list[NormalizedOffer] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            payload = http.fetch_json(
                _LIST.format(slug=token),
                params={"limit": _PAGE, "offset": offset},
                allowed_hosts=self.allowed_hosts,
                accept_language=ctx.language,
                max_bytes=settings.ATS_HTTP_MAX_BYTES,
            )
            content = payload.get("content") if isinstance(payload, dict) else None
            if not content:
                break
            for row in content:
                offer = self._normalize(row, token)
                if offer is not None and keep(offer):
                    offers.append(offer)
                    if len(offers) >= limit:
                        return offers
            total = payload.get("totalFound")
            offset += _PAGE
            if len(content) < _PAGE:
                break
            if isinstance(total, int) and offset >= total:
                break
        return offers

    def _enrich(self, offers, ctx, *, deadline):
        self._bounded_enrich(offers, ctx, self._fetch_description, deadline=deadline)

    def _fetch_description(self, offer: NormalizedOffer) -> str | None:
        slug = (offer.metadata or {}).get("board")
        job_id = (offer.metadata or {}).get("posting_id")
        if not valid_slug(slug) or not valid_id(job_id):
            return None
        payload = http.fetch_json(
            _DETAIL.format(slug=slug, job_id=job_id),
            allowed_hosts=self.allowed_hosts,
        )
        sections = ((payload.get("jobAd") or {}).get("sections") or {}) if isinstance(payload, dict) else {}
        parts: list[str] = []
        for key in _DETAIL_SECTIONS:
            section = sections.get(key) or {}
            text = html_to_text(section.get("text")) if isinstance(section, dict) else None
            if text:
                parts.append(text)
        joined = "\n\n".join(parts).strip()
        return joined[:8000] or None

    @staticmethod
    def _normalize(row: dict, token: str) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        job_id = str(row.get("id") or "").strip()
        title = clean_text(row.get("name"))
        if not job_id or not title:
            return None
        url = f"https://jobs.smartrecruiters.com/{token}/{job_id}"
        if not looks_like_job(title, url):
            return None

        loc = row.get("location") if isinstance(row.get("location"), dict) else {}
        city = clean_text(loc.get("city"))
        country = clean_text(loc.get("country"))
        location = ", ".join(
            p for p in (city, clean_text(loc.get("region")), country) if p
        ) or None

        emp = row.get("typeOfEmployment") if isinstance(row.get("typeOfEmployment"), dict) else {}
        emp_label = clean_text(emp.get("label"))
        exp = row.get("experienceLevel") if isinstance(row.get("experienceLevel"), dict) else {}
        exp_label = (clean_text(exp.get("label")) or "").lower()

        is_intern = (emp_label or "").lower() in _INTERN_EMPLOYMENT or exp_label == "internship"
        job_type = JobType.internship if is_intern else JobType(guess_job_type(title))

        dept = row.get("department") if isinstance(row.get("department"), dict) else {}
        func = row.get("function") if isinstance(row.get("function"), dict) else {}
        skills = [s for s in (clean_text(dept.get("label")), clean_text(func.get("label"))) if s]

        company = row.get("company") if isinstance(row.get("company"), dict) else {}

        return NormalizedOffer(
            source="smartrecruiters",
            source_job_id=f"{token}:{job_id}",
            source_url=url,
            title=title,
            company=clean_text(company.get("name")) or token,
            company_url=f"https://jobs.smartrecruiters.com/{token}",
            location=location,
            city=city,
            country=country,
            employment_type=emp_label,
            job_type=job_type,
            remote_type="remote" if loc.get("remote") is True
            else guess_remote_type(title, location),
            experience_level=_EXPERIENCE.get(exp_label) or guess_experience_level(title),
            skills=skills[:5],
            posted_at=parse_posted_date(row.get("releasedDate")),
            metadata={
                "ats": "smartrecruiters",
                "board": token,
                "posting_id": job_id,
                "ref_number": clean_text(row.get("refNumber")),
            },
        )
