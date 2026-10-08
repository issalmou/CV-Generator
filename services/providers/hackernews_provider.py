"""Hacker News "Ask HN: Who is hiring?" — via the free Algolia API (no key).

1. find the newest ``author_whoishiring`` story,
2. fetch its comment tree (``/api/v1/items/<id>``),
3. treat each top-level comment as a posting; parse the conventional first
   line ``Company | Role | Location | REMOTE | ...``.

Niche + monthly, but a genuinely free public source. Comment text is
untrusted -> converted to plain text; only used as data.
"""

from __future__ import annotations

import html as _html
import re

from schemas.jobs import JobSearchContext, JobType, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    guess_experience_level, guess_job_type, guess_remote_type, html_to_text,
    keyword_match, search_terms, split_city_country, parse_posted_date,
)

_SEARCH = "https://hn.algolia.com/api/v1/search"
_ITEM = "https://hn.algolia.com/api/v1/items/"
_URL_RE = re.compile(r"https?://[^\s<>\"]+")


class HackerNewsProvider(JobProvider):
    name = "hackernews"
    allowed_hosts = ("hn.algolia.com",)
    application_priority = 20

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        story = http.fetch_json(
            _SEARCH,
            params={"tags": "story,author_whoishiring", "hitsPerPage": 3},
            allowed_hosts=self.allowed_hosts, accept_language=ctx.language,
        )
        hits = [h for h in (story.get("hits") or [])
                if "who is hiring" in (h.get("title") or "").lower()]
        if not hits:
            return [], "hn_algolia"
        story_id = hits[0].get("objectID")
        tree = http.fetch_json(f"{_ITEM}{story_id}", allowed_hosts=self.allowed_hosts,
                               accept_language=ctx.language)

        terms = search_terms(ctx) + ctx.skills
        offers: list[NormalizedOffer] = []
        for child in tree.get("children") or []:
            offer = self._normalize(child, story_id)
            if offer is None:
                continue
            if ctx.job_type in (JobType.job, JobType.internship) and offer.job_type != ctx.job_type.value:
                continue
            blob = " ".join([offer.title, offer.company or "", offer.description or ""])
            if terms and not keyword_match(blob, terms):
                continue
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers, "hn_algolia"

    @staticmethod
    def _normalize(comment: dict, story_id: str) -> NormalizedOffer | None:
        comment_id = comment.get("id")
        raw = comment.get("text")
        if not comment_id or not raw or comment.get("author") is None:
            return None
        text = html_to_text(_html.unescape(raw))
        if not text or len(text) < 20:
            return None
        first_line = text.splitlines()[0]
        segments = [s.strip() for s in re.split(r"\s*[|·—–]\s*|\s{2,}", first_line) if s.strip()]
        company = segments[0][:120] if segments else None
        title = segments[1][:200] if len(segments) > 1 else first_line[:200]
        location = None
        for seg in segments[2:5]:
            if re.search(r"[A-Za-z]", seg) and not seg.upper().startswith("HTTP"):
                location = seg[:120]
                break
        city, country = split_city_country(location)
        apply_urls = _URL_RE.findall(text)
        return NormalizedOffer(
            source="hackernews",
            source_job_id=str(comment_id),
            source_url=f"https://news.ycombinator.com/item?id={comment_id}",
            title=title or "Role (see description)",
            company=company,
            location=location,
            city=city,
            country=country,
            description=text[:6000],
            job_type=guess_job_type(first_line, text),
            remote_type=guess_remote_type(first_line, text),
            experience_level=guess_experience_level(first_line, text),
            posted_at=parse_posted_date(comment.get("created_at")),
            metadata={
                "story_id": story_id,
                "apply_urls": apply_urls[:5],
                "hn_author": comment.get("author"),
            },
        )
