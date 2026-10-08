"""
MinIO integration: the in-memory fake used across the suite behaves like
object storage, and a genuinely unconfigured ``MinioService`` makes the
generation routes answer 503.
"""

from __future__ import annotations

import pytest

from services.minio_service import MinioService, minio_service


def test_fake_minio_round_trips(fake_minio):
    key = minio_service.upload("cv/x_en.pdf", b"%PDF-bytes")
    assert key == "cv/x_en.pdf"
    assert minio_service.download(key) == b"%PDF-bytes"
    assert minio_service.presigned_get_url(key).startswith("http://minio.test/")
    minio_service.delete(key)
    assert key not in fake_minio


def test_real_service_without_endpoint_is_not_configured():
    # conftest unsets MINIO_ENDPOINT -> a fresh instance has no client
    assert MinioService().is_configured() is False


def test_presigned_url_is_signed_against_the_public_endpoint(monkeypatch):
    """Phase 3 — inside Docker MINIO_ENDPOINT is 'minio:9000' (not
    browser-reachable). Presigned download URLs must be signed against
    MINIO_PUBLIC_ENDPOINT instead."""
    from config import settings

    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "cdn.example.com")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_SECURE", True)
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "k")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "s")

    svc = MinioService()
    url = svc.presigned_get_url("cv/abc_en.pdf")
    assert url.startswith("https://cdn.example.com/")
    assert "minio:9000" not in url


def test_presigned_url_falls_back_to_main_endpoint_when_no_public(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "localhost:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", "")
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "k")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "s")
    monkeypatch.setattr(settings, "MINIO_SECURE", False)

    url = MinioService().presigned_get_url("cv/abc_en.pdf")
    assert url.startswith("http://localhost:9000/")


def test_generation_route_returns_503_when_storage_unconfigured(client, cv_profile_dict, monkeypatch):
    monkeypatch.setattr(minio_service, "is_configured", lambda: False)
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 503
    assert r.json()["status"] == "error"
