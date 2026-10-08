"""
API router: superadmin administration — ``/api/admin/*``.

**Every route depends on ``require_superadmin``** (which builds on
``get_current_user``): a missing/expired token is 401, an authenticated but
non-superadmin user is 403. There is no route anywhere that lets a user grant
themselves ``is_superadmin`` — that field is not in any create/update schema a
normal user can reach.

Phases 31 → 37 add routes here (users, ATS boards, provider stats, usage stats,
dashboard, LLM config). Phase 31: user administration.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies.auth import require_superadmin
from models import User
from schemas.admin import (
    ATS_MANAGED_PROVIDERS, AdminUserList, AdminUserRow, AdminUserUpdate,
    AtsBoardCreate, AtsBoardList, AtsBoardRow, AtsBoardTestResult, AtsBoardUpdate,
    ProviderStatRow, UsageOverview, UserStatList, UserStatRow,
    ApplicationStats, ApplicationsPerUserList, ApplicationsPerUserRow, DocumentStats,
    JobsActivityStats, LlmConfigView, LlmConfigUpdate, LlmTestResult, DashboardResponse,
    BoardsOverviewRow,
)
from services.admin_service import AdminService
from services.stats_service import StatsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(require_superadmin)])


@router.get("/users", response_model=AdminUserList)
def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
):
    rows, total = AdminService(db).list_users(limit=limit, offset=offset, q=q)
    return AdminUserList(total=total, limit=limit, offset=offset,
                         users=[AdminUserRow(**r) for r in rows])


@router.get("/users/{user_id}", response_model=AdminUserRow)
def get_user(user_id: str, db: Session = Depends(get_db)):
    svc = AdminService(db)
    return AdminUserRow(**svc.user_row(svc.get_user(user_id)))


@router.patch("/users/{user_id}", response_model=AdminUserRow)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    svc = AdminService(db)
    user = svc.update_user(
        actor, user_id,
        is_active=payload.is_active, is_superadmin=payload.is_superadmin,
    )
    return AdminUserRow(**svc.user_row(user))


# ---------------------------------------------------------------------------
# ATS boards (Phase 32) — DB overlay on top of data/ats_boards/*.txt + env
# ---------------------------------------------------------------------------

@router.get("/ats-boards", response_model=AtsBoardList)
def list_ats_boards(
    provider: str | None = Query(None, max_length=32),
    db: Session = Depends(get_db),
):
    rows = AdminService(db).list_boards(provider.strip().lower() if provider else None)
    return AtsBoardList(
        providers=ATS_MANAGED_PROVIDERS,
        boards=[AtsBoardRow.model_validate(r, from_attributes=True) for r in rows],
    )


@router.post("/ats-boards", response_model=AtsBoardRow, status_code=201)
def create_ats_board(
    payload: AtsBoardCreate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    row = AdminService(db).create_board(
        actor, provider=payload.provider, token=payload.token,
        enabled=payload.enabled, label=payload.label, note=payload.note,
    )
    return AtsBoardRow.model_validate(row, from_attributes=True)


@router.patch("/ats-boards/{board_id}", response_model=AtsBoardRow)
def update_ats_board(
    board_id: str,
    payload: AtsBoardUpdate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    row = AdminService(db).update_board(
        actor, board_id, token=payload.token, enabled=payload.enabled,
        label=payload.label, note=payload.note,
    )
    return AtsBoardRow.model_validate(row, from_attributes=True)


@router.delete("/ats-boards/{board_id}", status_code=204)
def delete_ats_board(
    board_id: str,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    from fastapi import Response
    AdminService(db).delete_board(actor, board_id)
    return Response(status_code=204)


@router.post("/ats-boards/test", response_model=AtsBoardTestResult)
def test_ats_board(
    payload: AtsBoardCreate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    return AtsBoardTestResult(**AdminService(db).test_board(payload.provider, payload.token))


@router.post("/ats-boards/{board_id}/test", response_model=AtsBoardTestResult)
def test_ats_board_by_id(
    board_id: str,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    return AtsBoardTestResult(**AdminService(db).test_board_by_id(actor, board_id))


# ---------------------------------------------------------------------------
# Provider statistics (Phase 33)
# ---------------------------------------------------------------------------

@router.get("/providers", response_model=list[ProviderStatRow])
def provider_statistics(db: Session = Depends(get_db)):
    return [ProviderStatRow(**row) for row in AdminService.provider_stats()]


# ---------------------------------------------------------------------------
# Statistics (Phases 34-36) — live SQL aggregates, superadmin only
# ---------------------------------------------------------------------------

@router.get("/stats/usage", response_model=UsageOverview)
def usage_stats(db: Session = Depends(get_db)):
    return UsageOverview(**StatsService(db).usage_overview())


@router.get("/stats/users", response_model=UserStatList)
def per_user_stats(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    rows, total = StatsService(db).per_user(limit=limit, offset=offset)
    return UserStatList(total=total, limit=limit, offset=offset,
                        users=[UserStatRow(**r) for r in rows])


@router.get("/stats/applications", response_model=ApplicationStats)
def application_stats(db: Session = Depends(get_db)):
    return ApplicationStats(**StatsService(db).application_stats())


@router.get("/stats/applications/by-user", response_model=ApplicationsPerUserList)
def applications_per_user(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    rows, total = StatsService(db).applications_per_user(limit=limit, offset=offset)
    return ApplicationsPerUserList(total=total, limit=limit, offset=offset,
                                   users=[ApplicationsPerUserRow(**r) for r in rows])


@router.get("/stats/documents", response_model=DocumentStats)
def document_stats(db: Session = Depends(get_db)):
    return DocumentStats(**StatsService(db).document_stats())


@router.get("/stats/jobs", response_model=JobsActivityStats)
def jobs_activity_stats(db: Session = Depends(get_db)):
    return JobsActivityStats(**StatsService(db).jobs_activity())


# ---------------------------------------------------------------------------
# Profiling (v2.9 Phase 1) — read-only latency + LLM-call measurements.
# Never contains prompt / response / document content — durations, token
# counts, model names and boolean flags only.
# ---------------------------------------------------------------------------

@router.get("/profiling/recent")
def profiling_recent(limit: int = Query(50, ge=1, le=200)):
    from services import profiling
    return profiling.recent(limit)


@router.post("/profiling/reset")
def profiling_reset():
    from services import profiling
    profiling.reset()
    return {"status": "success", "message": "Profiling ring buffer cleared."}


# ---------------------------------------------------------------------------
# Dashboard (Phase 37) + LLM config view (Phases 29/30/37)
# ---------------------------------------------------------------------------

@router.get("/llm", response_model=LlmConfigView)
def llm_config(actor: User = Depends(require_superadmin)):
    import services.gemini_client as gemini_client
    return LlmConfigView(**gemini_client.get_config())


@router.patch("/llm", response_model=LlmConfigView)
def update_llm_config(
    payload: LlmConfigUpdate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from services.runtime_config_service import ConfigError, save_llm_overrides

    try:
        cfg = save_llm_overrides(db, payload.to_settings_patch())
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("[admin] %s changed the LLM configuration", actor.id)
    return LlmConfigView(**cfg)


@router.post("/llm/test", response_model=LlmTestResult)
def test_llm(actor: User = Depends(require_superadmin)):
    import services.gemini_client as gemini_client
    return LlmTestResult(**gemini_client.healthcheck())


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(refresh: bool = Query(False), db: Session = Depends(get_db)):
    import services.gemini_client as gemini_client
    from datetime import datetime, timezone

    from services.cache_service import cache

    ckey = cache.key("admin", "dashboard")
    if not refresh:
        cached = cache.get(ckey)
        if cached is not None:
            return DashboardResponse.model_validate(cached)

    stats = StatsService(db)
    resp = DashboardResponse(
        usage=UsageOverview(**stats.usage_overview()),
        jobs=JobsActivityStats(**stats.jobs_activity()),
        applications=ApplicationStats(**stats.application_stats()),
        documents=DocumentStats(**stats.document_stats()),
        providers=[ProviderStatRow(**r) for r in AdminService.provider_stats()],
        boards=[BoardsOverviewRow(**r) for r in AdminService(db).boards_overview()],
        llm=LlmConfigView(**gemini_client.get_config()),
        generated_at=datetime.now(timezone.utc),
    )
    cache.set(ckey, resp.model_dump(mode="json"), ttl=settings.DASHBOARD_CACHE_TTL)
    return resp
