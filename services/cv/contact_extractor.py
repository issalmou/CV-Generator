"""
CV Assistant - CV Generation
Service: ContactExtractor

Responsibilities:
- Extract contact information (name, email, phone, LinkedIn, GitHub,
  portfolio) from the "contact" section text (and, as a fallback, the
  full resume text) using regex only.
- NEVER calls Gemini / any LLM. Purely deterministic.

Design notes
------------
The "name" is the hardest field to extract reliably without an LLM,
because there's no universal marker for it. We use a conservative
heuristic: scan the first few lines of the contact block (the header
block at the top of the resume) for a line that has a plausible
"Firstname Lastname"-like shape, in any of these forms:

  1. Title Case on one line:           "John Doe"
  2. UPPERCASE on one line:             "JOHN DOE"
  3. Split across two consecutive lines (common on Canva/designer
     templates where the first and last name are styled differently,
     or simply wrap onto two lines due to a narrow sidebar column):
       "JOHN"
       "DOE"
     or
       "John"
       "Doe"

If no such line is found, name is None and the caller (validator /
pipeline) can flag low confidence instead of guessing.
"""

import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Phone numbers: optional leading +, country code, then groups of digits
# separated by spaces, dots or dashes. Deliberately permissive (international
# formats vary a lot) but anchored to avoid matching plain integers like years.
_PHONE_PATTERN = re.compile(
    r"(?<![\w@/.])"
    r"(\+?\d{1,3}[\s.\-]?)?"     # country code
    r"(\d[\s.\-])?"              # a lone leading digit (French mobile: "+33 6 …")
    r"(\(?\d{2,4}\)?[\s.\-]?){2,5}\d{2,4}"
    r"(?![\w@/.])"
)

# Accepts every written form (spec §4): with/without scheme, with/without
# "www."/"fr."/country subdomain, with/without a trailing slash.
_LINKEDIN_PATTERN = re.compile(
    r"(?:https?://)?(?:[a-z]{2,4}\.)?linkedin\.com/(?:in|pub|profile)/[A-Za-z0-9\-_/%.]+/?",
    re.IGNORECASE,
)

_GITHUB_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9\-_.]+/?",
    re.IGNORECASE,
)

# Generic portfolio / personal website: any http(s) URL that is not
# LinkedIn/GitHub, or a bare domain ending in a common TLD that looks like
# a personal site (e.g. "issalmou.dev", "jdoe.me").
_GENERIC_URL_PATTERN = re.compile(
    r"(?:https?://)[A-Za-z0-9\-.]+\.[A-Za-z]{2,}(?:/[A-Za-z0-9\-_/%.]*)?",
    re.IGNORECASE,
)

_BARE_PORTFOLIO_PATTERN = re.compile(
    r"\b[A-Za-z0-9\-]+\.(?:dev|me|io|app|design|tech|xyz)\b",
    re.IGNORECASE,
)

# Labelled "Nationality: X" / "Nationalité : X" / "Citizenship: X" line.
# Only a labelled value is trusted — nationality is never guessed from a
# place name or a language.
_NATIONALITY_PATTERN = re.compile(
    r"(?:nationalit[ée]|nationality|citizenship|citoyennet[ée])\s*[:\-]\s*"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ '\-/]{1,40})",
    re.IGNORECASE,
)

# Labelled "Address: X" / "Adresse : X" / "Location: X" / "Based in: X" line.
_ADDRESS_LABEL_PATTERN = re.compile(
    r"(?:address|adresse|location|localisation|based\s+in|domicili[ée]\s+[àa]|"
    r"r[ée]sidence|ville)\s*[:\-]\s*(.+)",
    re.IGNORECASE,
)

