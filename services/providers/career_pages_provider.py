"""
CareerPagesProvider — renders a curated list of **public** company career
pages with the shared headless browser and extracts schema.org
``JobPosting`` structured data from the rendered DOM.

Used for JS-only career pages that expose no JSON API (Greenhouse / Lever
have their own no-browser providers). ``settings.career_page_urls_list``
holds the URLs; empty list ⇒ provider disabled. It renders **public pages
only** — no login, no credentials, and never a LinkedIn URL.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers.browser_base import BrowserJobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, json_ld_blocks, keyword_match, passes_context_filters,
    search_terms, split_city_country, parse_posted_date, to_utc,
)

logger = logging.getLogger(__name__)

_WAIT = "script[type='application/ld+json'], main, [data-testid], .job, .opening"
_BLOCKED_HOSTS = ("linkedin.com",)   # never render LinkedIn with the browser


class CareerPagesProvider(BrowserJobProvider):
    name = "career_pages"
    application_priority = 50

    @property
    def enabled(self) -> bool:
        return super().enabled and bool(settings.career_page_urls_list)

    def _render_and_parse(self, ctx: JobSearchContext, *, limit: int):
        terms = search_terms(ctx) + ctx.skills
        offers: list[NormalizedOffer] = []
        rendered = 0

        for url in settings.career_page_urls_list:
            host = (urlparse(url).hostname or "").lower()
            if not host or any(host == b or host.endswith("." + b) for b in _BLOCKED_HOSTS):
                logger.warning("[career_pages] skipping disallowed URL host %r", host)
                continue
            try:
                html = self.get_page(
                    url, allowed_hosts=(host,), wait_selector=_WAIT,
                    timeout=settings.BROWSER_PAGE_LOAD_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001 — one page must not break the rest
                logger.warning("[career_pages] %s render failed: %s", host, type(exc).__name__)
                continue
            rendered += 1
            for offer in self._extract(html, url, host):
                if offer is None or not passes_context_filters(offer, ctx):
                    continue
                blob = " ".join([offer.title, offer.company or "", offer.description or ""])
                if terms and not keyword_match(blob, terms):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "career_pages_browser"

        return offers, "career_pages_browser" if rendered else None

    @staticmethod
    def _extract(html: str, page_url: str, host: str) -> list[NormalizedOffer]:
        out: list[NormalizedOffer] = []
        postings: list[dict] = []
        for block in json_ld_blocks(html):
            for node in _iter(block):
                types = node.get("@type")
                types = [types] if isinstance(types, str) else (types or [])
                if any(str(t).lower() == "jobposting" for t in types):
                    postings.append(node)
        for node in postings:
            offer = CareerPagesProvider._normalize(node, page_url, host)
            if offer:
                out.append(offer)
        return out

    @staticmethod
    def _normalize(node: dict, page_url: str, host: str) -> NormalizedOffer | None:
        title = node.get("title") or node.get("name")
        if not title:
            return None
        url = node.get("url") or node.get("sameAs") or page_url
        org = node.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else None
        loc = node.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        location = ", ".join(str(x) for x in [
            addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry"),
        ] if x) or None
        city, country = split_city_country(location)
        description = html_to_text(node.get("description"))
        salary = node.get("baseSalary") or {}
        val = (salary.get("value") or {}) if isinstance(salary, dict) else {}
        ext_id = node.get("identifier")
        if isinstance(ext_id, dict):
            ext_id = ext_id.get("value")
        ext_id = str(ext_id) if ext_id else _url_id(url)
        emp = node.get("employmentType")
        emp = emp[0] if isinstance(emp, list) and emp else emp
        return NormalizedOffer(
            source="career_pages",
            source_job_id=f"{host}:{ext_id}",
            source_url=url,
            title=clean_text(title) or "Untitled",
            company=clean_text(company) or host.split(".")[0].title(),
            location=clean_text(location),
            city=city,
            country=country,
            description=description,
            employment_type=clean_text(str(emp)) if emp else None,
            job_type=guess_job_type(title, description, str(emp) if emp else None),
            remote_type=guess_remote_type(title, description, location),
            salary_min=_num(val.get("minValue")),
            salary_max=_num(val.get("maxValue")),
            salary_currency=salary.get("currency") if isinstance(salary, dict) else None,
            experience_level=guess_experience_level(title, description),
            posted_at=parse_posted_date(node.get("datePosted")),
            expires_at=to_utc(node.get("validThrough")),
            metadata={"career_page": page_url},
        )


def _iter(node):
    if isinstance(node, list):
        for x in node:
            yield from _iter(x)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter(v)


def _url_id(url: str) -> str:
    return (urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url)[:64]


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
