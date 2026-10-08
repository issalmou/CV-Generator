"""
CV Assistant - CV Generation
Service: SectionSplitter

Responsibilities:
- Split raw resume text into named sections (contact, summary, education,
  experience, projects, skills, certifications, languages, interests,
  personal_qualities)
- Detect common header variants (English + French) for each section
- Purely rule-based (regex / heuristics). No AI of any kind.

The splitter is intentionally conservative: anything it cannot confidently
assign to a known section header is kept under the section it currently
belongs to (or under "contact" if it appears before the first detected
header — this is typically the name/title block at the top of a resume).

Interests / personal qualities
-------------------------------
"interests" and "personal_qualities" are dedicated canonical sections.
Before this module recognised their headers, hobby/interest/personality
content (e.g. "Centres d'intérêt", "Personal Qualities") had no bucket of
its own and silently fell into whichever section was still open when it
appeared — most commonly leaking into "languages" or "contact" and
corrupting those extractions. Giving them their own buckets means that
content is captured separately and never pollutes another section, even
though neither bucket is structured further downstream (the pipeline
currently surfaces them as plain optional text, never sent to Gemini).
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical section names
# ---------------------------------------------------------------------------

SECTION_CONTACT = "contact"
SECTION_SUMMARY = "summary"
SECTION_EDUCATION = "education"
SECTION_EXPERIENCE = "experience"
SECTION_PROJECTS = "projects"
SECTION_SKILLS = "skills"
SECTION_CERTIFICATIONS = "certifications"
SECTION_LANGUAGES = "languages"
SECTION_INTERESTS = "interests"
SECTION_PERSONAL_QUALITIES = "personal_qualities"

CANONICAL_SECTIONS: tuple[str, ...] = (
    SECTION_CONTACT,
    SECTION_SUMMARY,
    SECTION_EDUCATION,
    SECTION_EXPERIENCE,
    SECTION_PROJECTS,
    SECTION_SKILLS,
    SECTION_CERTIFICATIONS,
    SECTION_LANGUAGES,
    SECTION_INTERESTS,
    SECTION_PERSONAL_QUALITIES,
)

# ---------------------------------------------------------------------------
# Header variants (English + French), matched case-insensitively against a
# normalised (accent-stripped) version of each line.
# ---------------------------------------------------------------------------

_SECTION_HEADER_PATTERNS: dict[str, list[str]] = {
    SECTION_SUMMARY: [
        r"^summary$",
        r"^professional\s+summary$",
        r"^profile$",
        r"^professional\s+profile$",
        r"^about\s*(me)?$",
        r"^career\s+summary$",
        r"^objective$",
        r"^career\s+objective$",
        r"^resume\s+summary$",
        r"^profil$",
        r"^profil\s+professionnel$",
        r"^a\s+propos$",
        r"^a\s+propos\s+de\s+moi$",
        r"^objectif$",
        r"^objectif\s+professionnel$",
        r"^objectif\s+de\s+carriere$",
        r"^synthese$",
        r"^resume$",           # "Résumé" (accent-stripped)
        r"^presentation$",
        r"^en\s+bref$",
    ],
    SECTION_EDUCATION: [
        r"^education$",
        r"^academic\s+background$",
        r"^academic\s+history$",
        r"^educational\s+background$",
        r"^qualifications$",
        r"^formation$",
        r"^formations$",
        r"^formation\s+academique$",
        r"^parcours\s+academique$",
        r"^diplomes?$",
        r"^etudes$",
        r"^scolarite$",
        # "Éducation" -> accent-stripped to "education", already covered by
        # the first pattern above, but kept explicit variants below for the
        # forms commonly seen on French/Europass templates.
        r"^education\s+et\s+formation$",
        r"^cursus$",
        r"^cursus\s+scolaire$",
        r"^parcours\s+scolaire$",
    ],
    SECTION_EXPERIENCE: [
        r"^experience$",
        r"^experiences$",
        r"^work\s+experience$",
        r"^professional\s+experience$",
        r"^employment\s+history$",
        r"^career\s+history$",
        r"^work\s+history$",
        r"^experience\s+professionnelle$",
        r"^experiences\s+professionnelles$",
        r"^parcours\s+professionnel$",
        r"^stages?(\s+et\s+experiences?)?$",
        r"^emplois?$",
        r"^experience\s+de\s+travail$",
        r"^historique\s+professionnel$",
        r"^vie\s+professionnelle$",
    ],
    SECTION_PROJECTS: [
        r"^projects?$",
        r"^personal\s+projects?$",
        r"^academic\s+projects?$",
        r"^side\s+projects?$",
        r"^key\s+projects?$",
        r"^selected\s+projects?$",
        r"^projets?$",
        r"^projets?\s+personnels?$",
        r"^projets?\s+academiques?$",
        r"^realisations?$",
    ],
    SECTION_SKILLS: [
        r"^skills?$",
        r"^technical\s+skills?$",
        r"^competenc(e|ies)$",
        r"^core\s+competenc(e|ies)$",
        r"^technologies$",
        r"^tech\s+stack$",
        r"^tools?(\s+(and|&)\s+technologies)?$",
        r"^competences$",
        r"^competences\s+techniques$",
        r"^competences\s+cles$",
        r"^outils$",
    ],
    SECTION_CERTIFICATIONS: [
        r"^certifications?$",
        r"^licenses?(\s+(and|&)\s+certifications?)?$",
        r"^certificates?$",
        r"^certifications?\s+professionnelles?$",
        r"^certificats?$",
    ],
    SECTION_LANGUAGES: [
        r"^languages?$",
        r"^language\s+skills?$",
        r"^langues?$",
        r"^langues?\s+parlees?$",
    ],
    SECTION_INTERESTS: [
        r"^interests?$",
        r"^hobbies(\s+(and|&)\s+interests?)?$",
        r"^hobbies?$",
        r"^personal\s+interests?$",
        r"^activities$",
        r"^extracurricular\s+activities$",
        r"^centres?\s+d.?interet(s)?$",
        r"^centre\s+d.?interet(s)?$",
        r"^loisirs?$",
        r"^activites?$",
        r"^activites?\s+extra[\s\-]?professionnelles?$",
        r"^divers$",
    ],
    SECTION_PERSONAL_QUALITIES: [
        r"^personal\s+qualities$",
        r"^personal\s+qualities\s+and\s+skills$",
        r"^qualities$",
        r"^soft\s+skills?$",
        r"^strengths?$",
        r"^character\s+traits?$",
        r"^qualites?$",
        r"^qualites?\s+personnelles?$",
        r"^qualites?\s+humaines?$",
        r"^qualites?\s+et\s+competences$",
        r"^savoir[\s\-]?etre$",
        r"^traits?\s+de\s+caractere$",
        r"^atouts?$",
        r"^points?\s+forts?$",
    ],
    SECTION_CONTACT: [
        r"^contact$",
        r"^contact\s+information$",
        r"^contact\s+details$",
        r"^personal\s+information$",
        r"^personal\s+details$",
        r"^coordonnees$",
        r"^informations?\s+personnelles?$",
        r"^informations?\s+de\s+contact$",
    ],
}

# Pre-compile all patterns once.
_COMPILED_PATTERNS: dict[str, list[re.Pattern]] = {
    section: [re.compile(p, re.IGNORECASE) for p in patterns]
    for section, patterns in _SECTION_HEADER_PATTERNS.items()
}

# A line is only considered a "header candidate" if it is short (resume
# section headers are almost never long sentences) — this avoids false
# positives where a bullet point happens to contain the word "Skills".
_MAX_HEADER_LINE_LENGTH = 45
_MAX_HEADER_WORD_COUNT = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    """Remove common French accents so 'Compétences' matches 'competences'."""
    replacements = {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "É": "e", "È": "e", "Ê": "e",
        "À": "a", "Â": "a",
        "Î": "i", "Ï": "i",
        "Ô": "o",
        "Ù": "u", "Û": "u",
        "Ç": "c",
    }
    for accented, plain in replacements.items():
        text = text.replace(accented, plain)
    return text


# ---------------------------------------------------------------------------
# Language detection (lightweight, local, no AI)
# ---------------------------------------------------------------------------

# Distinctive, high-frequency tokens for each language. Deliberately small
# and unambiguous (no English/French homographs) so the score is reliable
# even on short resumes. Used only to *detect* the resume's language for
# response metadata — never to alter the extracted content itself.
_FR_MARKERS: tuple[str, ...] = (
    " et ", " de ", " des ", " le ", " la ", " les ", " du ", " pour ",
    " avec ", " dans ", "experience professionnelle", "competences",
    "formation", "diplome", "langues", "projets", "realisations",
    "responsable", "annee", "stage",
)
_EN_MARKERS: tuple[str, ...] = (
    " and ", " the ", " of ", " for ", " with ", " in ",
    "professional experience", "skills", "education", "degree",
    "languages", "projects", "achievements", "responsible", "year",
    "internship",
)


def detect_language(text: str) -> str:
    """
    Best-effort detection of whether a resume is written in French or
    English, based on the frequency of a small set of unambiguous marker
    words/phrases. Purely local heuristic — no AI call.

    Returns "fr" or "en". Defaults to "en" when the text is empty or the
    signal is too weak to decide (tie or no markers found at all), since
    English is the more common fallback for technical resumes.
    """
    if not text or not text.strip():
        return "en"

    normalized = " " + _strip_accents(text.lower()) + " "
    fr_score = sum(normalized.count(marker) for marker in _FR_MARKERS)
    en_score = sum(normalized.count(marker) for marker in _EN_MARKERS)

    if fr_score > en_score:
        return "fr"
    return "en"


def _normalize_header_candidate(line: str) -> str:
    """Normalise a line for header matching: strip punctuation/markers, accents, case."""
    text = _strip_accents(line.strip())
    # Remove common decorative markers around headers, e.g. "== SKILLS ==", "### Education"
    text = re.sub(r"^[#=\-*_•\s]+", "", text)
    text = re.sub(r"[#=\-*_•\s:]+$", "", text)
    text = text.strip()
    return text.lower()


def _match_section_header(line: str) -> str | None:
    """Return the canonical section name if this line looks like a section header, else None."""
    raw = line.strip()
    if not raw:
        return None
    if len(raw) > _MAX_HEADER_LINE_LENGTH:
        return None
    if len(raw.split()) > _MAX_HEADER_WORD_COUNT:
        return None

    candidate = _normalize_header_candidate(raw)
    if not candidate:
        return None

    for section, patterns in _COMPILED_PATTERNS.items():
        for pattern in patterns:
            if pattern.match(candidate):
                return section
    return None


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SplitResult:
    """Result of splitting a resume into sections."""
    sections: dict[str, str] = field(default_factory=dict)
    detected_headers: list[tuple[str, str]] = field(default_factory=list)  # (raw_header, canonical_section)

    def to_dict(self) -> dict[str, str]:
        return dict(self.sections)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SectionSplitter:
    """Splits raw resume text into canonical sections using header detection."""

    def split(self, text: str) -> dict[str, str]:
        """
        Split raw resume text into canonical sections.

        Parameters
        ----------
        text:
            Raw resume text (output of ResumeTextExtractor).

        Returns
        -------
        dict[str, str]
            Mapping of canonical section name -> section body text.
            Keys always present (possibly empty string):
            contact, summary, education, experience, projects, skills,
            certifications, languages, interests, personal_qualities.
        """
        result = self.split_detailed(text)
        return result.to_dict()

    def split_detailed(self, text: str) -> SplitResult:
        """Same as split(), but also returns which raw header lines were matched."""
        sections: dict[str, list[str]] = {name: [] for name in CANONICAL_SECTIONS}
        detected_headers: list[tuple[str, str]] = []

        if not text or not text.strip():
            logger.warning("[SectionSplitter] Empty input text – returning empty sections.")
            return SplitResult(sections={k: "" for k in CANONICAL_SECTIONS}, detected_headers=[])

        lines = text.split("\n")

        # Everything before the first recognised header is treated as
        # "contact" (name, title, header block at the top of the resume).
        current_section = SECTION_CONTACT

        for line in lines:
            matched_section = _match_section_header(line)
            if matched_section is not None:
                current_section = matched_section
                detected_headers.append((line.strip(), matched_section))
                logger.debug(
                    "[SectionSplitter] Header detected: '%s' -> %s",
                    line.strip(),
                    matched_section,
                )
                continue  # the header line itself is not part of the body

            sections[current_section].append(line)

        joined = {
            name: "\n".join(body_lines).strip("\n")
            for name, body_lines in sections.items()
        }

        n_detected = len(detected_headers)
        logger.info(
            "[SectionSplitter] Split complete | headers_detected=%d | sections_non_empty=%d",
            n_detected,
            sum(1 for v in joined.values() if v.strip()),
        )

        return SplitResult(sections=joined, detected_headers=detected_headers)