# An UNLABELLED address / location line on a modern contact bar — an icon
# then "City, Country" (optionally "Street, City, Region, Country"). Every
# comma-separated part is 1-4 capitalised words (a leading street number is
# allowed). No '@', no URL, no long digit run (that would be a phone).
_GEO_TOKEN = r"(?:\d{1,5}\s+)?[A-ZÀ-Þ][\wÀ-ÿ'’.\-]*(?:[ \-][A-Za-zÀ-ÿ][\wÀ-ÿ'’.\-]*){0,3}"
_GEO_LINE_PATTERN = re.compile(
    rf"^\s*{_GEO_TOKEN}(?:\s*,\s*{_GEO_TOKEN}){{1,3}}\s*$"
)

# --- Name patterns -----------------------------------------------------
#
# A plausible "human name" line, in three shapes. All are deliberately
# bounded to 2-4 "words" total to avoid matching section headers or
# sentences. None of these match strings containing digits.

# 1) Title Case, one line: "John Doe", "Jean-Pierre Dupont", "Marie-Claire
#    Anne Bernard". Each space-separated word is one or more capitalised
#    parts joined by hyphens (so the capital *after* an internal hyphen is
#    allowed — very common in French given names).
_NAME_LINE_PATTERN = re.compile(
    r"^[A-ZÀ-Ý][a-zà-ÿ']*(?:-[A-ZÀ-Ý][a-zà-ÿ']*)*"
    r"(?:\s+[A-ZÀ-Ý][a-zà-ÿ']*(?:-[A-ZÀ-Ý][a-zà-ÿ']*)*){1,3}$"
)

# 2) Fully UPPERCASE, one line: "JOHN DOE", "JEAN-PIERRE DUPONT"
#    Each token is upper-case letters only (plus internal hyphen/apostrophe).
_NAME_LINE_PATTERN_UPPER = re.compile(
    r"^[A-ZÀ-Ý][A-ZÀ-Ý'\-]*(?:\s+[A-ZÀ-Ý][A-ZÀ-Ý'\-]*){1,3}$"
)

# 3) A single name token on its own line (either case), used to detect a
#    name that has been split across two consecutive lines, e.g.:
#       JOHN
#       DOE
#    Requires at least 2 letters so single initials ("J.") aren't treated
#    as a full first/last name on their own.
_SINGLE_NAME_TOKEN_PATTERN = re.compile(
    r"^[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'\-]+$"
)

_NAME_NOISE_KEYWORDS = (
    "curriculum", "resume", "cv", "cover letter", "linkedin", "github",
    "email", "phone", "address", "contact", "tel", "tél",
)

# Common all-caps section/document headers that are single tokens and could
# otherwise be mistaken for a one-word name fragment (e.g. "PROFILE" on its
# own line right above the real name in some templates).
_NAME_TOKEN_NOISE = {
    "profile", "summary", "contact", "resume", "cv", "curriculum",
    "education", "experience", "skills", "languages", "projects",
    "certifications", "interests", "objective", "about",
}

# Only the first N lines of the contact block are considered candidates for
# the name. This mirrors how resumes are actually laid out (name is always
# at/near the top) and avoids accidentally matching a capitalized phrase
# deeper in the document (e.g. a job title or company name).
_MAX_NAME_SEARCH_LINES = 6

