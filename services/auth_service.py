"""
Service: AuthService

Everything the auth router needs, kept out of the HTTP layer:
- signup / authenticate (bcrypt password hashing — plaintext never stored)
- JWT access-token mint / decode (PyJWT, HS256)
- the two-step verification-code password reset:
    * request_reset_code(email)         -> generate + persist a 6-digit code
    * reset_password_with_code(...)      -> verify the code, set the password

Security properties of the reset flow (see the project spec):
- the code is random (``secrets``), 6 digits, SHA-256-hashed at rest
- it expires, is single-use, and is invalidated after a successful reset
- a wrong code increments an attempt counter; past the configured limit the
  code is burned
- every failure raises the SAME ``ResetError`` so the caller returns one
  generic 400 — no oracle for "email exists" / "code close" / "expired"
- ``request_reset_code`` returns ``None`` for an unknown e-mail; the router
  answers an identical 200 either way (no user enumeration)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from models import PasswordResetCode, ReactivationCode, User

logger = logging.getLogger(__name__)


class AuthError(ValueError):
    """Signup / signin failure (mapped to 401 / 409 by the router)."""


class AccountDisabledError(AuthError):
    """Sign-in with a correct password on a DEACTIVATED account — the router
    answers 403 with ``code: "account_disabled"`` so the frontend can show the
    reactivation flow instead of a generic 'wrong password'."""


class ResetError(ValueError):
    """Any password-reset failure — deliberately opaque (generic 400)."""


class ReactivationError(ValueError):
    """Any reactivation failure — deliberately opaque (generic 400)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_email(value: str) -> str:
    """The one place e-mail normalisation is defined — trim + lowercase.
    Applied before every storage and every comparison (signup, signin,
    password reset) so ``Bob@X.com`` and `` bob@x.com `` are the same
    account. Mirrors ``Settings.superadmin_emails_list``."""
    return (value or "").strip().lower()


