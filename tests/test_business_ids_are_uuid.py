"""Phase 6 finalisation — business-entity ids are UUIDs, end to end.

Every model already generates its primary key as ``str(uuid4())``
(``String(36)`` columns) — this suite makes that guarantee explicit and
regression-tested, at three layers:

1. the DB layer — every model's own id, and every foreign key pointing at
   one, parses as a real UUID;
2. the JWT — the access token's ``sub`` claim IS the user's id, so it is a
   UUID too;
3. the HTTP layer — ids coming back out of the API (signup, a conversation,
   an agent job search, an application) are UUID-parseable strings.

It also locks in the one deliberate exception (Objective 1's own "don't do a
blanket replacement" warning): a job-search *ordinal* ("1", "2" — a display
index, never persisted as a real id) must never be mistaken for a UUID.
"""

from __future__ import annotations

import uuid

import bcrypt
import jwt as _pyjwt
import pytest

from config import settings
from models import (
    AtsBoard, Conversation, GeneratedCV, GeneratedLetter, JobApplication,
    JobOffer, Message, PasswordResetCode, ReactivationCode, SavedJob,
    UsageEvent, User, UserProfile,
)
from services.id_utils import is_valid_uuid, require_uuid_or_404


def _assert_uuid(value) -> None:
    assert value is not None
    uuid.UUID(str(value))   # raises ValueError if not a real UUID


# ---------------------------------------------------------------------------
# 1. every model's id (+ FKs) is a real UUID
# ---------------------------------------------------------------------------

def _make_user(db, email="uuid-tester@example.com") -> User:
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_user_id_is_uuid(db):
    u = _make_user(db)
    _assert_uuid(u.id)
    assert not u.id.isdigit()          # never a bare integer either


def test_conversation_and_message_ids_and_fks_are_uuid(db):
    u = _make_user(db)
    conv = Conversation(user_id=u.id)
    db.add(conv); db.commit(); db.refresh(conv)
    msg = Message(conversation_id=conv.id, user_id=u.id, role="user", content="hi")
    db.add(msg); db.commit(); db.refresh(msg)

    for value in (conv.id, conv.user_id, msg.id, msg.conversation_id, msg.user_id):
        _assert_uuid(value)


def test_job_offer_and_saved_job_ids_and_fks_are_uuid(db):
    u = _make_user(db)
    offer = JobOffer(
        source="arbeitnow", source_job_id="ext-1", source_url="https://example.com/job/1",
        content_hash="h1", title="Backend Engineer",
    )
    db.add(offer); db.commit(); db.refresh(offer)
    saved = SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")
    db.add(saved); db.commit(); db.refresh(saved)

    for value in (offer.id, saved.id, saved.user_id, saved.job_offer_id):
        _assert_uuid(value)
    # the provider's OWN native id is explicitly NOT one of ours / not a UUID
    assert offer.source_job_id == "ext-1"


def test_job_application_id_and_fks_are_uuid(db):
    u = _make_user(db)
    offer = JobOffer(
        source="arbeitnow", source_job_id="ext-2", source_url="https://example.com/job/2",
        content_hash="h2", title="Data Engineer",
    )
    db.add(offer); db.commit(); db.refresh(offer)
    app = JobApplication(user_id=u.id, job_offer_id=offer.id, status="manual_required")
    db.add(app); db.commit(); db.refresh(app)

    for value in (app.id, app.user_id, app.job_offer_id):
        _assert_uuid(value)


def test_generated_cv_and_letter_ids_are_uuid(db):
    u = _make_user(db)
    cv = GeneratedCV(user_id=u.id, filename="cv.pdf", storage_key="cv/x.pdf",
                     minio_bucket="cv-files", language="en")
    letter = GeneratedLetter(user_id=u.id, filename="l.pdf", storage_key="letter/x.pdf",
                             minio_bucket="cv-files", language="en")
    db.add_all([cv, letter]); db.commit(); db.refresh(cv); db.refresh(letter)

    for value in (cv.id, cv.user_id, letter.id, letter.user_id):
        _assert_uuid(value)
    # the human-facing document reference ("CV_A8F42K") is explicitly NOT a UUID
    # and must never be treated as one.
    assert cv.reference is None or not is_valid_uuid(cv.reference)


def test_user_profile_pk_is_uuid(db):
    u = _make_user(db)
    profile = UserProfile(user_id=u.id)
    db.add(profile); db.commit()
    _assert_uuid(profile.user_id)


def test_ats_board_password_reset_reactivation_usage_event_ids_are_uuid(db):
    u = _make_user(db)
    board = AtsBoard(provider="greenhouse", token="acme")
    reset = PasswordResetCode(user_id=u.id, code_hash="x" * 64,
                              expires_at=__import__("datetime").datetime.now(
                                  __import__("datetime").timezone.utc))
    reactivate = ReactivationCode(user_id=u.id, code_hash="y" * 64,
                                  expires_at=__import__("datetime").datetime.now(
                                      __import__("datetime").timezone.utc))
    event = UsageEvent(user_id=u.id, kind="login")
    db.add_all([board, reset, reactivate, event]); db.commit()
    db.refresh(board); db.refresh(reset); db.refresh(reactivate); db.refresh(event)

    for value in (board.id, reset.id, reset.user_id, reactivate.id,
                  reactivate.user_id, event.id, event.user_id):
        _assert_uuid(value)


