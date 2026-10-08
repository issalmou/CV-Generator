"""
Shared building blocks for the **public ATS JSON providers**
(``ashby`` · ``workday`` · ``oracle_hcm`` · ``smartrecruiters`` · ``rippling``
· ``phenom`` · ``talentbrew``).

Every one of these hits a documented, public, **no-auth** ATS board API
through the single HTTP chokepoint (:mod:`services.providers.http`) — no
browser, no login, no cookies, no per-tenant secret. Scraped free text is
run through :func:`services.providers.parsing.html_to_text` before it ever
leaves a provider, exactly like the existing providers.

``MultiBoardATSProvider`` factors out the loop shared by the token-list
ATS: iterate configured board tokens, fetch each, keep going when one
board 404s / is blocked, and only mark the provider unavailable when
*every* board failed (``blocked`` if they all denied access, otherwise
``temporarily_unavailable``). ``greenhouse``/``lever`` predate this helper
and are intentionally left as-is.
"""

from __future__ import annotations

import re
import time
from typing import Callable
from urllib.parse import urlparse

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import (
    JobProvider, ProviderBlocked, ProviderTemporarilyUnavailable,
)
from services.providers.parsing import (
    keyword_match, passes_context_filters, search_terms,
)

# a predicate the base gives ``_fetch_board`` so pagination can stop at
# ``limit`` *matching* offers instead of ``limit`` raw postings.
Keep = Callable[[NormalizedOffer], bool]

# ---------------------------------------------------------------------------
# URL helpers (ported from JobNavigator ``_shared/urls.py`` — strict hostname
# matching, never a substring test)
# ---------------------------------------------------------------------------

def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def host_matches(url: str | None, *domains: str) -> bool:
    """True iff ``url``'s hostname equals or is a sub-domain of any ``domains``."""
    host = host_of(url)
    if not host:
        return False
    for raw in domains:
        d = (raw or "").lower().strip().strip("/").lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


# ---------------------------------------------------------------------------
# "Is this actually a job posting?" — ported from JobNavigator
# ``_shared/filters._validate_job`` (kept lighter: structured ATS JSON rarely
# needs it, but the HTML-scraping providers — talentbrew — do).
# ---------------------------------------------------------------------------

GARBAGE_TITLES: frozenset[str] = frozenset({
    "apply", "apply now", "contact", "contact us", "search", "search jobs",
    "job search", "back", "next", "previous", "filter", "filters", "reset",
    "clear", "sign in", "sign up", "login", "log in", "register", "submit",
    "load more", "show more", "view all", "see all", "close", "menu",
    "home", "about", "about us", "privacy", "terms", "cookie", "cookies",
    "accept", "decline", "subscribe", "follow", "share", "save",
    "join talent network", "join our talent network", "talent network",
    "sign up for alerts", "sign up for job alerts", "job alerts",
    "create job alert", "set up job alert", "email me jobs",
    "explore careers", "explore opportunities", "why work here",
    "our culture", "our values", "benefits", "open roles",
    "learn more", "read more", "find out more", "get started",
    "all locations", "all departments", "all categories",
    "accessibility", "equal opportunity", "eeo", "privacy policy",
    "terms of use", "terms and conditions", "sitemap",
    "locations", "teams", "departments", "categories",
})

GARBAGE_SUBSTRINGS: tuple[str, ...] = (
    "join talent network", "join our talent", "talent community",
    "sign up for job alert", "create job alert", "email me jobs",
    "cookie settings", "cookie preferences", "privacy policy",
    "equal opportunity employer", "© 20",
)

LOCALE_NAMES: frozenset[str] = frozenset({
    "nederlands", "deutsch", "français", "español", "português",
    "italiano", "polski", "svenska", "norsk", "dansk", "suomi",
    "english", "english (us)", "english (uk)",
    "日本語", "한국어", "中文",
})

_NUMERIC_TITLE = re.compile(r"^[\d\s\-.]+$")

# A board *slug* (Ashby / SmartRecruiters / Rippling) — anything outside this
# set is rejected before it is spliced into a request path, so a mistyped or
# hostile config value can never traverse or reach another host.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# An ATS "id" echoed back from a provider's own JSON, before it goes into a
# detail-endpoint URL / finder clause.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def valid_slug(token: str | None) -> bool:
    return bool(token and _SLUG_RE.match(token.strip()))


def valid_id(value: str | None) -> bool:
    return bool(value and _ID_RE.match(str(value).strip()))


def looks_like_job(title: str | None, url: str | None) -> bool:
    """Reject navigation links / boilerplate that leak into a scraped list.

    Kept deliberately permissive on length (ATS titles like ``SRE`` are real)
    — the exact-match garbage sets do the heavy lifting."""
    t = (title or "").strip()
    if len(t) < 3:
        return False
    low = t.lower()
    if low in GARBAGE_TITLES or low in LOCALE_NAMES:
        return False
    if any(sub in low for sub in GARBAGE_SUBSTRINGS):
        return False
    if _NUMERIC_TITLE.match(t):
        return False
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def offer_matches(offer: NormalizedOffer, ctx: JobSearchContext, terms: list[str]) -> bool:
    """The context/keyword predicate applied to every ATS offer — the same
    check the base ``_search`` used to run only on the *first page* of a board.
    Pushed into the per-board pagination so a keyword search keeps reading
    pages until it has ``limit`` *matches*, not just ``limit`` raw postings."""
    if offer is None or not passes_context_filters(offer, ctx):
        return False
    if terms:
        blob = " ".join(x for x in (offer.title, offer.company, offer.description) if x)
        if not keyword_match(blob, terms):
            return False
    return True


