"""
Service: AdminService — superadmin-only operations.

Everything here is reached exclusively through routes guarded by
``dependencies.auth.require_superadmin``. The service itself never trusts a
caller-supplied ``is_superadmin`` — the only ways an account becomes a
superadmin are the ``SUPERADMIN_EMAILS`` bootstrap (startup) and an explicit
PATCH by an existing superadmin (with the self-lockout guards below).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from config import settings
from models import AtsBoard, GeneratedCV, GeneratedLetter, JobApplication, User
from schemas.admin import _SLUG_PROVIDERS, _URL_PROVIDERS, ATS_MANAGED_PROVIDERS
from services.id_utils import require_uuid_or_404
from services.jobs.ats_board_registry import invalidate as _invalidate_board_overlay
from services.providers.metrics import health_note as _health_note

logger = logging.getLogger(__name__)


def _epoch_to_iso(epoch):
    if not epoch:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _validate_board_token(provider: str, token: str) -> str:
    token = token.strip()
    if provider not in ATS_MANAGED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown / non-manageable provider. Choose one of: {', '.join(ATS_MANAGED_PROVIDERS)}.",
        )
    if provider in _SLUG_PROVIDERS:
        from services.providers.ats_common import valid_slug
        if not valid_slug(token):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid board slug.")
    else:  # URL providers — sanity check only; the real SSRF/allow-list guard
           # still runs in http.validate_url at fetch time with the provider's hosts.
        from urllib.parse import urlparse
        parsed = urlparse(token)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Board URL must be an absolute https:// URL.")
        if parsed.hostname.lower() in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Board URL host is not allowed.")
    return token


def bootstrap_superadmins(db: Session) -> int:
    """Promote every existing account whose e-mail is in ``SUPERADMIN_EMAILS``.
    Never demotes. Returns the number of accounts newly promoted."""
    wanted = settings.superadmin_emails_list
    if not wanted:
        return 0
    promoted = 0
    for user in db.scalars(select(User).where(User.email.in_(wanted))):
        if not user.is_superadmin:
            user.is_superadmin = True
            promoted += 1
    if promoted:
        db.commit()
        logger.info("[admin] bootstrap promoted %d account(s) to superadmin", promoted)
    return promoted


class AdminService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------

    def list_users(self, *, limit: int = 50, offset: int = 0,
                   q: str | None = None) -> tuple[list[dict], int]:
        base = select(User)
        if q:
            base = base.where(User.email.ilike(f"%{q.strip()}%"))
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = list(self.db.scalars(
            base.order_by(User.created_at.desc()).limit(limit).offset(offset)
        ))
        if not rows:
            return [], total

        ids = [u.id for u in rows]
        cv_counts = self._count_by_user(GeneratedCV, ids)
        letter_counts = self._count_by_user(GeneratedLetter, ids)
        app_counts = self._count_by_user(JobApplication, ids)
        return [
            {
                "id": u.id,
                "email": u.email,
                "is_active": u.is_active,
                "is_superadmin": u.is_superadmin,
                "created_at": u.created_at,
                "last_active_at": u.last_active_at,
                "cv_count": cv_counts.get(u.id, 0),
                "cover_letter_count": letter_counts.get(u.id, 0),
                "application_count": app_counts.get(u.id, 0),
            }
            for u in rows
        ], total

    def _count_by_user(self, model, user_ids: list[str]) -> dict[str, int]:
        rows = self.db.execute(
            select(model.user_id, func.count())
            .where(model.user_id.in_(user_ids))
            .group_by(model.user_id)
        )
        return {uid: n for uid, n in rows}

    def get_user(self, user_id: str) -> User:
        require_uuid_or_404(user_id, detail="User not found.")
        user = self.db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
        return user

    def user_row(self, user: User) -> dict:
        """The enriched (with counts) row shape for one user."""
        ids = [user.id]
        return {
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
            "is_superadmin": user.is_superadmin,
            "created_at": user.created_at,
            "last_active_at": user.last_active_at,
            "cv_count": self._count_by_user(GeneratedCV, ids).get(user.id, 0),
            "cover_letter_count": self._count_by_user(GeneratedLetter, ids).get(user.id, 0),
            "application_count": self._count_by_user(JobApplication, ids).get(user.id, 0),
        }

    def update_user(
        self,
        actor: User,
        user_id: str,
        *,
        is_active: bool | None = None,
        is_superadmin: bool | None = None,
    ) -> User:
        target = self.get_user(user_id)

        if is_active is not None:
            if target.id == actor.id and is_active is False:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="You cannot deactivate your own account.")
            target.is_active = is_active

        if is_superadmin is not None:
            if target.id == actor.id and is_superadmin is False:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="You cannot revoke your own superadmin.")
            if is_superadmin is False and target.is_superadmin and self._superadmin_count() <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Cannot remove the last superadmin.")
            target.is_superadmin = is_superadmin

        self.db.commit()
        self.db.refresh(target)
        logger.info("[admin] %s updated user %s (active=%s superadmin=%s)",
                    actor.id, target.id, target.is_active, target.is_superadmin)
        return target

    def _superadmin_count(self) -> int:
        return self.db.scalar(
            select(func.count()).select_from(User).where(User.is_superadmin.is_(True))
        ) or 0

    # ------------------------------------------------------------------
    # ATS boards  (Phase 32) — a DB overlay on top of the file/env lists
    # ------------------------------------------------------------------

    def boards_overview(self) -> list[dict]:
        """Per-provider board summary for the dashboard."""
        rows = self.db.execute(
            select(AtsBoard.provider,
                   func.count(),
                   func.sum(cast(AtsBoard.enabled, Integer)),
                   func.sum(case((AtsBoard.last_test_ok.is_(True), 1), else_=0)))
            .group_by(AtsBoard.provider)
        ).all()
        return [
            {"provider": p, "total": int(n or 0), "enabled": int(en or 0),
             "disabled": int((n or 0) - (en or 0)), "last_test_ok": int(ok or 0)}
            for p, n, en, ok in rows
        ]

    def list_boards(self, provider: str | None = None) -> list[AtsBoard]:
        stmt = select(AtsBoard).order_by(AtsBoard.provider, AtsBoard.token)
        if provider:
            stmt = stmt.where(AtsBoard.provider == provider)
        return list(self.db.scalars(stmt))

    def create_board(self, actor: User, *, provider: str, token: str,
                     enabled: bool = True, label: str | None = None,
                     note: str | None = None) -> AtsBoard:
        provider = provider.strip().lower()
        token = _validate_board_token(provider, token)
        if self.db.scalar(select(AtsBoard).where(
            AtsBoard.provider == provider, AtsBoard.token == token
        )):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="That board is already registered.")
        row = AtsBoard(provider=provider, token=token, enabled=enabled,
                       label=label, note=note, created_by=actor.id)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _invalidate_board_overlay()
        logger.info("[admin] %s added ATS board %s/%s", actor.id, provider, token)
        return row

    def get_board(self, board_id: str) -> AtsBoard:
        require_uuid_or_404(board_id, detail="Board not found.")
        row = self.db.get(AtsBoard, board_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found.")
        return row

    def update_board(self, actor: User, board_id: str, *, token: str | None = None,
                     enabled: bool | None = None, label: str | None = None,
                     note: str | None = None) -> AtsBoard:
        row = self.get_board(board_id)
        if token is not None:
            row.token = _validate_board_token(row.provider, token)
        if enabled is not None:
            row.enabled = enabled
        if label is not None:
            row.label = label
        if note is not None:
            row.note = note
        self.db.commit()
        self.db.refresh(row)
        _invalidate_board_overlay()
        return row

    def delete_board(self, actor: User, board_id: str) -> None:
        row = self.get_board(board_id)
        self.db.delete(row)
        self.db.commit()
        _invalidate_board_overlay()
        logger.info("[admin] %s deleted ATS board %s/%s", actor.id, row.provider, row.token)

    # ------------------------------------------------------------------
    # provider statistics  (Phase 33)
    # ------------------------------------------------------------------

    @staticmethod
    def provider_stats() -> list[dict]:
        from services.providers.metrics import duration_stats, error_rate
        from services.providers.registry import registry

        out: list[dict] = []
        for name, provider in registry._providers.items():  # noqa: SLF001
            snap = provider.metrics.snapshot()
            circuit = provider.breaker.state_name()
            runs = int(snap.get("runs", 0) or 0)
            ok = int(snap.get("ok", 0) or 0)
            fail = int(snap.get("fail", 0) or 0)
            ok_nonempty = int(snap.get("ok_nonempty", 0) or 0)
            total_offers = int(snap.get("total_offers", 0) or 0)
            dur = duration_stats(snap)
            out.append({
                "name": name,
                "enabled": provider.enabled,
                "state": (provider.last_state.value if provider.last_state else snap.get("last_state")),
                "circuit_state": circuit,
                "runs": runs,
                "success": ok,
                "failure": fail,
                "success_rate": round(ok / runs, 3) if runs else None,
                "error_rate": error_rate(snap),
                "consecutive_failures": int(snap.get("consecutive_fail", 0) or 0),
                "last_run_at": _epoch_to_iso(snap.get("last_run_at")),
                "last_success_at": _epoch_to_iso(snap.get("last_ok_at")),
                "last_duration_ms": snap.get("last_duration_ms"),
                "avg_duration_ms": dur["avg_duration_ms"],
                "p95_duration_ms": dur["p95_duration_ms"],
                "last_offer_count": snap.get("last_offer_count"),
                "max_offers": int(snap.get("max_offers", 0) or 0),
                "total_offers_collected": total_offers,
                # derived from the counters above — no extra recorded state:
                "offers_per_run": round(total_offers / ok, 2) if ok else None,
                "empty_result_rate": round((ok - ok_nonempty) / ok, 3) if ok else None,
                "last_error": snap.get("last_error"),
                "health_note": _health_note(snap, circuit),
            })
        return out

    def test_board(self, provider: str, token: str) -> dict:
        """Best-effort live check of one board via its provider (no persistence)."""
        provider = provider.strip().lower()
        token = _validate_board_token(provider, token)
        from services.providers.registry import registry
        from schemas.jobs import JobSearchContext

        prov = registry.get(provider)
        if prov is None:
            return {"provider": provider, "token": token, "reachable": False,
                    "offers_found": None, "detail": "provider not enabled in this deployment"}
        try:
            import time as _t
            offers = prov._fetch_board(  # type: ignore[attr-defined]
                token, JobSearchContext(), limit=5,
                deadline=_t.monotonic() + 15.0, keep=lambda o: True,
            )
            return {"provider": provider, "token": token, "reachable": True,
                    "offers_found": len(offers), "detail": None}
        except Exception as exc:  # noqa: BLE001
            from services.providers.metrics import _safe_label
            return {"provider": provider, "token": token, "reachable": False,
                    "offers_found": None, "detail": _safe_label(str(exc)[:200])}

    def test_board_by_id(self, actor: User, board_id: str) -> dict:
        """Run the live test for a stored board and persist the result on the row."""
        from datetime import datetime, timezone

        row = self.get_board(board_id)
        result = self.test_board(row.provider, row.token)
        row.last_tested_at = datetime.now(timezone.utc)
        row.last_test_ok = bool(result.get("reachable"))
        row.last_test_offer_count = result.get("offers_found")
        row.last_test_error = result.get("detail")
        self.db.commit()
        self.db.refresh(row)
        logger.info("[admin] %s tested board %s/%s -> ok=%s",
                    actor.id, row.provider, row.token, row.last_test_ok)
        return {**result, "board_id": row.id}
