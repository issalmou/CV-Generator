"""
TalentBrew (TMP Worldwide / Radancy) — legacy AJAX search endpoint.

Career sites on TalentBrew (BlackRock, Intuit, PwC, …) expose a
``GET {origin}/search-jobs/results?...`` endpoint that returns
``{"results": "<html fragment>", "totalHits": N}``. The fragment is a list
of ``<a href="/job/...">`` anchors; we parse title + URL (+ a location span
when present).

``TALENTBREW_BOARDS`` is a CSV of the **full** results URLs (the operator
copies one from the site's network tab). Each URL's own host is its SSRF
allow-list. Paginates with ``&CurrentPage=N``.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.ats_common import MultiBoardATSProvider, looks_like_job
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    split_city_country,
)

_PAGE_SIZE = 15
_MAX_PAGES = 12
_LOC_HINT = re.compile(r"(location|city|region|area)", re.IGNORECASE)


def _with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["CurrentPage"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


class TalentBrewProvider(MultiBoardATSProvider):
    name = "talentbrew"
    allowed_hosts = ()               # resolved per configured URL
    application_priority = 45
    ats_label = "talentbrew"

    def _board_tokens(self) -> list[str]:
        return resolved_tokens("talentbrew", settings.talentbrew_boards_list)

    def _fetch_board(self, board_url: str, ctx: JobSearchContext, *, limit, deadline, keep):
        parsed = urlparse(board_url)
        host = (parsed.hostname or "").lower()
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme != "https" or not host or "/search-jobs/results" not in parsed.path.lower():
            return []

        offers: list[NormalizedOffer] = []
        seen: set[str] = set()
        for page in range(1, _MAX_PAGES + 1):
            if time.monotonic() >= deadline:
                break
            result = http.fetch(
                _with_page(board_url, page),
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
                allowed_hosts=(host,),
                accept_language=ctx.language,
                max_bytes=settings.ATS_HTTP_MAX_BYTES,
            )
            import json as _json
            try:
                payload = _json.loads(result.text) if result.text else {}
            except (ValueError, _json.JSONDecodeError) as exc:
                raise http.HttpError("malformed TalentBrew JSON") from exc
            html = payload.get("results") or ""
            total = payload.get("totalHits")
            page_offers = self._parse_fragment(html, origin, host)
            new = [o for o in page_offers if o.source_job_id not in seen]
            if not new:
                break
            progressed = False
            for o in new:
                seen.add(o.source_job_id)
                progressed = True
                if keep(o):
                    offers.append(o)
                    if len(offers) >= limit:
                        return offers
            if not progressed:
                break
            if isinstance(total, int) and page * _PAGE_SIZE >= total:
                break
        return offers

    @staticmethod
    def _parse_fragment(html: str, origin: str, host: str) -> list[NormalizedOffer]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        out: list[NormalizedOffer] = []
        for a in soup.select('a[href^="/job/"], a[href*="/job/"]'):
            href = a.get("href") or ""
            if "/job/" not in href:
                continue
            url = urljoin(origin + "/", href)
            if urlparse(url).hostname != host:
                continue
            heading = a.find(["h1", "h2", "h3", "h4"])
            title = clean_text(heading.get_text(" ")) if heading else clean_text(a.get_text(" ").split("\n")[0])
            if not title or not looks_like_job(title, url):
                continue
            location = None
            container = a.find_parent("li") or a
            for span in container.find_all(["span", "div", "p"]):
                cls = " ".join(span.get("class") or [])
                if _LOC_HINT.search(cls):
                    location = clean_text(span.get_text(" "))
                    if location:
                        break
            city, country = split_city_country(location)
            job_path = urlparse(url).path
            out.append(NormalizedOffer(
                source="talentbrew",
                source_job_id=f"{host}:{job_path.rstrip('/').rsplit('/', 1)[-1] or job_path}",
                source_url=url,
                title=title,
                company=host.split(".")[0].replace("-", " ").title(),
                location=location,
                city=city,
                country=country,
                job_type=JobType(guess_job_type(title)),
                remote_type=guess_remote_type(title, location),
                experience_level=guess_experience_level(title),
                metadata={"ats": "talentbrew", "host": host},
            ))
        return out
