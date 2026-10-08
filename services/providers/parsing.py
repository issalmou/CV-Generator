"""
Platform-agnostic parsing / normalisation helpers shared by providers.

Nothing here knows about a specific site — it deals in HTML, JSON-LD,
microdata, RSS and free text. All scraped HTML goes through
:func:`html_to_text` before it is stored or shown: **we never persist or
render provider HTML**.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

_MAX_DESCRIPTION = 8000
_WS = re.compile(r"[ \t ]+")
_MULTINL = re.compile(r"\n{3,}")

_INTERNSHIP_HINTS = re.compile(
    r"\b(intern|internship|stage|stagiaire|apprenti|apprentice|"
    r"working student|werkstudent|praktikum|trainee)\b",
    re.IGNORECASE,
)
_REMOTE_HINTS = re.compile(r"\b(remote|t[ée]l[ée]travail|work from home|wfh|anywhere)\b", re.I)
_HYBRID_HINTS = re.compile(r"\b(hybrid|hybride|partially remote|remote-friendly)\b", re.I)
_ONSITE_HINTS = re.compile(r"\b(on[- ]?site|sur site|in[- ]office|présentiel|presentiel)\b", re.I)

_EXPERIENCE_MAP = [
    (re.compile(r"\b(intern|student|stagiaire|étudiant|etudiant)\b", re.I), "student"),
    (re.compile(r"\b(entry[- ]level|graduate|débutant|junior)\b", re.I), "junior"),
    (re.compile(r"\b(mid[- ]level|confirmé|confirme|intermediate)\b", re.I), "mid"),
    (re.compile(r"\b(senior|sr\.?)\b", re.I), "senior"),
    (re.compile(r"\b(lead|principal|staff|head of|director)\b", re.I), "lead"),
]

_SALARY_RE = re.compile(
    r"(?P<cur>[$€£]|USD|EUR|GBP|CAD)?\s*"
    r"(?P<lo>\d{2,3}(?:[ ,.]\d{3})+|\d{2,7})(?P<lok>\s*[kK])?"
    r"(?:\s*(?:[-–—]|to)\s*[$€£]?\s*(?P<hi>\d{2,3}(?:[ ,.]\d{3})+|\d{2,7})(?P<hik>\s*[kK])?)?"
)
_CUR_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP"}


# ---------------------------------------------------------------------------
# HTML -> safe text
# ---------------------------------------------------------------------------

def html_to_text(fragment: str | None, *, max_len: int = _MAX_DESCRIPTION) -> str | None:
    if not fragment:
        return None
    soup = BeautifulSoup(fragment, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = _WS.sub(" ", text)
    text = _MULTINL.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    if not text:
        return None
    return text[:max_len]


def clean_text(value: str | None, *, max_len: int = 512) -> str | None:
    if not value:
        return None
    value = _WS.sub(" ", str(value)).strip()
    return value[:max_len] or None


# ---------------------------------------------------------------------------
# Structured data
# ---------------------------------------------------------------------------

def json_ld_blocks(html: str) -> list[Any]:
    out: list[Any] = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        for candidate in _split_json_objects(raw):
            try:
                out.append(json.loads(candidate))
            except (json.JSONDecodeError, ValueError):
                continue
    return out


def _split_json_objects(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    # some pages concatenate multiple JSON objects in one script tag
    if raw.startswith("["):
        return [raw]
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                parts.append(raw[start:i + 1])
    return parts or [raw]


def _iter_nodes(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)


def first_jobposting(html: str) -> dict | None:
    for block in json_ld_blocks(html):
        for node in _iter_nodes(block):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if any(str(t).lower() == "jobposting" for t in types):
                return node
    return None


# ---------------------------------------------------------------------------
# RSS 2.0 (stdlib) — no feedparser dependency
# ---------------------------------------------------------------------------

def parse_rss(xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for item in root.iter("item"):
        entry: dict[str, str] = {}
        for child in item:
            tag = child.tag.split("}")[-1]
            entry[tag] = (child.text or "").strip()
        if entry:
            items.append(entry)
    return items


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

def guess_job_type(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t)
    return "internship" if _INTERNSHIP_HINTS.search(blob) else "job"


def guess_remote_type(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    if _HYBRID_HINTS.search(blob):
        return "hybrid"
    if _REMOTE_HINTS.search(blob):
        return "remote"
    if _ONSITE_HINTS.search(blob):
        return "onsite"
    return None


def guess_experience_level(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    for pattern, level in _EXPERIENCE_MAP:
        if pattern.search(blob):
            return level
    return None


def parse_salary(text: str | None) -> tuple[float | None, float | None, str | None]:
    if not text:
        return None, None, None
    m = _SALARY_RE.search(text)
    if not m:
        return None, None, None
    def _num(raw: str | None, is_k: str | None) -> float | None:
        if not raw:
            return None
        digits = raw.replace(",", "").replace(" ", "").replace(".", "")
        try:
            val = float(digits)
        except ValueError:
            return None
        if is_k:
            val *= 1000
        return val
    lo, hi = _num(m.group("lo"), m.group("lok")), _num(m.group("hi"), m.group("hik"))
    cur = m.group("cur")
    if cur:
        cur = _CUR_SYMBOL.get(cur, cur.upper())
    elif re.search(r"€|eur", text, re.I):
        cur = "EUR"
    elif re.search(r"\$|usd", text, re.I):
        cur = "USD"
    return lo, hi, cur


# Referral / analytics query params — noise for identity, dropped from the
# canonical URL. A *functional* param that identifies the posting
# (``gh_jid``, ``jobId``, ``id`` …) is kept, so two different jobs on the same
# path are not collapsed into one.
_TRACKING_PARAMS = frozenset({
    "src", "source", "ref", "refid", "refsrc", "refsource", "referrer",
    "origin", "from", "channel", "medium", "trk", "trackingid", "tracking_id",
    "gh_src", "lever-source", "lever_source", "lever-origin", "lever_origin",
    "lever-source[]", "recommendedflavor", "savedsearchid", "position",
    "pagenum", "distance", "ebp", "refid", "rid",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid",
    "twclid", "dclid", "zanpid", "_ga", "_gl", "_hsenc", "_hsmi", "mkt_tok",
})


def normalize_url(url: str | None) -> str:
    """Canonical URL for identity/dedup: scheme+host lower-cased, trailing
    slash and fragment removed, referral/analytics query params stripped,
    the rest kept and **sorted** (stable across param order)."""
    if not url:
        return ""
    p = urlparse(url.strip())
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = re.sub(r"/+$", "", p.path) or "/"
    kept = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query = urlencode(sorted(kept)) if kept else ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def split_city_country(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    parts = [p.strip() for p in re.split(r"[,/·|]", location) if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return (None, parts[0]) if len(parts[0]) <= 3 or parts[0].isupper() else (parts[0], None)
    return parts[0], parts[-1]


# "posted N ago" — only the *explicit, unambiguous* forms are interpreted;
# a vague "recently" / "new" stays None (never invent a precise date).
_REL_UNIT_SECONDS = {
    "second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600,
    "day": 86400, "week": 604800, "month": 2592000, "year": 31536000,
    # FR
    "seconde": 1, "minute_fr": 60, "heure": 3600, "jour": 86400,
    "semaine": 604800, "mois": 2592000, "an": 31536000, "année": 31536000,
}
_REL_RE = re.compile(
    r"\b(\d+)\+?\s*(seconde?s?|sec|minutes?|min|heures?|hours?|hr|jours?|days?|"
    r"semaines?|weeks?|mois|months?|ans?|années?|years?)\b",
    re.IGNORECASE,
)
# A date this far out is a data error (a corrupted epoch, a "2099" typo). A
# real ``validThrough`` is < 1 year ahead, so 400 days is a safe outer bound;
# ``posted_at`` is additionally clamped to "not the future" by ``posted_at()``.
_ABSURD_FUTURE = 400 * 86400
_MIN_YEAR = 2000
_POSTED_FUTURE_SKEW = 2 * 86400


def _relative_to_utc(text: str, now: datetime) -> datetime | None:
    low = text.lower()
    if "just now" in low or "moments ago" in low or "à l'instant" in low:
        return now
    if "today" in low or "aujourd" in low or "hui" in low:
        return now
    if "yesterday" in low or "hier" in low:
        return now - timedelta(days=1)
    m = _REL_RE.search(low)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).rstrip("s").lower()
    unit = {
        "seconde": "second", "heure": "hour", "jour": "day", "semaine": "week",
        "mois": "month", "an": "year", "année": "year", "annee": "year",
    }.get(unit, unit)
    secs = _REL_UNIT_SECONDS.get(unit)
    if not secs:
        return None
    return now - timedelta(seconds=n * secs)


def to_utc(value: Any) -> datetime | None:
    """Best-effort parse of a date / datetime / epoch / **explicit** relative
    string into an aware UTC datetime. Returns ``None`` when the value cannot
    be resolved reliably — a missing date is always preferred over a guess.
    A parsed date before year 2000, or absurdly far in the future (corrupted
    epoch / typo), is rejected. Use :func:`posted_at` for a *posting* date,
    which additionally rejects the future."""
    now = datetime.now(timezone.utc)

    def _sane(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if dt.year < _MIN_YEAR or dt > now + timedelta(seconds=_ABSURD_FUTURE):
            return None
        return dt

    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return _sane(value)
    if isinstance(value, (int, float)):
        try:
            epoch = float(value)
            if abs(epoch) > 1e11:          # milliseconds (e.g. Lever's createdAt)
                epoch /= 1000.0
            return _sane(datetime.fromtimestamp(epoch, tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    # bare numeric string -> epoch (seconds or ms)
    if re.fullmatch(r"\d{9,14}", text):
        return to_utc(int(text))

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
    ):
        try:
            return _sane(datetime.strptime(text, fmt))
        except ValueError:
            continue
    # ISO-8601 fallback
    try:
        return _sane(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    # explicit relative ("Posted 5 days ago", "il y a 3 semaines")
    return _sane(_relative_to_utc(text, now))


def parse_posted_date(value: Any) -> datetime | None:
    """:func:`to_utc` for a *posting* date — a job cannot be posted in the
    future, so anything past a small clock-skew tolerance is dropped to
    ``None`` (never fabricated)."""
    dt = to_utc(value)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return None if dt > now + timedelta(seconds=_POSTED_FUTURE_SKEW) else dt


def keyword_match(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    low = (text or "").lower()
    return any(t.lower() in low for t in terms if t)


def search_terms(ctx) -> list[str]:  # ctx: schemas.jobs.JobSearchContext
    """Free-text terms a keyword-only provider can filter on."""
    out: list[str] = []
    if ctx.query:
        out.append(ctx.query)
    out.extend(ctx.keywords)
    return [t for t in out if t]


def passes_context_filters(offer, ctx) -> bool:  # offer: NormalizedOffer
    """Generic post-fetch filtering applied uniformly to every provider's
    output — job/internship type and excluded companies. (Ranking, not
    filtering, handles the softer preferences.)"""
    jt = getattr(ctx.job_type, "value", None)
    if jt in ("job", "internship") and offer.job_type != jt:
        return False
    if ctx.excluded_companies and offer.company:
        low = offer.company.lower()
        if any(x.lower() in low for x in ctx.excluded_companies):
            return False
    return True
