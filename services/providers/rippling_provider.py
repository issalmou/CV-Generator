"""
Rippling ATS — public board JSON API (no key, no browser).

``GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs``
-> a flat JSON array of postings. A multi-location role repeats with the
same ``uuid`` and a different ``workLocation`` — deduped by uuid here.

``settings.rippling_boards_list`` holds the board slugs. There is no
stable per-job detail endpoint, so the description is taken from the list
row when present and left ``None`` otherwise.
"""

from __future__ import annotations

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import MultiBoardATSProvider, looks_like_job, valid_slug
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, split_city_country, parse_posted_date,
)

_BASE = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
_INTERN_EMPLOYMENT = {"intern", "internship", "temporary"}


def _label(node) -> str | None:
    if isinstance(node, dict):
        return clean_text(node.get("label") or node.get("name"))
    return clean_text(node)


class RipplingProvider(MultiBoardATSProvider):
    name = "rippling"
    allowed_hosts = ("rippling.com",)
    application_priority = 55
    ats_label = "rippling"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("rippling", settings.rippling_boards_list, valid_slug)

    def _fetch_board(self, token, ctx: JobSearchContext, *, limit, deadline, keep):
        # single request per board — the base applies ``keep`` afterwards.
        payload = http.fetch_json(
            _BASE.format(slug=token),
            allowed_hosts=self.allowed_hosts,
            accept_language=ctx.language,
            max_bytes=settings.ATS_HTTP_MAX_BYTES,
        )
        rows = payload if isinstance(payload, list) else (
            payload.get("jobs") if isinstance(payload, dict) else None
        )
        best_by_uuid: dict[str, dict] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("uuid") or row.get("id") or "")
            if not key:
                continue
            # prefer the first entry; upgrade only to one that has a description
            if key not in best_by_uuid or (
                not best_by_uuid[key].get("descriptionHtml") and row.get("descriptionHtml")
            ):
                best_by_uuid[key] = row

        offers: list[NormalizedOffer] = []
        for key, row in best_by_uuid.items():
            offer = self._normalize(row, token, key)
            if offer is not None:
                offers.append(offer)
        return offers

    @staticmethod
    def _normalize(row: dict, token: str, key: str) -> NormalizedOffer | None:
        title = clean_text(row.get("name") or row.get("title"))
        url = (row.get("url") or row.get("jobUrl") or "").strip()
        if not title or not url or not looks_like_job(title, url):
            return None

        location = _label(row.get("workLocation")) or clean_text(row.get("location"))
        city, country = split_city_country(location)
        dept = _label(row.get("department"))
        emp = clean_text(row.get("employmentType"))
        is_intern = (emp or "").lower().replace("_", " ").strip() in _INTERN_EMPLOYMENT
        description = html_to_text(row.get("descriptionHtml")) or clean_text(
            row.get("descriptionPlain"), max_len=8000
        )

        return NormalizedOffer(
            source="rippling",
            source_job_id=f"{token}:{key}",
            source_url=url,
            title=title,
            company=token.replace("-", " ").title(),
            location=location,
            city=city,
            country=country,
            description=description,
            employment_type=emp,
            job_type=JobType.internship if is_intern else JobType(guess_job_type(title, description)),
            remote_type="remote" if row.get("isRemote") is True
            else guess_remote_type(title, description, location),
            experience_level=guess_experience_level(title, description),
            skills=[dept] if dept else [],
            posted_at=parse_posted_date(row.get("createdAt") or row.get("publishedAt")),
            metadata={"ats": "rippling", "board": token, "department": dept},
        )