# Whole-document name scan (fallback for two-column templates) looks only
# this far down — the name is always near the visual top even when the
# splitter reads a sidebar first.
_MAX_DOC_NAME_SCAN_LINES = 40


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ContactExtractor:
    """Extracts contact fields from resume text using regex only. No AI."""

    def extract(
        self,
        contact_section: str,
        full_text: str = "",
        links: "object | None" = None,
    ) -> dict[str, str | None]:
        """
        Extract contact information.

        Parameters
        ----------
        contact_section:
            Text of the "contact" section (the header block at the top of
            the resume, as produced by SectionSplitter).
        full_text:
            Full resume text, used as a fallback search space.
        links:
            Optional ``link_extractor.ClassifiedLinks`` from the PDF's link
            annotations. Per spec §24 an annotation URL is authoritative
            for linkedin / github / portfolio / email — it is used exactly
            as stored, and never overridden by a weaker text match.

        Returns
        -------
        dict
            Keys: name, email, phone, linkedin, github, portfolio, address,
            nationality. Each value is a string or None. NEVER invented;
            `address` / `nationality` only from an explicitly labelled line.
        """
        search_space = contact_section or ""
        fallback_space = full_text or ""

        link_linkedin = getattr(links, "linkedin", None)
        link_github = getattr(links, "github", None)
        link_portfolio = getattr(links, "portfolio", None)
        link_email = getattr(links, "email", None)

        email = link_email or self._extract_first(_EMAIL_PATTERN, search_space, fallback_space)

        linkedin = link_linkedin
        if not linkedin:
            m = self._extract_first(_LINKEDIN_PATTERN, search_space, fallback_space)
            linkedin = self._clean_url(m) if m else None

        github = link_github
        if not github:
            m = self._extract_first(_GITHUB_PATTERN, search_space, fallback_space)
            github = self._clean_url(m) if m else None

        portfolio = link_portfolio
        if not portfolio:
            m = self._extract_portfolio(search_space, fallback_space, linkedin, github)
            portfolio = self._clean_url(m) if m else None

        phone = self._extract_phone(search_space, fallback_space)
        name = self._extract_name(search_space) or self._extract_name_from_document(fallback_space)
        address = (
            self._extract_labelled(_ADDRESS_LABEL_PATTERN, search_space, fallback_space)
            or self._extract_geo_address(search_space)
        )
        nationality = self._extract_labelled(_NATIONALITY_PATTERN, search_space, fallback_space)

        result = {
            "name": name,
            "email": email,
            "phone": phone,
            "linkedin": linkedin,
            "github": github,
            "portfolio": portfolio,
            "address": address,
            "nationality": nationality,
        }

        logger.info(
            "[ContactExtractor] Extracted | name=%s | email=%s | phone=%s | "
            "linkedin=%s | github=%s | portfolio=%s | address=%s | nationality=%s",
            bool(name), bool(email), bool(phone),
            bool(linkedin), bool(github), bool(portfolio),
            bool(address), bool(nationality),
        )
        return result

    # ------------------------------------------------------------------
    # Field-specific extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_first(pattern: re.Pattern, primary: str, fallback: str) -> str | None:
        match = pattern.search(primary)
        if match:
            return match.group(0)
        if fallback:
            match = pattern.search(fallback)
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _extract_labelled(pattern: re.Pattern, primary: str, fallback: str) -> str | None:
        """
        Return capture group 1 of the first match of a "Label: value"
        pattern, cleaned of trailing separators. Searches the contact block
        first, then the whole resume. Returns None when no labelled line is
        present — the value is never inferred.
        """
        for space in (primary, fallback):
            if not space:
                continue
            match = pattern.search(space)
            if match:
                value = match.group(1).strip().strip(" ,;|-")
                # Guard against capturing a following field on the same line
                # (e.g. "Address: X | Phone: ...").
                value = re.split(r"\s[|•]\s|\s{2,}", value)[0].strip()
                if value:
                    return value
        return None

    @staticmethod
    def _extract_geo_address(contact_section: str) -> str | None:
        """
        An unlabelled "City, Country" line on the contact bar (icon-only
        modern CVs). Only the first few contact lines are considered, and
        each line is examined segment-by-segment (icon / '|' / wide gap),
        so it is never confused with a section body.
        """
        if not contact_section:
            return None
        seen_nonempty = 0
        for i, line in enumerate(contact_section.split("\n")):
            if not line.strip():
                if seen_nonempty:
                    break               # contact bar ends at the first blank line
                continue
            seen_nonempty += 1
            if seen_nonempty > 5:
                break
            for seg in re.split(r"\s*[|;•·]\s*|\s{2,}", line):
                seg = seg.strip(" \t.,")
                # drop a lone leading icon glyph / substituted letter
                # ("+ Rabat, …", "I Casablanca, …") before a real place name
                seg = re.sub(r"^(?:[^\s\w(]|[A-Za-z])\s+(?=[A-ZÀ-Þ0-9])", "", seg).strip()
                if len(seg) < 4 or len(seg) > 80:
                    continue
                if "@" in seg or "http" in seg.lower() or "www." in seg.lower():
                    continue
                if re.search(r"\d[\d\s.\-]{5,}\d", seg):        # phone-like
                    continue
                if _GEO_LINE_PATTERN.match(seg):
                    return seg
        return None

    def _extract_phone(self, primary: str, fallback: str) -> str | None:
        for space in (primary, fallback):
            if not space:
                continue
            for line in space.split("\n"):
                # Header / contact bars pack email + phone + urls on one
                # line, separated by '|', a bullet, an icon glyph or just a
                # wide gap — examine each segment on its own so an email or
                # URL elsewhere on the line doesn't hide the phone number.
                for segment in re.split(r"\s*[|;•·►▪]\s*|\s{2,}", line):
                    segment = segment.strip("  \t.,")
                    if not segment:
                        continue
                    if _EMAIL_PATTERN.search(segment) or "http" in segment.lower() \
                            or "linkedin.com" in segment.lower() or "github.com" in segment.lower():
                        continue
                    match = _PHONE_PATTERN.search(segment)
                    if match:
                        candidate = match.group(0).strip()
                        digit_count = sum(c.isdigit() for c in candidate)
                        # Require a realistic minimum digit count for a phone number
                        if digit_count >= 7:
                            return candidate
        return None

    # Hosts that are never a personal portfolio (social, job boards, free mail).
    _PORTFOLIO_BLOCKLIST = (
        "linkedin.com", "github.com", "gmail.", "outlook.", "hotmail.", "yahoo.",
        "live.", "icloud.", "proton", "facebook.", "instagram.", "twitter.",
        "x.com", "youtube.", "tiktok.", "indeed.", "wa.me", "t.me",
    )

    def _extract_portfolio(
        self,
        primary: str,
        fallback: str,
        linkedin: str | None,
        github: str | None,
    ) -> str | None:
        for space in (primary, fallback):
            if not space:
                continue
            for match in _GENERIC_URL_PATTERN.finditer(space):
                url = match.group(0)
                if any(b in url.lower() for b in self._PORTFOLIO_BLOCKLIST):
                    continue
                return url
            bare_match = _BARE_PORTFOLIO_PATTERN.search(space)
            if bare_match:
                return bare_match.group(0)
        return None

    def _extract_name(self, contact_section: str) -> str | None:
        """
        Heuristic, in priority order, scanning only the first
        `_MAX_NAME_SEARCH_LINES` non-empty lines of the contact block:

          1. A single line that already looks like a full name, either
             Title Case ("John Doe") or UPPERCASE ("JOHN DOE").
          2. Two consecutive single-token lines that together look like a
             first name + last name split across lines (e.g. a narrow
             sidebar column wrapping "JOHN" / "DOE" onto separate lines).

        Never invents a name: returns None if nothing plausible is found.
        """
        if not contact_section:
            return None

        candidate_lines = self._collect_candidate_lines(contact_section)

        # Pass 0: a name split across the first two lines (very common on
        # sidebar / Canva headers: "ISSALMOU" / "ADAAICHE" / "Job Title").
        # This has to run before Pass 1 so a Title-Case *job title* sitting
        # just below the split name is not returned as the name.
        if (
            len(candidate_lines) >= 2
            and self._is_name_token(candidate_lines[0])
            and self._is_name_token(candidate_lines[1])
        ):
            return self._normalize_name_casing(
                f"{candidate_lines[0]} {candidate_lines[1]}"
            )

        # Pass 1: single-line full name (Title Case or UPPERCASE).
        for candidate in candidate_lines:
            if _NAME_LINE_PATTERN.match(candidate) or _NAME_LINE_PATTERN_UPPER.match(candidate):
                return self._normalize_name_casing(candidate)

        # Pass 2: name split across two consecutive single-token lines.
        for i in range(len(candidate_lines) - 1):
            first_token = candidate_lines[i]
            second_token = candidate_lines[i + 1]
            if self._is_name_token(first_token) and self._is_name_token(second_token):
                combined = f"{first_token} {second_token}"
                return self._normalize_name_casing(combined)

        return None

    def _extract_name_from_document(self, full_text: str) -> str | None:
        """
        Last-resort name search over the WHOLE resume text, for two-column /
        sidebar templates where the name sits in the main column and the
        section splitter (which reads the sidebar first) pushes it well
        past the contact block.

        Deliberately high-precision to avoid turning a random capitalised
        phrase into a name:

          A. two consecutive ALL-CAPS single-token lines immediately
             followed by a Title/UPPER-case "role" line
             (e.g. "ISSALMOU" / "ADAAICHE" / "Développeur Full Stack") —
             the canonical Canva/designer header block; OR
          B. two consecutive ALL-CAPS single-token lines on their own.

        Only the first `_MAX_DOC_NAME_SCAN_LINES` non-empty lines are
        considered. Returns None (never a guess) when nothing matches.
        """
        if not full_text:
            return None

        lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]
        lines = lines[:_MAX_DOC_NAME_SCAN_LINES]

        for i in range(len(lines) - 1):
            a, b = lines[i], lines[i + 1]
            if not (self._is_upper_name_token(a) and self._is_upper_name_token(b)):
                continue
            follower = lines[i + 2] if i + 2 < len(lines) else ""
            looks_like_role = bool(
                _NAME_LINE_PATTERN.match(follower) or _NAME_LINE_PATTERN_UPPER.match(follower)
            )
            # Case A (role follows) is very safe; case B is accepted too but
            # only when neither token is a common resume word.
            if looks_like_role or (
                a.lower() not in _NAME_TOKEN_NOISE and b.lower() not in _NAME_TOKEN_NOISE
            ):
                return self._normalize_name_casing(f"{a} {b}")
        return None

    @staticmethod
    def _is_upper_name_token(token: str) -> bool:
        """One ALL-CAPS word (letters only, min 2 chars), not a section-header word."""
        if not token.isupper():
            return False
        if not re.fullmatch(r"[A-ZÀ-Ý][A-ZÀ-Ý'\-]{1,}", token):
            return False
        return token.lower() not in _NAME_TOKEN_NOISE

    def _collect_candidate_lines(self, contact_section: str) -> list[str]:
        """Return up to `_MAX_NAME_SEARCH_LINES` cleaned, noise-filtered lines."""
        candidates: list[str] = []
        for line in contact_section.split("\n"):
            if len(candidates) >= _MAX_NAME_SEARCH_LINES:
                break
            stripped = line.strip()
            if not stripped:
                continue
            if _EMAIL_PATTERN.search(stripped) or "http" in stripped.lower():
                continue
            if any(kw in stripped.lower() for kw in _NAME_NOISE_KEYWORDS):
                continue
            if _PHONE_PATTERN.fullmatch(stripped):
                continue
            candidates.append(stripped)
        return candidates

    @staticmethod
    def _is_name_token(token: str) -> bool:
        """True if this single line plausibly holds one half of a split name."""
        if not _SINGLE_NAME_TOKEN_PATTERN.match(token):
            return False
        if len(token) < 2:
            return False
        if token.lower() in _NAME_TOKEN_NOISE:
            return False
        return True

    @staticmethod
    def _normalize_name_casing(name: str) -> str:
        """
        Convert an ALL-CAPS name to Title Case for consistent output
        ("JOHN DOE" -> "John Doe"). Names that are already mixed/Title
        Case are returned unchanged so accented/hyphenated forms the
        person already wrote correctly aren't altered.
        """
        if name.isupper():
            # capitalise the first letter after a space, hyphen OR apostrophe
            # so "JEAN-PIERRE O'CONNOR" -> "Jean-Pierre O'Connor"
            return re.sub(
                r"(^|[\s\-'’])(\w)",
                lambda m: m.group(1) + m.group(2).upper(),
                name.lower(),
            )
        return name

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_url(url: str) -> str:
        url = url.strip().rstrip(".,;)")
        if url and not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url.lstrip("/")
        return url
