"""
Phenom People — per-company ``/widgets`` search API (no key, no browser).

Phenom has **no shared public API**: every tenant exposes its own
``/widgets`` POST endpoint with a tenant-specific filter payload. So the
config is explicit per company:

``PHENOM_BOARDS`` is a JSON array of
``{"company": "...", "endpoint": "https://careers.example.com/widgets",
   "payload": { ... optional tenant filter body ... }}``.

Each request is ``POST {endpoint}`` with the entry's payload plus
``{"from": N, "size": 100, "jobs": true, "ddoKey": "refineSearch"}``; the
response is ``{<ddoKey>: {"totalHits": N, "data": {"jobs": [ ... ]}}}``.
The provider's SSRF allow-list for an entry is **the endpoint's own host**
(the operator configured it explicitly).
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from config import settings
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import MultiBoardATSProvider, looks_like_job
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, split_city_country, parse_posted_date,
)

_PAGE = 100
_MAX_PAGES = 12
_INTERN_TYPES = {"intern", "internship", "trainee", "apprentice"}


class PhenomProvider(MultiBoardATSProvider):
    name = "phenom"
    allowed_hosts = ()               # resolved per configured entry
    application_priority = 50
    ats_label = "phenom"

    def _board_tokens(self) -> list[dict]:
        return settings.phenom_boards_list

    def _fetch_board(self, entry: dict, ctx: JobSearchContext, *, limit, deadline, keep):
        endpoint = (entry.get("endpoint") or "").strip()
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            return []
        host = (parsed.hostname or "").lower()
        origin = f"{parsed.scheme}://{parsed.netloc}"
        company = clean_text(entry.get("company")) or host.split(".")[0]
        ddo = entry.get("ddoKey") or "refineSearch"
        raw_payload = entry.get("payload")
        base_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}

        offers: list[NormalizedOffer] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            body = {**base_payload, "from": offset, "size": _PAGE, "jobs": True, "ddoKey": ddo}
            payload = http.fetch_json(
                endpoint,
                method="POST",
                json_body=body,
                headers={"Content-Type": "application/json", "Referer": f"{origin}/"},
                allowed_hosts=(host,),
                accept_language=ctx.language,
                max_bytes=settings.ATS_HTTP_MAX_BYTES,
            )
            block = payload.get(ddo) if isinstance(payload, dict) else None
            if not isinstance(block, dict):
                break
            jobs = ((block.get("data") or {}).get("jobs")) or []
            if not jobs:
                break
            total = block.get("totalHits") or 0
            for row in jobs:
                offer = self._normalize(row, company, origin)
                if offer is not None and keep(offer):
                    offers.append(offer)
                    if len(offers) >= limit:
                        return offers
            offset += len(jobs)
            if isinstance(total, int) and offset >= total:
                break
        return offers

    @staticmethod
    def _normalize(row: dict, company: str, origin: str) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        job_id = str(row.get("jobId") or row.get("id") or "").strip()
        title = clean_text(row.get("title"))
        if not job_id or not title:
            return None
        url = (row.get("applyUrl") or "").strip()
        if url.endswith("/apply"):
            url = url[: -len("/apply")]
        if not url:
            url = f"{origin}/global/en/job/{job_id}"
        if not looks_like_job(title, url):
            return None

        location = (
            clean_text(row.get("location"))
            or clean_text(row.get("cityStateCountry"))
            or ", ".join(p for p in (
                clean_text(row.get("city")), clean_text(row.get("state")),
                clean_text(row.get("country")),
            ) if p) or None
        )
        city, country = split_city_country(location)
        description = html_to_text(row.get("description")) or html_to_text(row.get("descriptionSnippet"))
        emp = clean_text(row.get("type") or row.get("employmentType"))
        is_intern = (emp or "").lower() in _INTERN_TYPES
        raw_skills = row.get("ml_skills") or row.get("skills") or []
        skills = [clean_text(s) for s in raw_skills if isinstance(s, str) and clean_text(s)][:8]
        category = clean_text(row.get("category"))
        if category and category not in skills:
            skills = [category, *skills][:8]

        return NormalizedOffer(
            source="phenom",
            source_job_id=f"{company}:{job_id}",
            source_url=url,
            title=title,
            company=company,
            location=location,
            city=city,
            country=country,
            description=description,
            employment_type=emp,
            job_type=JobType.internship if is_intern else JobType(guess_job_type(title, description)),
            remote_type=guess_remote_type(title, description, location),
            experience_level=guess_experience_level(title, description),
            skills=skills,
            posted_at=parse_posted_date(row.get("postedDate") or row.get("dateCreated") or row.get("postedDateTime")),
            metadata={"ats": "phenom", "company": company},
        )
