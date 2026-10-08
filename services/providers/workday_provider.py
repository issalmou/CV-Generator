"""
Workday — public CXS JSON API (no key, no browser).

``POST https://{tenant}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs``
with ``{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}``
-> ``{"total": N, "jobPostings": [ ... ]}``. Paginates by offset (20/page).

``settings.workday_tenants_list`` holds the **full public board URLs**
(e.g. ``https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite``) —
tenant + site are parsed from each. The listing rows carry only
title / location / relative-posted-date; the description comes from a
bounded per-job CXS detail call (``…/wday/cxs/{company}/{site}{externalPath}``).
Case in the tenant path is preserved (Workday sites are case-sensitive).
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import MultiBoardATSProvider, host_matches, looks_like_job
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, to_utc,
)

_LOCALE_SEG = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")     # en, en-US, de-DE
_REQ_IN_PATH = re.compile(r"_((?:JR|R|REQ)[-_]?\d{3,})$", re.IGNORECASE)
_PAGE = 20
_MAX_PAGES = 15                                             # 300 postings / tenant hard cap


def _parse_board_url(url: str) -> tuple[str, str, str] | None:
    """(origin, company, site) from a full Workday board URL, or None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not host_matches(url, "myworkdayjobs.com") or parsed.scheme != "https":
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    company = (parsed.hostname or "").split(".")[0]
    site = ""
    for seg in (p for p in parsed.path.split("/") if p):
        if _LOCALE_SEG.match(seg):
            continue
        site = seg
        break
    if not company or not site:
        return None
    return origin, company, site


class WorkdayProvider(MultiBoardATSProvider):
    name = "workday"
    allowed_hosts = ("myworkdayjobs.com",)
    application_priority = 55
    ats_label = "workday"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("workday", settings.workday_tenants_list)

    def _fetch_board(self, token, ctx: JobSearchContext, *, limit, deadline, keep):
        parsed = _parse_board_url(token)
        if parsed is None:
            return []
        origin, company, site = parsed
        api = f"{origin}/wday/cxs/{company}/{site}/jobs"

        offers: list[NormalizedOffer] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            payload = http.fetch_json(
                api,
                method="POST",
                json_body={"appliedFacets": {}, "limit": _PAGE, "offset": offset, "searchText": ""},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                allowed_hosts=self.allowed_hosts,
                accept_language=ctx.language,
                max_bytes=settings.ATS_HTTP_MAX_BYTES,
            )
            postings = payload.get("jobPostings") if isinstance(payload, dict) else None
            if not postings:
                break
            total = payload.get("total") or 0
            for row in postings:
                offer = self._normalize(row, origin, company, site)
                if offer is not None and keep(offer):
                    offers.append(offer)
                    if len(offers) >= limit:
                        return offers
            offset += len(postings)
            if isinstance(total, int) and offset >= total:
                break
        return offers

    def _enrich(self, offers, ctx, *, deadline):
        self._bounded_enrich(offers, ctx, self._fetch_description, deadline=deadline)

    def _fetch_description(self, offer: NormalizedOffer) -> str | None:
        meta = offer.metadata or {}
        origin, company, site = meta.get("origin"), meta.get("company"), meta.get("site")
        ext_path = meta.get("external_path") or ""
        # externalPath is echoed from Workday's own JSON — keep it a same-host path
        if not (origin and company and site) or not ext_path.startswith("/") or "://" in ext_path:
            return None
        payload = http.fetch_json(
            f"{origin}/wday/cxs/{company}/{site}{ext_path}",
            headers={"Accept": "application/json"},
            allowed_hosts=self.allowed_hosts,
        )
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return None
        return html_to_text(info.get("jobDescription"))

    @staticmethod
    def _normalize(row: dict, origin: str, company: str, site: str) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        title = clean_text(row.get("title"))
        ext_path = (row.get("externalPath") or "").strip()
        if not title or not ext_path:
            return None
        url = f"{origin}/{site}{ext_path}"
        if not looks_like_job(title, url):
            return None

        bullets = [b for b in (row.get("bulletFields") or []) if isinstance(b, str)]
        req_id = next((b for b in bullets if re.search(r"\d", b)), None)
        if not req_id:
            m = _REQ_IN_PATH.search(ext_path)
            req_id = m.group(1) if m else ext_path.rsplit("/", 1)[-1]

        location = clean_text(row.get("locationsText"))
        # "Locations" / "2 Locations" is a placeholder, not a real place
        if location and re.match(r"^\d*\s*locations?$", location, re.IGNORECASE):
            location = None

        return NormalizedOffer(
            source="workday",
            source_job_id=f"{company}:{req_id}",
            source_url=url,
            title=title,
            company=company.replace("-", " ").title(),
            location=location,
            job_type=JobType(guess_job_type(title)),
            remote_type=guess_remote_type(title, location),
            experience_level=guess_experience_level(title),
            posted_at=(to_utc(row.get("startDate")) or to_utc(row.get("postedOn"))
                       or to_utc(row.get("postedOnDate"))),
            metadata={
                "ats": "workday",
                "origin": origin,
                "company": company,
                "site": site,
                "external_path": ext_path,
                "posted_relative": clean_text(row.get("postedOn")),
            },
        )
