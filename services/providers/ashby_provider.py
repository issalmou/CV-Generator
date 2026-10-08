"""
Ashby — public job-board JSON API (no key, no browser).

``GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true``
-> ``{"jobs": [ ... ]}``. One token per company board;
``settings.ashby_boards_list`` holds them. The board response already
carries the full description + compensation, so there is no per-job
enrichment pass. A board that 404s / is blocked is skipped — only *every*
board failing marks the provider (see :class:`MultiBoardATSProvider`).
"""

from __future__ import annotations

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import MultiBoardATSProvider, looks_like_job, valid_slug
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, parse_salary, split_city_country, parse_posted_date,
)

_BASE = "https://api.ashbyhq.com/posting-api/job-board/{token}"

# Ashby's employmentType enum -> our coarse job_type.
_INTERN_TYPES = {"intern", "internship"}


class AshbyProvider(MultiBoardATSProvider):
    name = "ashby"
    allowed_hosts = ("ashbyhq.com",)
    application_priority = 55
    ats_label = "ashby"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("ashby", settings.ashby_boards_list, valid_slug)

    def _fetch_board(self, token, ctx: JobSearchContext, *, limit, deadline, keep):
        # Ashby returns the whole board in one request — the base applies
        # ``keep`` afterwards, so no per-page filtering is needed here.
        payload = http.fetch_json(
            _BASE.format(token=token),
            params={"includeCompensation": "true"},
            allowed_hosts=self.allowed_hosts,
            accept_language=ctx.language,
            max_bytes=settings.ATS_HTTP_MAX_BYTES,
        )
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        offers: list[NormalizedOffer] = []
        for row in rows or []:
            offer = self._normalize(row, token)
            if offer is not None:
                offers.append(offer)
        return offers

    @staticmethod
    def _normalize(row: dict, token: str) -> NormalizedOffer | None:
        if not isinstance(row, dict) or row.get("isListed") is False:
            return None
        job_id = str(row.get("id") or "").strip()
        title = clean_text(row.get("title"))
        url = (row.get("jobUrl") or row.get("applyUrl") or "").strip()
        if not job_id or not title or not looks_like_job(title, url):
            return None

        description = html_to_text(row.get("descriptionHtml")) or clean_text(
            row.get("descriptionPlain"), max_len=8000
        )

        loc_text = clean_text(row.get("location"))
        addr = ((row.get("address") or {}).get("postalAddress") or {}) if isinstance(row.get("address"), dict) else {}
        city, country = split_city_country(loc_text)
        city = city or clean_text(addr.get("addressLocality"))
        country = country or clean_text(addr.get("addressCountry"))

        emp_type = clean_text(row.get("employmentType"))
        is_intern = (emp_type or "").lower() in _INTERN_TYPES
        job_type = JobType.internship if is_intern else JobType(guess_job_type(title, description))

        comp = row.get("compensation") if isinstance(row.get("compensation"), dict) else {}
        salary_text = (comp or {}).get("compensationTierSummary") or (comp or {}).get(
            "scrapeableCompensationSalarySummary"
        )
        salary_min, salary_max, salary_currency = parse_salary(salary_text)

        secondary = [
            clean_text(s.get("location"))
            for s in (row.get("secondaryLocations") or [])
            if isinstance(s, dict) and s.get("location")
        ]
        org_units = [u for u in (clean_text(row.get("department")), clean_text(row.get("team"))) if u]

        return NormalizedOffer(
            source="ashby",
            source_job_id=f"{token}:{job_id}",
            source_url=url,
            title=title,
            company=token.replace("-", " ").title(),
            company_url=f"https://jobs.ashbyhq.com/{token}",
            location=loc_text,
            city=city,
            country=country,
            description=description,
            employment_type=emp_type,
            job_type=job_type,
            remote_type="remote" if row.get("isRemote") is True
            else guess_remote_type(title, description, loc_text),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            experience_level=guess_experience_level(title, description),
            skills=org_units[:5],
            posted_at=parse_posted_date(row.get("publishedAt") or row.get("updatedAt")),
            metadata={
                "ats": "ashby",
                "board": token,
                "department": clean_text(row.get("department")),
                "team": clean_text(row.get("team")),
                "secondary_locations": [s for s in secondary if s],
            },
        )
