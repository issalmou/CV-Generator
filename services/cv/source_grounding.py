"""
CV Assistant - CV Generation
Service: source_grounding

Post-Gemini validation (spec §12): every value the LLM returned for a
section is checked back against THAT section's own source text. Anything
not supported by the text is removed — fidelity to the document beats
completeness.

Concretely, for one section this module:
- drops any technology whose name does not appear in the section text
  (accent- and punctuation-insensitive, so a line-cut rejoin like
  "scikit-learn" from "scikit-\\nlearn" still matches);
- drops an education ``start_date`` / ``end_date`` whose 4-digit year is
  not in the education text (a resolved "current year" is exempt — it was
  produced locally, on purpose, from "Présent"/"Depuis");
- nulls an experience ``location`` that is merely a substring of the
  company name (a location inferred from the employer's name — §6);
- splits an education ``degree`` that swallowed the ``field``
  ("Master Systèmes ..." -> degree "Master", field "Systèmes ...").

100% local. No LLM calls. Every removal is reported so the caller can
lower confidence / add a validation issue.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# Degree keywords, longest first so "Licence d'Excellence" wins over "Licence".
_DEGREE_KEYWORDS: tuple[str, ...] = (
    "licence d'excellence", "licence d’excellence",
    "specialized technician diploma", "specialized technician",
    "technicien spécialisé", "technicien specialise",
    "diplôme d'ingénieur", "diplome d'ingenieur", "diplôme d’ingénieur",
    "master of science", "master of arts", "master of business administration",
    "bachelor of science", "bachelor of arts",
    "classe préparatoire", "classe preparatoire",
    "doctorat", "master", "licence", "baccalauréat", "baccalaureat", "bac",
    "bachelor", "ingénieur", "ingenieur",
    "ph.d.", "ph.d", "phd", "m.b.a.", "mba",
    "m.sc.", "m.sc", "msc", "b.sc.", "b.sc", "bsc",
    "m.a.", "b.a.", "b.eng.", "b.eng", "m.eng.", "m.eng", "b.tech.", "b.tech",
    "dut", "bts", "deug", "deust", "cpge", "certificat", "certificate",
    "diploma", "diplôme", "diplome", "brevet",
)
_FIELD_CONNECTORS = re.compile(
    r"^\s*(?:en|in|of|of the|of a|de|du|des|:|,|-|–|—|option|spécialité|"
    r"specialite|mention|parcours|filière|filiere|major(?:\s+in)?)\s+",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    """Lower-case, strip accents, drop every non-alphanumeric char."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _split_items(value: str) -> list[str]:
    """'Python, React / Node.js' -> ['Python', 'React', 'Node.js'] (never joins)."""
    return [p.strip(" .·") for p in re.split(r"\s*[,;]\s*|\s+/\s+|\s+•\s+", value) if p.strip(" .·")]


# ---------------------------------------------------------------------------
# Project repo / demo links (spec §4 / §24)
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]|]+"
    r"|(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|me|app|co|xyz|tech|pages?\.dev|"
    r"vercel\.app|netlify\.app|herokuapp\.com|streamlit\.app|fly\.dev|onrender\.com)"
    r"(?:/[^\s<>()\[\]|]*)?",
    re.IGNORECASE,
)
_GITHUB_REPO_RE = re.compile(r"github\.com/[^/\s]+/[^/\s#?]+", re.IGNORECASE)


def _url_tokens(url: str) -> set[str]:
    """Identifying words in a URL: repo name, sub-domain, path segments."""
    u = re.sub(r"^https?://", "", url.lower()).strip("/")
    u = re.sub(r"[?#].*$", "", u)
    host, _, path = u.partition("/")
    parts = [p for p in re.split(r"[/._\-]", f"{host} {path}") if p]
    stop = {"com", "org", "net", "io", "dev", "me", "app", "co", "www", "github",
            "gitlab", "vercel", "netlify", "streamlit", "herokuapp", "fly",
            "onrender", "pages", "git", "http", "https", "demo", "live", "repo",
            "code", "project", "app"}
    return {p for p in parts if len(p) >= 3 and p not in stop}