def _as_aware(dt: datetime) -> datetime:
    """SQLite round-trips ``DateTime`` as naive — treat those as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Signup / signin
    # ------------------------------------------------------------------

    def signup(self, email: str, password: str) -> User:
        email = _normalize_email(email)
        existing = self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise AuthError("An account with this e-mail already exists.")

        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        )
        self.db.add(user)
        try:
            self.db.commit()
        except IntegrityError:
            # The pre-check above is a UX nicety, not the guarantee — under a
            # concurrent signup with the exact same e-mail, both requests can
            # pass the SELECT before either COMMITs. The DB-level UNIQUE
            # constraint on `users.email` is what actually prevents the
            # duplicate; this turns that race into a clean 409, never a 500.
            self.db.rollback()
            logger.info("[AuthService] Signup race on e-mail — already exists.")
            raise AuthError("An account with this e-mail already exists.")
        self.db.refresh(user)
        logger.info("[AuthService] New account created | user_id=%s", user.id)
        return user

    def authenticate(self, email: str, password: str) -> User:
        email = _normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            raise AuthError("Invalid e-mail or password.")
        if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            raise AuthError("Invalid e-mail or password.")
        if not user.is_active:
            # correct credentials, disabled account -> distinct signal so the
            # frontend routes to reactivation (not "wrong password").
            raise AccountDisabledError("This account is deactivated.")
        return user

    def get_user(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------

    def create_access_token(self, user: User) -> str:
        now = _now()
        payload = {
            "sub": user.id,
            "email": user.email,
            # session generation — bumped on deactivate/reactivate so a token
            # from a previous generation is rejected (dependencies.auth).
            "tv": int(getattr(user, "token_version", 1) or 1),
            "iat": now,
            "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def decode_token(token: str) -> dict:
        try:
            return jwt.decode(
                token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("Invalid access token.") from exc

    # ------------------------------------------------------------------
    # Password reset — step 1: request a code
    # ------------------------------------------------------------------

    def request_reset_code(self, email: str) -> str | None:
        """
        Generate + persist a fresh 6-digit code for ``email``.

        Returns the raw code (the caller e-mails it / echoes it only in dev
        mode) or ``None`` when no such account exists. The router answers an
        identical 200 in both cases.
        """
        email = _normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            logger.info("[AuthService] Reset requested for unknown e-mail — no-op.")
            return None

        # Invalidate any earlier unused code for this user.
        stale = self.db.scalars(
            select(PasswordResetCode).where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used.is_(False),
            )
        ).all()
        for row in stale:
            row.used = True

        code = f"{secrets.randbelow(10 ** 6):06d}"
        self.db.add(
            PasswordResetCode(
                user_id=user.id,
                code_hash=_sha256(code),
                expires_at=_now() + timedelta(minutes=settings.PASSWORD_RESET_CODE_EXPIRE_MINUTES),
                used=False,
                attempts=0,
            )
        )
        self.db.commit()
        logger.info("[AuthService] Reset code issued | user_id=%s", user.id)
        return code

    # ------------------------------------------------------------------
    # Password reset — step 2: verify the code, set the new password
    # ------------------------------------------------------------------

    def reset_password_with_code(self, email: str, code: str, new_password: str) -> User:
        email = _normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            raise ResetError("Invalid or expired verification code.")

        row = self.db.scalars(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used.is_(False),
            )
            .order_by(PasswordResetCode.created_at.desc())
        ).first()
        if row is None:
            raise ResetError("Invalid or expired verification code.")

        # Count this attempt and burn the code if it has been hammered.
        row.attempts += 1
        if row.attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
            row.used = True
        self.db.commit()

        if _as_aware(row.expires_at) < _now():
            raise ResetError("Invalid or expired verification code.")
        if row.used and row.attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
            raise ResetError("Invalid or expired verification code.")
        if not secrets.compare_digest(row.code_hash, _sha256(code)):
            raise ResetError("Invalid or expired verification code.")

        user.password_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        # a password reset also ends every existing session (a leaked token
        # must not survive the reset it may have been leaked for).
        user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        row.used = True
        self.db.commit()
        self.db.refresh(user)
        logger.info("[AuthService] Password reset completed | user_id=%s", user.id)
        return user

    # ------------------------------------------------------------------
    # Account deactivation / reactivation
    # ------------------------------------------------------------------

    def deactivate(self, user: User) -> None:
        """Disable the account and END EVERY ACTIVE SESSION: ``is_active`` False
        is checked on every request (dependencies.auth), and bumping
        ``token_version`` makes every already-issued token fail its ``tv``
        check as well. Idempotent."""
        user.is_active = False
        user.deactivated_at = _now()
        user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        self._burn_reactivation_codes(user.id)
        self.db.commit()
        logger.info("[AuthService] Account deactivated | user_id=%s", user.id)

    def _burn_reactivation_codes(self, user_id: str) -> None:
        for row in self.db.scalars(
            select(ReactivationCode).where(
                ReactivationCode.user_id == user_id, ReactivationCode.used.is_(False)
            )
        ).all():
            row.used = True

    def request_reactivation_code(self, email: str) -> str | None:
        """Fresh one-time key for a DEACTIVATED account. Returns the raw key
        (e-mailed / dev-mode echoed) or ``None`` when there is no such account
        OR the account is active — the router answers an identical 200 either
        way (no enumeration of which emails exist / are disabled)."""
        email = _normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None or user.is_active:
            return None
        self._burn_reactivation_codes(user.id)
        code = f"{secrets.randbelow(10 ** 8):08d}"
        self.db.add(ReactivationCode(
            user_id=user.id,
            code_hash=_sha256(code),
            expires_at=_now() + timedelta(minutes=settings.REACTIVATION_CODE_EXPIRE_MINUTES),
            used=False, attempts=0,
        ))
        self.db.commit()
        logger.info("[AuthService] Reactivation key issued | user_id=%s", user.id)
        return code

    def reactivate_with_code(self, email: str, code: str) -> User:
        """Validate the key and reactivate. Bumps ``token_version`` again so the
        returned session supersedes any pre-deactivation token that might still
        be cryptographically valid. Every failure raises the SAME opaque error."""
        email = _normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            raise ReactivationError("Invalid or expired reactivation key.")

        row = self.db.scalars(
            select(ReactivationCode)
            .where(ReactivationCode.user_id == user.id, ReactivationCode.used.is_(False))
            .order_by(ReactivationCode.created_at.desc())
        ).first()
        if row is None:
            raise ReactivationError("Invalid or expired reactivation key.")

        row.attempts += 1
        if row.attempts >= settings.REACTIVATION_MAX_ATTEMPTS:
            row.used = True
        self.db.commit()

        if _as_aware(row.expires_at) < _now() or row.used:
            raise ReactivationError("Invalid or expired reactivation key.")
        if not secrets.compare_digest(row.code_hash, _sha256(code)):
            raise ReactivationError("Invalid or expired reactivation key.")

        user.is_active = True
        user.deactivated_at = None
        user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
        row.used = True
        self.db.commit()
        self.db.refresh(user)
        logger.info("[AuthService] Account reactivated | user_id=%s", user.id)
        return user
