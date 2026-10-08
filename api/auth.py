"""
API router: authentication — ``/api/auth/*``.

Public:  POST /signup, POST /signin, POST /forgot-password, POST /reset-password
Protected: GET /me

The password reset is a two-step, verification-code flow:

    POST /forgot-password {email}                -> always the same 200
                                                   (a 6-digit code is e-mailed
                                                    when the account exists)
    POST /reset-password  {email, code, new_password}
                                                -> 200 on success,
                                                   generic 400 on ANY failure
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.auth import (
    AuthResponse,
    DeactivateAccountRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    MessageResponse,
    ReactivateRequest,
    RequestReactivationRequest,
    RequestReactivationResponse,
    ResetPasswordRequest,
    SigninRequest,
    SignupRequest,
    UserPublic,
)
from services.auth_service import (
    AccountDisabledError,
    AuthError,
    AuthService,
    ReactivationError,
    ResetError,
)
from services.email_service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_email_service = EmailService()

_FORGOT_MESSAGE = (
    "If an account exists for that e-mail, a verification code has been sent."
)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "message": message})


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    service = AuthService(db)
    try:
        user = service.signup(payload.email, payload.password)
    except AuthError as exc:
        return _error(status.HTTP_409_CONFLICT, str(exc))
    token = service.create_access_token(user)
    return AuthResponse(access_token=token, user=UserPublic(id=user.id, email=user.email, is_superadmin=user.is_superadmin))


@router.post("/signin", response_model=AuthResponse)
def signin(payload: SigninRequest, db: Session = Depends(get_db)):
    service = AuthService(db)
    try:
        user = service.authenticate(payload.email, payload.password)
    except AccountDisabledError as exc:
        # Correct credentials on a deactivated account — 403 + a machine code so
        # the frontend can route to the reactivation flow.
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"status": "error", "code": "account_disabled", "message": str(exc)},
        )
    except AuthError as exc:
        return _error(status.HTTP_401_UNAUTHORIZED, str(exc))
    token = service.create_access_token(user)
    try:
        from services.usage_event_service import record
        record(db, user.id, "login", None)
    except Exception:  # noqa: BLE001
        pass
    return AuthResponse(access_token=token, user=UserPublic(id=user.id, email=user.email, is_superadmin=user.is_superadmin))


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    service = AuthService(db)
    code = service.request_reset_code(payload.email)
    if code is not None:
        _email_service.send_reset_code(payload.email, code)
    return ForgotPasswordResponse(
        message=_FORGOT_MESSAGE,
        reset_code=code if settings.PASSWORD_RESET_DEV_MODE else None,
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    service = AuthService(db)
    try:
        service.reset_password_with_code(payload.email, payload.code, payload.new_password)
    except ResetError as exc:
        return _error(status.HTTP_400_BAD_REQUEST, str(exc))
    return MessageResponse(message="Password updated successfully.")


@router.post("/deactivate", response_model=MessageResponse)
def deactivate_account(
    payload: DeactivateAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service account deactivation. Requires the current password (the
    bearer token alone is not enough). On success the account is disabled and
    EVERY existing session/token is invalidated immediately — the caller's
    included."""
    service = AuthService(db)
    try:
        service.authenticate(current_user.email, payload.password)
    except AuthError:
        return _error(status.HTTP_401_UNAUTHORIZED, "Invalid password.")
    service.deactivate(current_user)
    try:
        from services.usage_event_service import record
        record(db, current_user.id, "account_deactivated", None)
    except Exception:  # noqa: BLE001
        pass
    return MessageResponse(
        message="Account deactivated. All sessions have been signed out."
    )


@router.post("/request-reactivation", response_model=RequestReactivationResponse)
def request_reactivation(payload: RequestReactivationRequest, db: Session = Depends(get_db)):
    """Ask for a one-time reactivation key. Always the same 200 — the key is
    e-mailed only when a matching deactivated account exists (no enumeration of
    which addresses exist or are disabled)."""
    service = AuthService(db)
    key = service.request_reactivation_code(payload.email)
    if key is not None:
        _email_service.send_reactivation_code(payload.email, key)
    return RequestReactivationResponse(
        message=(
            "If a deactivated account exists for that e-mail, a reactivation key "
            "has been sent."
        ),
        reactivation_key=key if settings.REACTIVATION_DEV_MODE else None,
    )


@router.post("/reactivate", response_model=AuthResponse)
def reactivate(payload: ReactivateRequest, db: Session = Depends(get_db)):
    """Consume a reactivation key, re-enable the account and return a fresh
    session. Generic 400 on ANY failure (bad/expired/used key, unknown e-mail)."""
    service = AuthService(db)
    try:
        user = service.reactivate_with_code(payload.email, payload.key)
    except ReactivationError as exc:
        return _error(status.HTTP_400_BAD_REQUEST, str(exc))
    token = service.create_access_token(user)
    try:
        from services.usage_event_service import record
        record(db, user.id, "account_reactivated", None)
    except Exception:  # noqa: BLE001
        pass
    return AuthResponse(
        access_token=token,
        user=UserPublic(id=user.id, email=user.email, is_superadmin=user.is_superadmin),
    )


@router.get("/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)):
    return UserPublic(id=current_user.id, email=current_user.email, is_superadmin=current_user.is_superadmin)