def ground_project_links(
    projects: list[dict[str, Any]],
    projects_text: str,
    extra_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Fill a project's ``github`` / ``demo`` from a URL that actually exists —
    in the PROJECTS text, or in a PDF link annotation the classifier put in
    ``others``. A ``github.com/<user>/<repo>`` URL goes to ``github``;
    anything else deployable goes to ``demo``. A bare ``github.com/<user>``
    (a profile, not a repo) is ignored here.

    Assignment: match the URL's identifying words to a project title; if
    that is ambiguous, fall back to the sole project that still has an
    empty slot. Never overwrites a value the model already produced, never
    invents a URL.
    """
    if not projects:
        return projects

    # normalise whatever the model already produced (scheme + trailing junk)
    for proj in projects:
        for slot in ("github", "demo"):
            v = (proj.get(slot) or "").strip().strip(".,;)")
            if not v:
                continue
            proj[slot] = v if re.match(r"^https?://", v, re.I) else "https://" + v.lstrip("/")

    candidates: list[str] = []
    for u in _URL_RE.findall(projects_text or ""):
        candidates.append(u)
    for u in extra_urls or []:
        if u:
            candidates.append(u)

    seen: set[str] = set()
    urls: list[str] = []
    for u in candidates:
        key = re.sub(r"^https?://", "", u.lower()).rstrip("/")
        if key and key not in seen:
            seen.add(key)
            urls.append(u if re.match(r"^https?://", u, re.I) else "https://" + u)

    if not urls:
        return projects

    titles = [(_norm(p.get("title") or ""), p) for p in projects]

    for url in urls:
        is_repo = bool(_GITHUB_REPO_RE.search(url))
        if "github.com" in url.lower() and not is_repo:
            continue                                   # profile link, not a project
        slot = "github" if is_repo else "demo"
        toks = _url_tokens(url)

        target = None
        # 1) URL words overlap a project title
        best = 0
        for norm_title, proj in titles:
            if not norm_title:
                continue
            score = sum(1 for t in toks if t in norm_title or norm_title in t)
            if score > best:
                best, target = score, proj
        # 2) otherwise, the only project still missing this slot
        if target is None:
            missing = [p for p in projects if not p.get(slot)]
            if len(missing) == 1:
                target = missing[0]
        if target is not None and not target.get(slot):
            target[slot] = url

    return projects


# ---------------------------------------------------------------------------
# Technologies
# ---------------------------------------------------------------------------

def ground_technologies(
    entries: list[dict[str, Any]],
    source_text: str,
    *,
    section: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Remove, from each entry's ``technologies`` list, any item that does not
    literally appear in ``source_text``. Returns ``(entries, removed)``
    where ``removed`` is the flat list of dropped technology names.
    """
    haystack = _norm(source_text or "")
    removed: list[str] = []
    for entry in entries:
        techs = entry.get("technologies") or []
        kept: list[str] = []
        for raw in techs:
            if not isinstance(raw, str) or not raw.strip():
                continue
            for tech in _split_items(raw):
                needle = _norm(tech)
                if needle and needle in haystack:
                    if tech not in kept:
                        kept.append(tech)
                else:
                    removed.append(tech)
        entry["technologies"] = kept
    if removed:
        logger.info(
            "[source_grounding] %s: dropped %d unsupported technolog%s: %s",
            section, len(removed), "y" if len(removed) == 1 else "ies", removed,
        )
    return entries, removed


def ground_skills(
    skills: list[str],
    source_text: str,
) -> tuple[list[str], list[str]]:
    """
    Drop any skill not present in the SKILLS section text — order and exact
    duplicates handling preserved. Returns ``(kept, removed)``.

    Matching is accent- and punctuation-insensitive so a PDF line-cut
    rejoin ("scikit-learn" from "scikit-\\nlearn") still matches, but a
    technology the model invented ("Django" when the text only says
    "Python") is removed.
    """
    haystack = _norm(source_text or "")
    kept: list[str] = []
    removed: list[str] = []
    for raw in skills or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        for skill in _split_items(raw):
            if _norm(skill) and _norm(skill) in haystack:
                if skill not in kept:
                    kept.append(skill)
            elif skill not in removed:
                removed.append(skill)
    if removed:
        logger.info("[source_grounding] skills: dropped %d unsupported: %s", len(removed), removed)
    return kept, removed


# ---------------------------------------------------------------------------
# Education dates
# ---------------------------------------------------------------------------

def ground_education_dates(
    entries: list[dict[str, Any]],
    education_text: str,
) -> tuple[list[dict[str, Any]], int]:
    """
    Null any education ``start_date`` / ``end_date`` whose year is absent
    from the education section text — the signature of a date borrowed
    from another entry or section (§11). A value equal to the current
    year that carries no year token of its own in the source is assumed to
    be a locally-resolved "Présent" and is kept.
    """
    years_in_text = set(_YEAR_RE.findall(education_text or ""))
    dropped = 0
    for entry in entries:
        for key in ("start_date", "end_date"):
            value = entry.get(key)
            if not value:
                continue
            value_years = _YEAR_RE.findall(str(value))
            if value_years and not any(y in years_in_text for y in value_years):
                entry[key] = None
                dropped += 1
    if dropped:
        logger.info("[source_grounding] education: nulled %d date(s) not present in the education text", dropped)
    return entries, dropped


def ground_experience_period(
    entries: list[dict[str, Any]],
    experience_text: str,
) -> tuple[list[dict[str, Any]], int]:
    """Null an experience ``period`` whose year(s) are absent from the experience text."""
    years_in_text = set(_YEAR_RE.findall(experience_text or ""))
    dropped = 0
    for entry in entries:
        period = entry.get("period")
        if not period:
            continue
        period_years = _YEAR_RE.findall(str(period))
        if period_years and not any(y in years_in_text for y in period_years):
            entry["period"] = None
            dropped += 1
    if dropped:
        logger.info("[source_grounding] experience: nulled %d period(s) not present in the experience text", dropped)
    return entries, dropped


# ---------------------------------------------------------------------------
# Experience location inferred from the company name (§6)
# ---------------------------------------------------------------------------

def strip_company_derived_location(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    dropped = 0
    for entry in entries:
        loc = (entry.get("location") or "").strip()
        company = (entry.get("company") or "").strip()
        if loc and company and _norm(loc) and _norm(loc) in _norm(company):
            entry["location"] = None
            dropped += 1
    if dropped:
        logger.info("[source_grounding] experience: nulled %d location(s) that were part of the company name", dropped)
    return entries, dropped


# ---------------------------------------------------------------------------
# Education degree / field split (§2)
# ---------------------------------------------------------------------------

def split_degree_field(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    When ``degree`` contains the whole "<qualification> <speciality>"
    phrase and ``field`` is empty, split it locally as a safety net behind
    the LLM prompt. Never invents a field: if nothing follows the
    qualification keyword, ``field`` stays ``None``.
    """
    for entry in entries:
        degree = (entry.get("degree") or "").strip()
        field = (entry.get("field") or "").strip()

        # Always drop a leading connector the model left on the field
        # ("en Informatique" -> "Informatique", "in Data Science" -> "Data Science").
        if field:
            stripped = _FIELD_CONNECTORS.sub("", field).strip(" -–—:,")
            if stripped and stripped != field:
                entry["field"] = field = stripped

        if not degree or field:
            continue

        low = degree.lower()
        for kw in _DEGREE_KEYWORDS:
            if low.startswith(kw):
                head = degree[: len(kw)].strip()
                tail = degree[len(kw):].strip()
                # a parenthetical right after the keyword is part of the
                # degree, not the field ("B.Sc. (Excellence) in AI").
                m = re.match(r"\(([^)]*)\)\s*", tail)
                if m:
                    head = f"{head} {m.group(0).strip()}"
                    tail = tail[m.end():]
                tail = _FIELD_CONNECTORS.sub("", tail).strip(" -–—:,")
                entry["degree"] = head
                entry["field"] = tail or None
                break
    return entries


# A qualifier the LLM sometimes drops from a degree ("Licence" instead of
# "Licence d'Excellence", "Bachelor" instead of "Bachelor of Science").
_DEGREE_QUALIFIERS: tuple[str, ...] = (
    "d'excellence", "d’excellence", "professionnelle", "professionnel",
    "of science", "of arts", "of business administration", "of engineering",
    "of technology", "of laws", "of philosophy", "(hons)", "(honours)",
    "(honors)", "(excellence)", "spécialisé", "specialise", "spécialisée",
)


def ground_education_degree(
    entries: list[dict[str, Any]], source_text: str
) -> list[dict[str, Any]]:
    """
    Re-expand a degree the model truncated: if the source text has
    ``"<degree> <qualifier>"`` where the entry only kept ``"<degree>"``,
    restore the qualifier — using the source's own casing. Never invents a
    qualifier that is not in the text; never shortens a degree.
    """
    low_src = source_text.lower()
    for entry in entries:
        degree = (entry.get("degree") or "").strip()
        if not degree:
            continue
        for q in _DEGREE_QUALIFIERS:
            want = f"{degree.lower()} {q}" if not q.startswith("(") else f"{degree.lower()} {q}"
            idx = low_src.find(want)
            if idx == -1:
                continue
            # copy the exact source span (keeps real casing / accents)
            entry["degree"] = source_text[idx: idx + len(want)].strip()
            # if the field now repeats the qualifier, trim it
            field = (entry.get("field") or "").strip()
            if field and field.lower().startswith(q):
                entry["field"] = field[len(q):].strip(" -–—:,") or None
            break
    return entries
