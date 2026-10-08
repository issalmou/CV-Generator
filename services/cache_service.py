"""
Service: CacheService — the application-level cache.

This sits ABOVE ``services/gemini_client.py``'s own prompt-SHA response
cache (which is left completely untouched). Where the Gemini cache keys on
the exact prompt string — and so misses the moment a prompt template is
edited — this cache keys on the *semantic inputs* (a hash of the profile /
job description / language) at coarse pipeline boundaries, so an identical
request skips prompt building, ATS scoring and PDF assembly entirely.

Backends:
- ``MemoryCache``  — process-local dict, thread-safe, TTL with lazy eviction
- ``RedisCache``   — used automatically when ``REDIS_URL`` is set

Nothing else in the codebase imports ``redis`` directly — only
``from services.cache_service import cache``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class MemoryCache:
    """Thread-safe in-process cache with per-key TTL (lazy eviction)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if expiry and expiry < time.time():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expiry = time.time() + ttl if ttl else 0.0
        with self._lock:
            self._store[key] = (value, expiry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class RedisCache:
    """Redis-backed cache. Values are JSON-encoded; TTL is native (setex)."""

    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Any | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, default=str)
        if ttl:
            self._client.setex(key, ttl, payload)
        else:
            self._client.set(key, payload)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def clear(self) -> None:
        self._client.flushdb()


class CacheService:
    """Facade picking a backend and namespacing keys with ``CACHE_VERSION``.

    Every public method is **best-effort**: a backend failure (Redis down,
    connection reset, timeout — anything after the initial connection, which
    ``__init__`` already falls back from) is logged and swallowed, never
    raised. Every caller in the codebase — the dashboards, the conversation
    history cache, the generation pipeline cache — can therefore always treat
    a cache as an optimisation, with SQL/the real computation as the always-
    correct fallback."""

    def __init__(self) -> None:
        if settings.REDIS_URL:
            try:
                self._backend: Any = RedisCache(settings.REDIS_URL)
                logger.info("[CacheService] Using Redis backend.")
            except Exception as exc:  # noqa: BLE001 - fall back to memory
                logger.warning("[CacheService] Redis unavailable (%s) — using memory.", exc)
                self._backend = MemoryCache()
        else:
            self._backend = MemoryCache()

    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        try:
            return self._backend.get(key)
        except Exception as exc:  # noqa: BLE001 — cache is never a hard dependency
            logger.warning("[CacheService] get(%s) failed (%s) — treated as a miss.", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            self._backend.set(key, value, ttl if ttl is not None else settings.CACHE_TTL)
        except Exception as exc:  # noqa: BLE001 — a failed write must not break the caller
            logger.warning("[CacheService] set(%s) failed (%s) — skipped.", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._backend.delete(key)
        except Exception as exc:  # noqa: BLE001 — invalidation must never raise
            logger.warning("[CacheService] delete(%s) failed (%s) — skipped.", key, exc)

    def clear(self) -> None:
        try:
            self._backend.clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CacheService] clear() failed (%s) — skipped.", exc)

    # ------------------------------------------------------------------

    @staticmethod
    def key(*parts: str) -> str:
        """Build a namespaced key: ``v{CACHE_VERSION}:part:part:...``."""
        return f"v{settings.CACHE_VERSION}:" + ":".join(str(p) for p in parts)

    @staticmethod
    def digest(obj: Any) -> str:
        """Stable SHA-256 of any JSON-serialisable object (sorted keys)."""
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


cache = CacheService()
