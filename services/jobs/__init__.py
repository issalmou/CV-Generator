"""Job & internship domain — search (sync + async), matching, dedup, freshness,
ranking, applications, and the LLM-assisted context/company parsers.

Provider adapters live in ``services/providers/`` (a self-contained subpackage);
this package orchestrates them. No CV / auth / infra logic here.
"""
