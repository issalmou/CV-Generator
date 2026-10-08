"""Step 2 inputs — build the EXACT production prompts for each benchmark task
by importing the production prompt builders (read-only). No prompt is copied
or modified; we call the same `_build_prompt(...)` the app calls.

Each task exposes: id, kind (structured|writing|chat), prompt (str),
json_schema (dict|None), max_tokens (int), and a `check(text)` scorer hook
name. The scoring itself lives in score.py.
"""
from __future__ import annotations

import json
import os

from cv_models import CVProfile
from services.cv.ats_optimizer import ATSOptimizer
from services.cv.education_parser import EducationParser
from services.cv.experience_parser import ExperienceParser
from services.jobs.preference_service import JobPreferenceExtractor
from services.cv.letter_generator import LetterGenerator
from services.cv.profile_analyzer import ProfileAnalyzer
from services.cv.project_parser import ProjectParser
from services.cv.section_splitter import SectionSplitter
from services.cv.skills_parser import SkillsParser

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_CV_TEXT = open(os.path.join(FIX, "sample_cv.txt"), encoding="utf-8").read()

JOB_DESCRIPTION = (
    "Senior Backend Engineer (H/F) - Casablanca. Nous recherchons un ingenieur "
    "backend confirme pour concevoir et faire evoluer nos services de donnees "
    "distribues a fort trafic. Missions : conception d'APIs REST performantes, "
    "orchestration de conteneurs avec Kubernetes, pipelines CI/CD, observabilite "
    "(metriques, tracing), optimisation PostgreSQL. Stack : Python, FastAPI, "
    "PostgreSQL, Redis, Docker, Kubernetes, AWS, Terraform. Un plus : Kafka, gRPC, "
    "experience du mentorat. Anglais professionnel requis."
)

# A validated CVProfile matching the sample CV (what the frontend would submit).
CV_PROFILE = CVProfile.model_validate({
    "name": "Mehdi Bennani",
    "email": "mehdi.bennani@example.com",
    "phone": "+212 6 61 22 33 44",
    "linkedin": "linkedin.com/in/mehdibennani",
    "github": "github.com/mbennani",
    "address": "Casablanca, Maroc",
    "professional_summary": ("Ingenieur logiciel backend avec 4 ans d'experience sur des "
                             "plateformes de donnees et des APIs a fort trafic. Specialise "
                             "en Python, systemes distribues et cloud."),
    "education": [
        {"institution": "INSEA", "degree": "Diplome d'Ingenieur d'Etat", "field": "Informatique",
         "start_date": "2017", "end_date": "2020"},
    ],
    "experience": [
        {"company": "Atlas Data", "position": "Ingenieur Backend Senior", "period": "Mars 2022 - Present",
         "location": "Casablanca",
         "achievements": ["Concu et livre un service de facturation traitant 2M de transactions/mois",
                          "Reduit la latence p95 de l'API de 800ms a 210ms via un cache Redis et l'optimisation des requetes",
                          "Encadre 2 developpeurs juniors et mis en place la revue de code systematique"],
         "technologies": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "AWS"]},
        {"company": "Beta Solutions", "position": "Developpeur Backend", "period": "Septembre 2020 - Fevrier 2022",
         "achievements": ["Developpe les microservices d'authentification et de paiement d'une marketplace",
                          "Migre une base monolithique MySQL vers PostgreSQL sans interruption de service"],
         "technologies": ["Python", "Django", "MySQL", "PostgreSQL", "RabbitMQ"]},
        {"company": "Startup Nova", "position": "Stagiaire Developpeur", "period": "Fevrier 2020 - Juillet 2020",
         "achievements": ["Implemente un tableau de bord analytique interne"],
         "technologies": ["Python", "Flask", "MongoDB"]},
    ],
    "projects": [
        {"title": "Moteur de recommandation open-source",
         "description": "Bibliotheque Python de filtrage collaboratif, 400+ etoiles GitHub",
         "technologies": ["Python", "NumPy", "scikit-learn"]},
    ],
    "skills": [
        {"category": "Langages", "skills": ["Python", "SQL", "JavaScript", "Go"]},
        {"category": "Frameworks", "skills": ["FastAPI", "Django", "Flask"]},
        {"category": "Bases de donnees", "skills": ["PostgreSQL", "MySQL", "MongoDB", "Redis"]},
        {"category": "DevOps", "skills": ["Docker", "AWS", "GitHub Actions", "Terraform"]},
    ],
    "languages": [
        {"language": "Arabe", "level": "Langue maternelle"},
        {"language": "Francais", "level": "Courant"},
        {"language": "Anglais", "level": "Courant"},
    ],
    "certifications": [
        {"name": "AWS Certified Solutions Architect - Associate", "issuer": "Amazon", "year": "2023"},
    ],
})

