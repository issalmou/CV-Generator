"""
API router: persistent conversational memory — ``/api/conversations/*``.

Every route requires ``Authorization: Bearer`` and is **strictly owner-scoped**
— ``ConversationService.get_owned`` re-checks ``conversation.user_id ==
current_user.id`` on every read/write; no route accepts a ``user_id`` from
the client. A conversation belonging to another user is a ``403`` (or a
``404`` for the sub-resources of one that plain does not exist), never a
silent cross-user read.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.conversation import (
    ConversationCreate, ConversationList, ConversationOut, EditedDocument,
    MessageCreate, MessageList, MessageOut, MessageSendResponse, PendingConfirmation,
    RecommendedAction,
)
from schemas.dashboard_jobs import ApplyOutcome, SelectedJobOut
from schemas.recommendation import IntelligentRecommendation
from services.conversations.conversation_service import ConversationService
from services.generation_service import llm_error_response, require_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["Conversations"])


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = ConversationService(db).create(current_user, title=payload.title)
    return ConversationOut(**ConversationService._to_out(conv, 0))


@router.get("", response_model=ConversationList)
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = ConversationService(db).list(current_user, limit=limit, offset=offset)
    return ConversationList(total=total, limit=limit, offset=offset,
                            conversations=[ConversationOut(**r) for r in rows])


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ConversationOut(**ConversationService(db).get_out(current_user, conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ConversationService(db).delete(current_user, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=MessageList)
def list_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = ConversationService(db).list_messages(
        current_user, conversation_id, limit=limit, offset=offset
    )
    return MessageList(
        total=total, limit=limit, offset=offset,
        messages=[MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
                 for m in rows],
    )


@router.post("/{conversation_id}/messages", response_model=MessageSendResponse)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    guard = require_llm()
    if guard is not None:
        return guard

    service = ConversationService(db)
    try:
        res = await service.send_message(current_user, conversation_id, payload.content)
    except RuntimeError as exc:
        mapped = llm_error_response(exc, trace_id=conversation_id)
        if mapped is not None:
            return mapped
        raise

    um, am = res.user_message, res.assistant_message
    return MessageSendResponse(
        conversation_id=conversation_id,
        user_message=MessageOut(id=um.id, role=um.role, content=um.content, created_at=um.created_at),
        assistant_message=MessageOut(id=am.id, role=am.role, content=am.content, created_at=am.created_at),
        document=EditedDocument(**res.document) if res.document else None,
        recommended_actions=[RecommendedAction(**a) for a in res.recommended_actions],
        recommendations=[IntelligentRecommendation(**r) for r in res.recommendations],
        jobs_found=[SelectedJobOut(**j) for j in res.jobs_found],
        applications=[ApplyOutcome(**a) for a in res.applications],
        pending_confirmation=(PendingConfirmation(**res.pending_confirmation)
                              if res.pending_confirmation else None),
    )
