"""
API router: the per-user structured job-search profile — ``/api/profile``.

Every route requires ``Authorization: Bearer`` and is **owner-scoped** — a user
can only ever read or write their own profile (the row's primary key *is* the
user id, so there is no id to tamper with).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.dashboard import UserDashboardResponse
from schemas.dashboard_jobs import SelectedJobList
from schemas.profile import UserProfileIn, UserProfileOut
from services.user_dashboard_service import UserDashboardService
from services.user_profile_service import UserProfileService

router = APIRouter(prefix="/api/profile", tags=["Profile"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@dashboard_router.get("", response_model=UserDashboardResponse)
def my_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The authenticated user's own dashboard — strictly owner-scoped."""
    return UserDashboardService(db, current_user).build()


@dashboard_router.get("/jobs", response_model=SelectedJobList)
def my_selected_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The jobs the agent retained for this user (+ any pinned manually), each
    with its current application status so the frontend can show an **Apply**
    button per job. Strictly owner-scoped."""
    return UserDashboardService(db, current_user).selected_jobs(limit=limit, offset=offset)


@router.get("", response_model=UserProfileOut)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return UserProfileService(db).get_or_empty(current_user)


@router.put("", response_model=UserProfileOut)
def put_profile(
    payload: UserProfileIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return UserProfileService(db).upsert(current_user, payload)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    UserProfileService(db).delete(current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