# --- section texts from the production splitter -----------------------------
_split = SectionSplitter().split_detailed(SAMPLE_CV_TEXT).sections
EXPERIENCE_TEXT = _split.get("experience", "")
EDUCATION_TEXT = _split.get("education", "")
PROJECTS_TEXT = _split.get("projects", "")
SKILLS_TEXT = _split.get("skills", "")

# --- structured-output schemas (used to test json_schema support) ----------
_EXP_SCHEMA = {
    "type": "object",
    "properties": {"experience": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "company": {"type": ["string", "null"]},
            "position": {"type": ["string", "null"]},
            "period": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "achievements": {"type": "array", "items": {"type": "string"}},
            "technologies": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["company", "position", "period", "location", "description",
                     "achievements", "technologies"],
        "additionalProperties": False,
    }}},
    "required": ["experience"],
    "additionalProperties": False,
}
_EDU_SCHEMA = {
    "type": "object",
    "properties": {"education": {"type": "array", "items": {
        "type": "object",
        "properties": {k: {"type": ["string", "null"]} for k in
                       ("institution", "degree", "field", "start_date", "end_date", "gpa", "location")},
        "required": ["institution", "degree", "field", "start_date", "end_date", "gpa", "location"],
        "additionalProperties": False,
    }}},
    "required": ["education"],
    "additionalProperties": False,
}
_SKILLS_SCHEMA = {
    "type": "object",
    "properties": {"skills": {"type": "array", "items": {"type": "string"}}},
    "required": ["skills"],
    "additionalProperties": False,
}
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "professional_summary": {"type": "string"},
        "career_objective": {"type": "string"},
        "key_skills": {"type": "array", "items": {"type": "string"}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "expertise_areas": {"type": "array", "items": {"type": "string"}},
        "years_of_experience": {"type": "integer"},
        "seniority_level": {"type": "string"},
    },
    "required": ["professional_summary", "career_objective", "key_skills", "strengths",
                 "expertise_areas", "years_of_experience", "seniority_level"],
    "additionalProperties": False,
}
_KEYWORDS_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "array", "items": {"type": "string"}} for k in
                   ("technical_skills", "business_skills", "tools", "frameworks",
                    "certifications", "degrees", "soft_skills")},
    "required": ["technical_skills", "business_skills", "tools", "frameworks",
                 "certifications", "degrees", "soft_skills"],
    "additionalProperties": False,
}


def _analysis_prompt() -> str:
    pa = ProfileAnalyzer()
    return pa._build_analysis_prompt(pa._serialize_profile(CV_PROFILE))


def _ats_keywords_prompt() -> str:
    return ATSOptimizer._build_keyword_extraction_prompt(JOB_DESCRIPTION)


def _ats_optim_prompt() -> str:
    # a realistic analysis + ats_analysis, computed locally (no LLM), just to
    # feed the prompt builder the shape it expects.
    analysis = {
        "professional_summary": CV_PROFILE.professional_summary,
        "career_objective": "",
        "key_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "AWS"],
        "expertise_areas": ["Backend API development", "Distributed data platforms"],
        "seniority_level": "Senior",
    }
    kw = {"technical_skills": ["Python", "Kubernetes", "PostgreSQL", "AWS", "Terraform"],
          "frameworks": ["FastAPI"], "tools": ["Docker", "Redis"], "business_skills": [],
          "certifications": [], "degrees": [], "soft_skills": ["mentorat"]}
    ats = ATSOptimizer().calculate_match_score(CV_PROFILE, kw, analysis)
    return ATSOptimizer._build_optimization_prompt(CV_PROFILE, analysis, ats, JOB_DESCRIPTION, language="fr")


