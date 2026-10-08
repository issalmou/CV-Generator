"""
Service: MinioService

Thin wrapper over the MinIO SDK. Every generated PDF (CV or cover letter)
goes here — nothing is ever written to the local filesystem.

Object key convention:
    cv/{cv_id}_{language}.pdf
    letter/{letter_id}_{language}.pdf

When ``MINIO_ENDPOINT`` is unset the service is "not configured": routes
that need storage answer 503 via a guard rather than crashing.
"""

from __future__ import annotations

import io
import logging
from datetime import timedelta

from config import settings

logger = logging.getLogger(__name__)


class MinioService:
    def __init__(self) -> None:
        self._client = None
        # separate client used ONLY to sign download URLs, bound to the host a
        # browser can reach (settings.MINIO_PUBLIC_ENDPOINT). Falls back to the
        # main client when unset.
        self._url_client = None
        self.bucket = settings.MINIO_BUCKET
        if settings.MINIO_ENDPOINT:
            from minio import Minio

            _region = settings.MINIO_REGION or None
            self._client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY or None,
                secret_key=settings.MINIO_SECRET_KEY or None,
                secure=settings.MINIO_SECURE,
                region=_region,
            )
            if settings.MINIO_PUBLIC_ENDPOINT:
                self._url_client = Minio(
                    settings.MINIO_PUBLIC_ENDPOINT,
                    access_key=settings.MINIO_ACCESS_KEY or None,
                    secret_key=settings.MINIO_SECRET_KEY or None,
                    secure=settings.MINIO_PUBLIC_SECURE,
                    region=_region,   # no live get_bucket_location lookup
                )

    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return self._client is not None

    def ensure_bucket(self) -> None:
        if self._client is None:
            return
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)
            logger.info("[MinioService] Created bucket %s", self.bucket)

    # ------------------------------------------------------------------

    @staticmethod
    def cv_key(cv_id: str, language: str) -> str:
        return f"cv/{cv_id}_{language}.pdf"

    @staticmethod
    def letter_key(letter_id: str, language: str) -> str:
        return f"letter/{letter_id}_{language}.pdf"

    # ------------------------------------------------------------------

    def upload(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        self._require()
        self._client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.info("[MinioService] Uploaded %s (%d bytes)", key, len(data))
        return key

    def presigned_get_url(self, key: str, expires: int | None = None) -> str:
        self._require()
        seconds = expires or settings.MINIO_URL_EXPIRE_SECONDS
        signer = self._url_client or self._client   # browser-reachable host when configured
        return signer.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=seconds)
        )

    def download(self, key: str) -> bytes:
        self._require()
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        self._require()
        self._client.remove_object(self.bucket, key)
        logger.info("[MinioService] Deleted %s", key)

    # ------------------------------------------------------------------

    def _require(self) -> None:
        if self._client is None:
            raise RuntimeError("MinIO is not configured (MINIO_ENDPOINT missing).")


# Module-level singleton — imported by generation_service and cv_router.
minio_service = MinioService()
