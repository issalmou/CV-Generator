"""
Schemas for ``/api/auth/*``.

Password rules live in one validator (``_validate_password``) reused for
both signup and the new password on reset. ``password_hash`` never appears
in any response model — the only user-facing shape is ``UserPublic``.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

_PASSWORD_MIN_LENGTH = 8


def _validate_password(value: str) -> str:
    if len(value) < _PASSWORD_MIN_LENGTH:
        raise ValueError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Za-z]", value):
        raise ValueError("Password must contain at least one letter.")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one digit.")
    return value


class SignupRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return _validate_password(v)


class SigninRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, v: str) -> str:
        return _validate_password(v)


class DeactivateAccountRequest(BaseModel):
    # Re-authenticate before a self-service deactivation: it logs out every
    # session, so a stolen/borrowed token must not be enough to trigger it.
    password: str


class RequestReactivationRequest(BaseModel):
    email: EmailStr


class RequestReactivationResponse(BaseModel):
    status: str = "success"
    message: str
    # Populated ONLY when REACTIVATION_DEV_MODE is on (local dev / tests).
    reactivation_key: str | None = None


class ReactivateRequest(BaseModel):
    email: EmailStr
    key: str = Field(..., min_length=8, max_length=8, pattern=r"^\d{8}$")


class UserPublic(BaseModel):
    id: UUID
    email: EmailStr
    is_superadmin: bool = False   # output only — never read from a request body


class AuthResponse(BaseModel):
    status: str = "success"
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class ForgotPasswordResponse(BaseModel):
    status: str = "success"
    message: str
    # Populated ONLY when PASSWORD_RESET_DEV_MODE is on (local dev / tests).
    reset_code: str | None = None


class MessageResponse(BaseModel):
    status: str = "success"
    message: str
