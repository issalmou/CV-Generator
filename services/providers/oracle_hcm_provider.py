"""
Oracle HCM (Candidate Experience) — public REST API (no key, no browser).

``GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions``
``?onlyData=true&expand=requisitionList.secondaryLocations``
``&finder=findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,limit=200,offset=N``
-> ``{"items": [{"requisitionList": [ ... ], "TotalJobsCount": N}]}``.

``settings.oracle_hcm_sites_list`` holds the **full public board URLs**
(``https://<host>.oraclecloud.com/hcmUI/CandidateExperience/en/sites/<site>/requisitions``).
Site numbers and URL paths are **case-sensitive** — they are passed through
verbatim (Oracle's WAF 403s a lower-cased path). The description comes from
a bounded per-job ``recruitingCEJobRequisitionDetails`` call.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import (
    MultiBoardATSProvider, host_matches, looks_like_job, valid_id,
)
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, split_city_country, parse_posted_date,
)

_LIST = (
    "{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    "?onlyData=true&expand=requisitionList.secondaryLocations"
    "&finder=findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,limit={limit},offset={offset}"
)
_DETAIL = (
    '{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails'
    '?expand=all&onlyData=true&finder=ById;Id="{job_id}",siteNumber={site}'
)
_PAGE = 200
_MAX_PAGES = 10
_DESC_FIELDS = (
    "ExternalDescriptionStr", "ExternalResponsibilitiesStr",
    "ExternalQualificationsStr", "CorporateDescriptionStr",
)
_REMOTE = {"REMOTE": "remote", "HYBRID": "hybrid", "ONSITE": "onsite", "ON-SITE": "onsite"}


def _parse_board_url(url: str) -> tuple[str, str, str, str] | None:
    """(ui_origin, api_origin, site, path_prefix) or None. api_origin == ui_origin
    for direct *.oraclecloud.com URLs (the only shape we accept)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or not host_matches(url, "oraclecloud.com"):
        return None
    ui_origin = f"{parsed.scheme}://{parsed.netloc}"
    segs = parsed.path.split("/")
    site = ""
    for i, seg in enumerate(segs):
        if seg == "sites" and i + 1 < len(segs):
            site = segs[i + 1]
            break
    if not site:
        return None
    prefix = "/hcmUI/CandidateExperience" if "hcmUI/CandidateExperience" in parsed.path else ""
    return ui_origin, ui_origin, site, prefix


class OracleHcmProvider(MultiBoardATSProvider):
    name = "oracle_hcm"
    allowed_hosts = ("oraclecloud.com",)
    application_priority = 55
    ats_label = "oracle_hcm"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("oracle_hcm", settings.oracle_hcm_sites_list)

    def _fetch_board(self, token, ctx: JobSearchContext, *, limit, deadline, keep):
        parsed = _parse_board_url(token)
        if parsed is None:
            return []
        ui_origin, api_origin, site, prefix = parsed
        host = urlparse(api_origin).hostname or api_origin

        offers: list[NormalizedOffer] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            payload = http.fetch_json(
                _LIST.format(origin=api_origin, site=site, limit=_PAGE, offset=offset),
                allowed_hosts=self.allowed_hosts,
                accept_language=ctx.language,
                max_bytes=settings.ATS_HTTP_MAX_BYTES,
            )
            items = payload.get("items") if isinstance(payload, dict) else None
            if not items:
                break
            block = items[0] if isinstance(items[0], dict) else {}
            reqs = block.get("requisitionList") or []
            total = block.get("TotalJobsCount") or 0
            for row in reqs:
                offer = self._normalize(row, ui_origin, api_origin, site, prefix, host)
                if offer is not None and keep(offer):
                    offers.append(offer)
                    if len(offers) >= limit:
                        return offers
            offset += len(reqs)
            if not reqs or (isinstance(total, int) and offset >= total):
                break
        return offers

    def _enrich(self, offers, ctx, *, deadline):
        self._bounded_enrich(offers, ctx, self._fetch_description, deadline=deadline)

    def _fetch_description(self, offer: NormalizedOffer) -> str | None:
        meta = offer.metadata or {}
        origin, site, req_id = meta.get("api_origin"), meta.get("site"), meta.get("req_id")
        if not (origin and site and req_id) or not valid_id(req_id) or not valid_id(site):
            return None
        payload = http.fetch_json(
            _DETAIL.format(origin=origin, job_id=req_id, site=site),
            allowed_hosts=self.allowed_hosts,
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not items or not isinstance(items[0], dict):
            return None
        parts = [html_to_text(items[0].get(f)) for f in _DESC_FIELDS]
        joined = "\n\n".join(p for p in parts if p).strip()
        return joined[:8000] or None

    @staticmethod
    def _normalize(row, ui_origin, api_origin, site, prefix, host) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        req_id = str(row.get("Id") or "").strip()
        title = clean_text(row.get("Title"))
        if not req_id or not title:
            return None
        url = f"{ui_origin}{prefix}/en/sites/{site}/job/{req_id}"
        if not looks_like_job(title, url):
            return None

        loc = clean_text(row.get("PrimaryLocation"))
        city, country = split_city_country(loc)
        country = country or clean_text(row.get("PrimaryLocationCountry"))
        workplace = str(row.get("WorkplaceType") or row.get("WorkplaceTypeCode") or "").upper()

        return NormalizedOffer(
            source="oracle_hcm",
            source_job_id=f"{host}:{req_id}",
            source_url=url,
            title=title,
            location=loc,
            city=city,
            country=country,
            job_type=JobType(guess_job_type(title)),
            remote_type=_REMOTE.get(workplace) or guess_remote_type(title, loc),
            experience_level=guess_experience_level(title),
            posted_at=parse_posted_date(row.get("PostedDate") or row.get("ExternalPostedStartDate")),
            metadata={
                "ats": "oracle_hcm",
                "api_origin": api_origin,
                "site": site,
                "req_id": req_id,
            },
        )
