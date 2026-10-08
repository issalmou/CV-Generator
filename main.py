"""
CV Assistant — FastAPI application entry point.

Responsibilities:
- build the app, wire middleware and the three routers (auth / extraction / CV)
- validate the runtime configuration and initialise the database + object
  storage on startup
- expose the two public monitoring endpoints (/api/health, /api/stats)
- uniform error handling

All business logic lives in ``services/`` (generation, auth, cache, MinIO,
e-mail) and the routers. ``main`` itself carries no pipeline code.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import services.gemini_client as gemini_client
from api.admin import router as admin_router
from api.auth import router as auth_router
from api.conversations import router as conversations_router
from api.cv import router as cv_router
from api.extraction import router as extraction_router
from api.jobs import router as jobs_router
from api.profile import dashboard_router, router as profile_router
from config import settings
from cv_models import HealthResponse
from database import init_db
from services.minio_service import minio_service

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

APP_VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Startup] CV Assistant starting – version %s", APP_VERSION)

    missing = [
        name
        for name in ("DATABASE_URL", "JWT_SECRET_KEY")
        if not getattr(settings, name)
    ]
    if missing:
        raise RuntimeError(
            f"Missing mandatory configuration: {', '.join(missing)}. "
            "Set them in the environment or .env before starting the service."
        )

    init_db()

    try:
        from database import SessionLocal
        from services.admin_service import bootstrap_superadmins
        from services.runtime_config_service import apply_llm_overrides
        from services.usage_event_service import prune as _prune_usage
        with SessionLocal() as _db:
            bootstrap_superadmins(_db)
            apply_llm_overrides(_db)
            _prune_usage(_db)
    except Exception as exc:  # noqa: BLE001 — never block startup on this
        logger.error("[Startup] bootstrap / runtime-config failed: %s", exc)

    if minio_service.is_configured():
        try:
            minio_service.ensure_bucket()
        except Exception as exc:  # noqa: BLE001 - surfaced, not fatal at boot
            logger.error("[Startup] MinIO bucket check failed: %s", exc)
    else:
        logger.warning("[Startup] MinIO not configured — generation routes will return 503.")

    logger.info("[Startup] LLM configured: %s", gemini_client.is_configured())
    yield

    # release the shared headless browser (if a job provider ever started one)
    try:
        from services.providers.browser import browser_session
        browser_session.shutdown()
    except Exception as exc:  # noqa: BLE001 - best-effort teardown
        logger.debug("[Shutdown] browser session teardown: %s", exc)
    logger.info("[Shutdown] CV Assistant stopped.")


app = FastAPI(
    title="CV Assistant – CV Generation",
    description="ATS-optimised CV generation with authentication, MinIO storage and caching.",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(extraction_router)
app.include_router(cv_router)
app.include_router(jobs_router)
app.include_router(profile_router)
app.include_router(dashboard_router)
app.include_router(conversations_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    logger.warning("[ValidationError] %s", exc)
    return JSONResponse(status_code=422, content={"status": "error", "message": str(exc)})


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.exception("[UnhandledError] %s", exc)
    return JSONResponse(
        status_code=500, content={"status": "error", "message": "Internal server error."}
    )


# ---------------------------------------------------------------------------
# Monitoring endpoints (public)
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse, tags=["Monitoring"])
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="cv-generator",
        version=APP_VERSION,
        gemini_configured=gemini_client.is_configured(),
    )

# Phase 6 — the public, unauthenticated `/api/stats` (internal LLM-client
# counters) was removed. The same figures are on `GET /api/admin/dashboard`
# and `GET /api/admin/llm` behind `require_superadmin`.
