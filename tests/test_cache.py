"""
The application cache: the MemoryCache primitive, CacheService key/digest
helpers, and the two pipeline integrations (extraction + generation) that
must skip LLM work on an identical repeat request.
"""

from __future__ import annotations

import io

import pytest

from services.cache_service import CacheService, MemoryCache, cache


# ---------------------------------------------------------------------------
# MemoryCache primitive
# ---------------------------------------------------------------------------

def test_memory_cache_get_set_delete_clear():
    c = MemoryCache()
    assert c.get("k") is None
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}
    c.delete("k")
    assert c.get("k") is None
    c.set("a", 1)
    c.clear()
    assert c.get("a") is None


def test_memory_cache_ttl_expiry(monkeypatch):
    import services.cache_service as mod

    now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: now[0])

    c = MemoryCache()
    c.set("k", "v", ttl=10)
    assert c.get("k") == "v"
    now[0] += 11
    assert c.get("k") is None


def test_cache_key_embeds_version_and_digest_is_stable():
    assert CacheService.key("extract", "abc").startswith("v")
    assert ":extract:abc" in CacheService.key("extract", "abc")
    assert CacheService.digest({"a": 1, "b": 2}) == CacheService.digest({"b": 2, "a": 1})
    assert CacheService.digest({"a": 1}) != CacheService.digest({"a": 2})


# ---------------------------------------------------------------------------
# v2.8 — CacheService is best-effort: a broken backend never raises
# ---------------------------------------------------------------------------

class _BrokenBackend:
    def get(self, key):
        raise ConnectionError("backend down")
    def set(self, key, value, ttl=None):
        raise ConnectionError("backend down")
    def delete(self, key):
        raise ConnectionError("backend down")
    def clear(self):
        raise ConnectionError("backend down")


def test_get_set_delete_clear_never_raise_when_backend_is_down(monkeypatch):
    monkeypatch.setattr(cache, "_backend", _BrokenBackend())
    assert cache.get("k") is None          # treated as a miss, not an error
    cache.set("k", "v")                    # swallowed
    cache.delete("k")                      # swallowed
    cache.clear()                          # swallowed


def test_cache_hit_then_backend_dies_mid_session_degrades_gracefully(monkeypatch):
    cache.set("still-there", "v1")
    assert cache.get("still-there") == "v1"   # real HIT while the backend is healthy

    monkeypatch.setattr(cache, "_backend", _BrokenBackend())
    assert cache.get("still-there") is None   # now a clean miss, not a crash


# ---------------------------------------------------------------------------
# Extraction cache
# ---------------------------------------------------------------------------

def _upload(text: str = "JOHN DOE\njohn@x.com\n\nSKILLS\nPython, SQL\n") -> dict:
    return {"file": ("resume.txt", io.BytesIO(text.encode()), "text/plain")}


def test_extraction_second_identical_request_makes_no_llm_call(client, mock_llm):
    r1 = client.post("/api/extract-cv", files=_upload())
    assert r1.status_code == 200
    assert mock_llm.calls  # first time hit the LLM

    mock_llm.calls.clear()
    r2 = client.post("/api/extract-cv", files=_upload())
    assert r2.status_code == 200
    assert mock_llm.calls == []  # served entirely from the app cache
    assert r2.json()["cv_profile"] == r1.json()["cv_profile"]


def test_extraction_different_language_is_a_different_cache_key(client, mock_llm):
    client.post("/api/extract-cv", files=_upload(), data={"language": "en"})
    mock_llm.calls.clear()
    client.post("/api/extract-cv", files=_upload(), data={"language": "fr"})
    assert mock_llm.calls != []  # a new language -> a fresh extraction


# ---------------------------------------------------------------------------
# Generation cache
# ---------------------------------------------------------------------------

_SKIPPABLE = {"profile_analysis", "ats_keyword_extraction", "ats_content_optimization"}


def test_generation_second_identical_request_skips_llm_steps(client, mock_llm, cv_profile_dict):
    assert client.post("/api/generate-cv", json=cv_profile_dict).status_code == 200
    assert _SKIPPABLE & set(mock_llm.calls)

    mock_llm.calls.clear()
    assert client.post("/api/generate-cv", json=cv_profile_dict).status_code == 200
    assert not (_SKIPPABLE & set(mock_llm.calls))


def test_cache_clear_forces_a_recompute(client, mock_llm, cv_profile_dict):
    client.post("/api/generate-cv", json=cv_profile_dict)
    cache.clear()
    mock_llm.calls.clear()
    client.post("/api/generate-cv", json=cv_profile_dict)
    assert _SKIPPABLE & set(mock_llm.calls)