def rank_enrichment_targets(
    offers: list[NormalizedOffer], ctx: JobSearchContext,
) -> list[NormalizedOffer]:
    """Order offers by how much the user is likely to care (deterministic,
    LLM-free) so a bounded enrichment budget is spent on the best matches
    first, not on whatever the board happened to return first. Tie-break on
    recency (a known recent ``posted_at`` beats an unknown / older one)."""
    from datetime import datetime, timezone

    from services.jobs.ranking import score as _rank_score

    floor = datetime.min.replace(tzinfo=timezone.utc)

    def _key(o: NormalizedOffer):
        posted = o.posted_at
        if posted is not None and posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        try:
            relevance = _rank_score(o, ctx)
        except Exception:  # noqa: BLE001 — scoring must never break enrichment
            relevance = 0.0
        return (relevance, posted or floor)

    return sorted(offers, key=_key, reverse=True)


# ---------------------------------------------------------------------------
# Multi-board base provider
# ---------------------------------------------------------------------------

class MultiBoardATSProvider(JobProvider):
    """Base for an ATS whose config is a list of company board tokens.

    Subclasses set ``name`` / ``allowed_hosts`` / ``ats_label`` and implement
    :meth:`_board_tokens` and :meth:`_fetch_board`. They may override
    :meth:`_enrich` (default: no-op) for bounded per-job description fetches.
    """

    ats_label: str = "ats"

    # -- subclass hooks -------------------------------------------------------

    def _board_tokens(self) -> list[str]:
        raise NotImplementedError

    def _fetch_board(
        self, token: str, ctx: JobSearchContext, *, limit: int, deadline: float,
        keep: "Keep",
    ) -> list[NormalizedOffer]:
        """Return this board's offers. ``keep(offer)`` is the base's
        context/keyword filter — a *paginating* provider applies it as it goes
        and stops once it has ``limit`` matches (so a keyword search reads
        deeper than the first page). A single-request provider may ignore it;
        the base re-applies ``keep`` afterwards regardless."""
        raise NotImplementedError

    def _enrich(self, offers: list[NormalizedOffer], ctx: JobSearchContext,
                *, deadline: float) -> None:
        """Optional bounded per-job enrichment. Default: nothing to do."""

    # -- shared machinery ---------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._board_tokens())

    def _search(self, ctx, *, limit, deadline):
        tokens = self._board_tokens()
        terms = search_terms(ctx) + ctx.skills
        keep: "Keep" = lambda offer: offer_matches(offer, ctx, terms)  # noqa: E731
        offers: list[NormalizedOffer] = []
        failures = blocked = 0
        last_reason = ""

        for token in tokens:
            if time.monotonic() >= deadline:
                break
            try:
                board_offers = self._fetch_board(
                    token, ctx, limit=limit - len(offers), deadline=deadline, keep=keep,
                )
            except http.AccessDenied as exc:
                failures += 1
                last_reason = exc.reason or "access denied"
                if "429" not in (exc.reason or ""):
                    blocked += 1
                continue
            except http.HttpError as exc:
                failures += 1
                last_reason = str(exc)[:80]
                continue

            for offer in board_offers:
                if not keep(offer):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    self._enrich(offers, ctx, deadline=deadline)
                    return offers, f"{self.ats_label}_api"

        if tokens and failures == len(tokens):
            if blocked == len(tokens):
                raise ProviderBlocked(
                    f"all {len(tokens)} {self.ats_label} boards denied access "
                    f"(last: {last_reason})"
                )
            raise ProviderTemporarilyUnavailable(
                f"all {len(tokens)} {self.ats_label} boards unreachable "
                f"(last: {last_reason})"
            )

        self._enrich(offers, ctx, deadline=deadline)
        return offers, f"{self.ats_label}_api"

    # -- helper for subclasses that enrich --------------------------------

    def _bounded_enrich(
        self,
        offers: list[NormalizedOffer],
        ctx: JobSearchContext,
        fetch_one: Callable[[NormalizedOffer], str | None],
        *,
        deadline: float,
    ) -> None:
        """Fill ``offer.description`` for up to ``ATS_ENRICH_MAX`` offers that
        lack one — **the most relevant first** (LLM-free ``jobs.ranking.score``),
        stopping at the deadline. Every fetch is best-effort: a failing detail
        endpoint never fails the search."""
        if not settings.ATS_ENRICH_DESCRIPTIONS:
            return
        candidates = rank_enrichment_targets(
            [o for o in offers if not o.description], ctx
        )
        attempts = 0
        cap = settings.ATS_ENRICH_MAX
        for offer in candidates:
            if attempts >= cap or time.monotonic() >= deadline:
                break
            if offer.description:
                continue
            attempts += 1
            try:
                text = fetch_one(offer)
            except http.HttpError:
                continue
            except Exception:  # noqa: BLE001 — enrichment must never break search
                continue
            if text:
                offer.description = text