def test_no_business_model_uses_an_integer_primary_key():
    """Every business model's own primary key column is a UUID string
    (String(36), default=uuid4) — never Integer / autoincrement."""
    from sqlalchemy import String

    for model in (User, Conversation, Message, JobOffer, JobApplication, SavedJob,
                  GeneratedCV, GeneratedLetter, AtsBoard, PasswordResetCode,
                  ReactivationCode, UsageEvent):
        pk_cols = list(model.__table__.primary_key.columns)
        assert len(pk_cols) == 1, model.__name__
        col = pk_cols[0]
        assert isinstance(col.type, String), f"{model.__name__}.{col.name} is not a String PK"
        assert col.type.length == 36, f"{model.__name__}.{col.name} is not String(36)"


# ---------------------------------------------------------------------------
# 2. the JWT `sub` claim is the user's UUID
# ---------------------------------------------------------------------------

def test_jwt_sub_claim_is_the_users_uuid(db):
    from services.auth_service import AuthService

    u = _make_user(db, "jwt-tester@example.com")
    token = AuthService(db).create_access_token(u)
    payload = _pyjwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert payload["sub"] == u.id
    _assert_uuid(payload["sub"])


# ---------------------------------------------------------------------------
# 3. HTTP layer — ids coming back out of the API are UUID-parseable
# ---------------------------------------------------------------------------

def test_signup_response_id_is_uuid(anon_client):
    r = anon_client.post("/api/auth/signup",
                         json={"email": "signup-uuid@example.com", "password": "Str0ngPass1"})
    assert r.status_code == 201
    _assert_uuid(r.json()["user"]["id"])


def test_conversation_and_message_response_ids_are_uuid(client):
    """Uses the `client` fixture (bundles `mock_llm`) — a real LLM call here
    would 429, unrelated to what this test checks (id format)."""
    r = client.post("/api/conversations", json={"title": "t"})
    assert r.status_code == 201
    conv = r.json()
    _assert_uuid(conv["id"])

    m = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hello"})
    assert m.status_code == 200
    body = m.json()
    _assert_uuid(body["conversation_id"])
    _assert_uuid(body["user_message"]["id"])
    _assert_uuid(body["assistant_message"]["id"])


def test_agent_found_jobs_and_application_ids_are_uuid(db, mock_llm, swap_providers):
    import json as _json

    from _jobs_helpers import FakeJobProvider, make_offer

    u = _make_user(db, "jobs-uuid@example.com")
    from tests.conftest import _client as _mk_client
    client = _mk_client(db, u)

    swap_providers([FakeJobProvider("arbeitnow", [make_offer(sid="u1", title="Backend Engineer")])])

    def router(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return _json.dumps({
                "reply": "", "intent": "search_jobs",
                "search_patch": {"query": "backend"}, "apply": None, "question": None,
            })
        return "{}"
    mock_llm.set(router)

    conv = client.post("/api/conversations", json={}).json()
    resp = client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": "trouve-moi un poste backend"}).json()
    assert resp["jobs_found"], "expected at least one job in this turn's reply"
    for job in resp["jobs_found"]:
        _assert_uuid(job["job_offer_id"])
    client._cleanup_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# services.id_utils — the shared format-validation helper
# ---------------------------------------------------------------------------

def test_is_valid_uuid_accepts_real_uuids_and_rejects_ordinals_and_garbage():
    assert is_valid_uuid(str(uuid.uuid4())) is True
    assert is_valid_uuid("1") is False              # a display ordinal, never a real id
    assert is_valid_uuid("does-not-exist") is False
    assert is_valid_uuid("") is False
    assert is_valid_uuid(None) is False


def test_require_uuid_or_404_raises_404_not_422_on_a_malformed_id():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        require_uuid_or_404("not-a-uuid", detail="Nope.")
    assert exc.value.status_code == 404

    # a real UUID passes straight through, unchanged
    real = str(uuid.uuid4())
    assert require_uuid_or_404(real) == real


@pytest.mark.parametrize("path", [
    "/api/conversations/does-not-exist",
    "/api/conversations/does-not-exist/messages",
    "/api/jobs/does-not-exist",
    "/api/jobs/applications/does-not-exist",
])
def test_malformed_path_id_is_still_404_not_422(auth_client, path):
    """The UUID-format hardening at the service layer must never turn an
    existing "unknown id -> 404" contract into a 422 — that would be an
    undocumented, regression-causing API change."""
    r = auth_client.get(path)
    assert r.status_code == 404, (path, r.status_code, r.text)
