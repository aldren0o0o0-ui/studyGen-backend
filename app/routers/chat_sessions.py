"""Thin FastAPI routes for Phase 6 document chat."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.schemas.chat import (
    ChatCancelResponse,
    ChatDeleteResponse,
    ChatGenerationResponse,
    ChatMessagePage,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionListResponse,
    ChatSessionUpdate,
    ChatUsageSummary,
    UserMessageRequest,
)
from app.services import chat_service
from app.dependencies import get_current_user

router = APIRouter(prefix="/chat-sessions", tags=["chat"], dependencies=[Depends(get_current_user)])


def _run_db(operation):
    db = SessionLocal()
    try:
        return operation(db)
    except chat_service.ChatOperationError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.detail)
    finally:
        db.close()


@router.post("", response_model=ChatSessionDetail, status_code=201)
async def create_chat_session(payload: ChatSessionCreate, request: Request, current_user = Depends(get_current_user)):
    settings = request.app.state.settings

    def operation(db):
        from app.schemas.ai_scope import AIScopeRequest, AIScopeType
        from app.services import ai_scope_service

        scope_type_str = payload.scope_type or (
            "folder" if payload.folder_id else ("selected_documents" if payload.document_ids and len(payload.document_ids) > 1 else "document")
        )
        try:
            scope_type_enum = AIScopeType(scope_type_str)
        except ValueError:
            scope_type_enum = AIScopeType.DOCUMENT

        scope_req = AIScopeRequest(
            scope_type=scope_type_enum,
            document_id=payload.document_ids[0] if (payload.document_ids and len(payload.document_ids) == 1 and scope_type_enum == AIScopeType.DOCUMENT) else None,
            document_ids=payload.document_ids,
            folder_id=payload.folder_id,
            include_subfolders=payload.include_subfolders,
        )
        try:
            resolved = ai_scope_service.resolve_ai_scope(db, current_user.id, scope_req)
        except ai_scope_service.AIScopeError as exc:
            raise chat_service.ChatOperationError(exc.status_code, exc.detail)

        return chat_service.create_session(
            db,
            document_ids=resolved.document_ids,
            title=payload.title or (f"Folder: {resolved.folder_name}" if resolved.folder_name else None),
            system_mode=payload.system_mode,
            max_documents=settings.chat_max_documents,
            scope_type=resolved.scope_type,
            folder_id=resolved.folder_id,
            include_subfolders=resolved.include_subfolders,
        )

    return await run_in_threadpool(_run_db, operation)


@router.get("", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=255),
    status: str | None = Query(None, pattern="^(ACTIVE|ARCHIVED)$"),
    document_id: str | None = None,
    order: str = Query("recent", pattern="^(recent|newest)$"),
):
    def operation(db):
        items, total = chat_service.list_sessions(
            db, page=page, page_size=page_size, search=search, status=status,
            document_id=document_id, order=order,
        )
        return ChatSessionListResponse(
            items=items, page=page, page_size=page_size, total=total
        )
    return await run_in_threadpool(_run_db, operation)


@router.get("/usage", response_model=ChatUsageSummary)
async def get_chat_usage():
    return await run_in_threadpool(
        _run_db, lambda db: ChatUsageSummary(**chat_service.usage_summary(db))
    )


@router.get("/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(session_id: str):
    return await run_in_threadpool(
        _run_db,
        lambda db: chat_service.serialize_session(
            db, chat_service.get_session(db, session_id), detail=True
        ),
    )


@router.patch("/{session_id}", response_model=ChatSessionDetail)
async def update_chat_session(session_id: str, payload: ChatSessionUpdate):
    return await run_in_threadpool(
        _run_db,
        lambda db: chat_service.update_session(
            db, session_id, title=payload.title, status=payload.status,
            system_mode=payload.system_mode,
        ),
    )


@router.delete("/{session_id}", response_model=ChatDeleteResponse)
async def delete_chat_session(session_id: str):
    def operation(db):
        chat_service.delete_session(db, session_id)
        return ChatDeleteResponse(session_id=session_id, status="DELETED")
    return await run_in_threadpool(_run_db, operation)


@router.get("/{session_id}/messages", response_model=ChatMessagePage)
async def list_chat_messages(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    def operation(db):
        items, total = chat_service.list_messages(
            db, session_id, page=page, page_size=page_size
        )
        return ChatMessagePage(items=items, page=page, page_size=page_size, total=total)
    return await run_in_threadpool(_run_db, operation)


async def _generation_call(request: Request, operation):
    settings = request.app.state.settings
    timeout = settings.chat_retrieval_timeout_seconds + settings.chat_generation_timeout_seconds
    try:
        return await asyncio.wait_for(
            run_in_threadpool(_run_db, operation), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Chat generation timed out.")


@router.post("/{session_id}/messages", response_model=ChatGenerationResponse)
async def send_chat_message(
    session_id: str, payload: UserMessageRequest, request: Request
):
    def operation(db):
        user, assistant = chat_service.send_message(
            db,
            session_id=session_id,
            text=payload.message,
            mode=payload.mode,
            collection=request.app.state.collection,
            model=request.app.state.gemini_model,
            settings=request.app.state.settings,
            model_available=request.app.state.gemini_available,
            unavailable_reason=request.app.state.gemini_unavailable_reason,
        )
        return ChatGenerationResponse(user_message=user, assistant_message=assistant)
    return await _generation_call(request, operation)


@router.post("/{session_id}/regenerate", response_model=ChatGenerationResponse)
async def regenerate_chat_message(session_id: str, request: Request):
    def operation(db):
        user, assistant = chat_service.regenerate(
            db,
            session_id=session_id,
            collection=request.app.state.collection,
            model=request.app.state.gemini_model,
            settings=request.app.state.settings,
            model_available=request.app.state.gemini_available,
            unavailable_reason=request.app.state.gemini_unavailable_reason,
        )
        return ChatGenerationResponse(user_message=user, assistant_message=assistant)
    return await _generation_call(request, operation)


@router.post("/{session_id}/cancel", response_model=ChatCancelResponse)
async def cancel_chat_message(session_id: str):
    def operation(db):
        cancelled = chat_service.cancel_pending(db, session_id)
        return ChatCancelResponse(
            session_id=session_id,
            cancelled_message_id=cancelled,
            status="cancelled" if cancelled else "no_pending_message",
        )
    return await run_in_threadpool(_run_db, operation)