def _letter_prompt() -> str:
    company = {"company_name": "Atlas Recrute", "position": "Senior Backend Engineer",
               "recipient": "", "location": "Casablanca", "company_address": ""}
    return LetterGenerator._build_prompt(CV_PROFILE.model_dump(), JOB_DESCRIPTION, company, "fr")


def _chat_prompt() -> str:
    from services.conversations.conversation_service import ConversationService
    history = [
        {"role": "user", "content": "Voici mon profil : ingenieur backend, 4 ans, Python/FastAPI/PostgreSQL/AWS. "
         "Je vise un poste Senior Backend a Casablanca."},
        {"role": "assistant", "content": "Bien recu. Ton profil est solide pour un poste Senior Backend."},
        {"role": "user", "content": "Mon resume actuel : 'Ingenieur logiciel backend avec 4 ans d'experience.' "
         "Reecris-le pour qu'il soit plus percutant et oriente ATS, sans rien inventer."},
    ]
    return ConversationService._build_prompt(history)


def _preference_prompt() -> str:
    ex = JobPreferenceExtractor()
    turns = [("user", "Je cherche un poste de data engineer a Paris ou en remote, "
                       "salaire minimum 55k, pas de stage, senior de preference.")]
    from schemas.jobs import JobSearchContext
    return ex._build_prompt(turns, JobSearchContext())


def build_tasks() -> list[dict]:
    return [
        {"id": "extract_experience", "kind": "structured",
         "prompt": ExperienceParser._build_prompt(EXPERIENCE_TEXT, "fr"),
         "json_schema": _EXP_SCHEMA, "max_tokens": 3000, "scorer": "extract_experience"},
        {"id": "extract_education", "kind": "structured",
         "prompt": EducationParser._build_prompt(EDUCATION_TEXT, "fr"),
         "json_schema": _EDU_SCHEMA, "max_tokens": 2000, "scorer": "extract_education"},
        {"id": "extract_projects", "kind": "structured",
         "prompt": ProjectParser._build_prompt(PROJECTS_TEXT, "fr"),
         "json_schema": None, "max_tokens": 2000, "scorer": "extract_projects"},
        {"id": "extract_skills", "kind": "structured",
         "prompt": SkillsParser._build_prompt(SKILLS_TEXT, "fr"),
         "json_schema": _SKILLS_SCHEMA, "max_tokens": 1500, "scorer": "extract_skills"},
        {"id": "profile_analysis", "kind": "structured",
         "prompt": _analysis_prompt(),
         "json_schema": _ANALYSIS_SCHEMA, "max_tokens": 1500, "scorer": "profile_analysis"},
        {"id": "ats_keyword_extraction", "kind": "structured",
         "prompt": _ats_keywords_prompt(),
         "json_schema": _KEYWORDS_SCHEMA, "max_tokens": 1200, "scorer": "ats_keyword_extraction"},
        {"id": "ats_content_optimization", "kind": "writing",
         "prompt": _ats_optim_prompt(),
         "json_schema": None, "max_tokens": 3000, "scorer": "ats_content_optimization"},
        {"id": "cover_letter", "kind": "writing",
         "prompt": _letter_prompt(),
         "json_schema": None, "max_tokens": 2000, "scorer": "cover_letter"},
        {"id": "conversation_agent", "kind": "chat",
         "prompt": _chat_prompt(),
         "json_schema": None, "max_tokens": 1200, "scorer": "conversation_agent"},
        {"id": "job_preference_extraction", "kind": "structured",
         "prompt": _preference_prompt(),
         "json_schema": None, "max_tokens": 1200, "scorer": "job_preference_extraction"},
    ]


if __name__ == "__main__":
    for t in build_tasks():
        print(f"{t['id']:26} kind={t['kind']:11} prompt_chars={len(t['prompt']):5}  "
              f"json_schema={'yes' if t['json_schema'] else 'no'}")
    print("\n--- section texts extracted by production SectionSplitter ---")
    for n, txt in (("experience", EXPERIENCE_TEXT), ("education", EDUCATION_TEXT),
                   ("projects", PROJECTS_TEXT), ("skills", SKILLS_TEXT)):
        print(f"  {n:11}: {len(txt)} chars")
