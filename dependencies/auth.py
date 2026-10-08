"""
``get_current_user`` — the dependency every protected route depends on.

Returns the authenticated ``User`` or raises 401. The bearer scheme is
non-auto-error so a missing ``Authorization`` header produces our uniform
``{"status": "error", "message": ...}`` body instead of FastAPI's default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from models import User
from services.auth_service import AuthError, AuthService

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)

# Only rewrite ``last_active_at`` when it is stale by more than this — keeps the
# activity signal useful for DAU/WAU/MAU without a DB write on every request.
_ACTIVITY_TTL = timedelta(minutes=10)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED

    service = AuthService(db)
    try:
        payload = service.decode_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    user = service.get_user(user_id) if user_id else None
    if user is None:
        raise _UNAUTHORIZED
    if not user.is_active:
        # Account deactivated after this token was issued — distinct signal so
        # the frontend clears its session and offers reactivation instead of
        # looping on a generic 401.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account_disabled",
        )
    if int(payload.get("tv", 0) or 0) != int(getattr(user, "token_version", 1) or 1):
        # Token from a superseded session generation (deactivate / reactivate /
        # password reset all bump token_version) — reject even though the
        # signature is still cryptographically valid.
        raise _UNAUTHORIZED

    _touch_activity(db, user)
    return user


def _touch_activity(db: Session, user: User) -> None:
    """Throttled ``last_active_at`` update (best-effort — never blocks a request)."""
    now = datetime.now(timezone.utc)
    last = user.last_active_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is not None and now - last < _ACTIVITY_TTL:
        return
    try:
        user.last_active_at = now
        db.commit()
    except Exception:  # noqa: BLE001 — an activity ping must never 500 a request
        db.rollback()


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """Gate for ``/api/admin/*``. 403 for a normal (or merely authenticated)
    user. ``is_superadmin`` is never accepted from a request body — it is set
    only by the ``SUPERADMIN_EMAILS`` bootstrap or another superadmin's PATCH."""
    if not getattr(user, "is_superadmin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin privileges required.",
        )
    return user
