"""Persistence and orchestration for Phase 6 document chat."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database.models.chat import (
    ChatCitation,
    ChatGenerationRecord,
    ChatMessage,
    ChatSession,
    ChatSessionDocument,
)
from app.database.types import normalize_json_value
from app.database.models.document import Document
from app.database.models.study import StudyActivity
from app.schemas.chat import (
    ChatCitationResponse,
    ChatMessageResponse,
    ChatSessionDetail,
    ChatSessionSummary,
    SelectedDocumentSummary,
)
from app.services import study_activity_service
from app.services.chat_generation_service import (
    ChatInvalidOutput,
    ChatProviderUnavailable,
    GeneratedChatAnswer,
    generate_extractive_fallback,
    generate_chat_answer,
)
from app.services.chat_prompts import CHAT_PROMPT_VERSION
from app.services.chat_retrieval import retrieve_chat_chunks
from app.services.conversation_context import (
    build_retrieval_query,
    deterministic_summary,
    recent_history,
)
from app.services.prompt_injection_defense import BOUNDARY_RESPONSE, is_boundary_attack


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatOperationError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _documents_for_session(db: Session, session_id: str) -> list[tuple[ChatSessionDocument, Document]]:
    rows = db.execute(
        select(ChatSessionDocument, Document)
        .join(Document, Document.id == ChatSessionDocument.document_id)
        .where(ChatSessionDocument.chat_session_id == session_id)
        .order_by(ChatSessionDocument.position)
    ).all()
    return list(rows)


def _selected_document_summaries(
    rows: list[tuple[ChatSessionDocument, Document]],
) -> list[SelectedDocumentSummary]:
    return [
        SelectedDocumentSummary(
            document_id=document.id,
            filename=document.original_filename,
            file_type=document.file_type,
            status=document.status,
            position=link.position,
        )
        for link, document in rows
    ]


def serialize_message(db: Session, message: ChatMessage) -> ChatMessageResponse:
    citations = db.execute(
        select(ChatCitation, Document)
        .join(Document, Document.id == ChatCitation.document_id)
        .where(ChatCitation.message_id == message.id)
        .order_by(ChatCitation.source_order)
    ).all()
    try:
        suggestions = normalize_json_value(message.follow_up_suggestions_json or [], location=f"chat_messages/{message.id}/follow_up_suggestions_json")
    except (ValueError, TypeError):
        suggestions = []
    return ChatMessageResponse(
        message_id=message.id,
        role=message.role,
        content=message.content,
        status=message.status,
        sequence_number=message.sequence_number,
        citations=[
            ChatCitationResponse(
                citation_id=citation.id,
                document_id=citation.document_id,
                document_name=document.original_filename,
                page_number=citation.page_number,
                section_label=citation.section_label,
                excerpt=citation.excerpt,
                source_order=citation.source_order,
            )
            for citation, document in citations
        ],
        follow_up_suggestions=suggestions if isinstance(suggestions, list) else [],
        insufficient_evidence=bool(message.insufficient_evidence),
        created_at=message.created_at,
    )


def serialize_session(db: Session, session: ChatSession, *, detail: bool = False):
    rows = _documents_for_session(db, session.id)
    latest = db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ChatMessage.sequence_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    folder_name = None
    if getattr(session, "folder_id", None):
        from app.database.models.folder import Folder
        f = db.get(Folder, session.folder_id)
        if f:
            folder_name = f.name

    values = dict(
        session_id=session.id,
        title=session.title,
        status=session.status,
        system_mode=session.system_mode,
        document_scope_mode=session.document_scope_mode,
        scope_type=getattr(session, "scope_type", "document") or "document",
        folder_id=getattr(session, "folder_id", None),
        folder_name=folder_name,
        include_subfolders=getattr(session, "include_subfolders", False) or False,
        documents=_selected_document_summaries(rows),
        message_count=session.message_count,
        latest_message_preview=(latest.content[:160] if latest and latest.content else None),
        last_message_at=session.last_message_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
    if detail:
        return ChatSessionDetail(**values, regeneration_count=session.regeneration_count)
    return ChatSessionSummary(**values)


def create_session(
    db: Session,
    *,
    document_ids: list[str],
    title: str | None,
    system_mode: str,
    max_documents: int,
    scope_type: str = "document",
    folder_id: str | None = None,
    include_subfolders: bool = False,
) -> ChatSessionDetail:
    document_ids = list(dict.fromkeys(document_ids))
    if not document_ids:
        raise ChatOperationError(422, "At least one document is required.")
    if len(document_ids) > max_documents:
        raise ChatOperationError(422, f"A chat can include at most {max_documents} documents.")
    documents = list(db.execute(
        select(Document).where(Document.id.in_(document_ids))
    ).scalars().all())
    by_id = {document.id: document for document in documents}
    missing = [item for item in document_ids if item not in by_id]
    if missing:
        raise ChatOperationError(404, f"Document '{missing[0]}' not found.")
    invalid = [
        document for document in documents
        if document.status != "READY" or document.deleted_at is not None
    ]
    if invalid:
        raise ChatOperationError(422, f"Document '{invalid[0].id}' is not ready for chat.")

    now = _utcnow()
    session = ChatSession(
        id=str(uuid4()),
        title=(title or f"Chat with {by_id[document_ids[0]].original_filename}")[:255],
        status="ACTIVE",
        document_scope_mode="single_document" if len(document_ids) == 1 else "multiple_documents",
        scope_type=scope_type,
        folder_id=folder_id,
        include_subfolders=include_subfolders,
        system_mode=system_mode,
        message_count=0,
        regeneration_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    for position, document_id in enumerate(document_ids, start=1):
        db.add(ChatSessionDocument(
            id=str(uuid4()),
            chat_session_id=session.id,
            document_id=document_id,
            position=position,
            created_at=now,
        ))
    study_activity_service.log_chat_activity(
        db, "chat_started", session.id, document_id=document_ids[0], item_count=1
    )
    db.commit()
    return serialize_session(db, session, detail=True)


def get_session(db: Session, session_id: str, *, include_deleted: bool = False) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if not session or (session.deleted_at is not None and not include_deleted):
        raise ChatOperationError(404, f"Chat session '{session_id}' not found.")
    return session


def list_sessions(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
    document_id: str | None,
    order: str,
) -> tuple[list[ChatSessionSummary], int]:
    stmt = select(ChatSession).where(ChatSession.deleted_at.is_(None))
    if search:
        stmt = stmt.where(ChatSession.title.ilike(f"%{search.strip()}%"))
    if status:
        stmt = stmt.where(ChatSession.status == status)
    if document_id:
        stmt = stmt.join(ChatSessionDocument).where(ChatSessionDocument.document_id == document_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    if order == "newest":
        stmt = stmt.order_by(ChatSession.created_at.desc())
    else:
        stmt = stmt.order_by(
            func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc()
        )
    sessions = list(db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all())
    return [serialize_session(db, session) for session in sessions], total


def update_session(
    db: Session,
    session_id: str,
    *,
    title: str | None,
    status: str | None,
    system_mode: str | None,
) -> ChatSessionDetail:
    session = get_session(db, session_id)
    if title is not None:
        session.title = title.strip()
    if system_mode is not None:
        session.system_mode = system_mode
    if status is not None and status != session.status:
        session.status = status
        if status == "ARCHIVED":
            study_activity_service.log_chat_activity(
                db, "chat_session_archived", session.id, item_count=1
            )
    session.updated_at = _utcnow()
    db.commit()
    return serialize_session(db, session, detail=True)


def delete_session(db: Session, session_id: str) -> None:
    session = get_session(db, session_id)
    now = _utcnow()
    session.status = "DELETED"
    session.deleted_at = now
    session.updated_at = now
    db.commit()


def list_messages(
    db: Session, session_id: str, *, page: int, page_size: int
) -> tuple[list[ChatMessageResponse], int]:
    get_session(db, session_id)
    stmt = select(ChatMessage).where(ChatMessage.chat_session_id == session_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    messages = list(db.execute(
        stmt.order_by(ChatMessage.sequence_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all())
    return [serialize_message(db, message) for message in messages], total


def _next_sequence(db: Session, session_id: str) -> int:
    current = db.execute(
        select(func.max(ChatMessage.sequence_number))
        .where(ChatMessage.chat_session_id == session_id)
    ).scalar_one()
    return int(current or 0) + 1


def _refresh_session_metrics(db: Session, session: ChatSession) -> None:
    count = db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.chat_session_id == session.id)
    ).scalar_one()
    last = db.execute(
        select(func.max(ChatMessage.created_at)).where(ChatMessage.chat_session_id == session.id)
    ).scalar_one()
    session.message_count = int(count or 0)
    session.last_message_at = last
    session.updated_at = _utcnow()


def _active_document_rows(
    db: Session, session: ChatSession
) -> list[tuple[ChatSessionDocument, Document]]:
    rows = _documents_for_session(db, session.id)
    if not rows:
        raise ChatOperationError(409, "Session has no selected documents.")
    invalid = [
        document for _, document in rows
        if document.status != "READY" or document.deleted_at is not None
    ]
    if invalid:
        raise ChatOperationError(409, "A selected document is no longer available for retrieval.")
    return rows


def _store_summary_if_needed(db: Session, session: ChatSession, max_recent: int) -> str:
    messages = list(db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role.in_(["user", "assistant"]),
            ChatMessage.status == "COMPLETED",
        )
        .order_by(ChatMessage.sequence_number)
    ).scalars().all())
    older = messages[:-max_recent]
    if len(older) < max_recent:
        existing = db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.chat_session_id == session.id,
                ChatMessage.role == "system_summary",
            )
            .order_by(ChatMessage.sequence_number.desc())
            .limit(1)
        ).scalar_one_or_none()
        return existing.content if existing else ""
    summary_text = deterministic_summary(older)
    existing = db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role == "system_summary",
        )
        .order_by(ChatMessage.sequence_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing:
        existing.content = summary_text
        existing.updated_at = _utcnow()
    elif summary_text:
        now = _utcnow()
        db.add(ChatMessage(
            id=str(uuid4()),
            chat_session_id=session.id,
            role="system_summary",
            content=summary_text,
            status="COMPLETED",
            sequence_number=_next_sequence(db, session.id),
            prompt_version="deterministic_summary_v1",
            created_at=now,
            updated_at=now,
        ))
        db.flush()
    return summary_text


def _create_pending_assistant(
    db: Session,
    session: ChatSession,
    *,
    supersedes_message_id: str | None = None,
) -> ChatMessage:
    now = _utcnow()
    assistant = ChatMessage(
        id=str(uuid4()),
        chat_session_id=session.id,
        role="assistant",
        content="",
        status="PENDING",
        sequence_number=_next_sequence(db, session.id),
        prompt_version=CHAT_PROMPT_VERSION,
        supersedes_message_id=supersedes_message_id,
        created_at=now,
        updated_at=now,
    )
    db.add(assistant)
    db.flush()
    _refresh_session_metrics(db, session)
    db.commit()
    return assistant


def _complete_assistant(
    db: Session,
    session: ChatSession,
    assistant: ChatMessage,
    answer: GeneratedChatAnswer,
    retrieval_query: str,
    context_chars: int,
    retrieved_chunk_count: int,
) -> None:
    db.refresh(assistant)
    if assistant.status == "CANCELLED":
        _refresh_session_metrics(db, session)
        db.commit()
        return
    assistant.content = answer.answer
    assistant.status = "COMPLETED"
    assistant.prompt_version = answer.prompt_version
    assistant.model_name = answer.model_name
    assistant.input_token_estimate = answer.input_token_estimate
    assistant.output_token_estimate = answer.output_token_estimate
    assistant.follow_up_suggestions_json = answer.follow_up_suggestions
    assistant.insufficient_evidence = answer.insufficient_evidence
    assistant.updated_at = _utcnow()
    for citation in answer.citations:
        db.add(ChatCitation(
            id=str(uuid4()),
            message_id=assistant.id,
            document_id=citation.document_id,
            chunk_index=citation.chunk_index,
            page_number=citation.page_number,
            section_label=citation.section_label,
            excerpt=citation.excerpt,
            source_order=citation.source_order,
            created_at=_utcnow(),
        ))
    db.add(ChatGenerationRecord(
        id=str(uuid4()),
        assistant_message_id=assistant.id,
        retrieval_query=retrieval_query[:1000],
        retrieved_chunk_count=retrieved_chunk_count,
        context_character_count=context_chars,
        prompt_version=answer.prompt_version,
        model_name=answer.model_name,
        generation_status="COMPLETED",
        created_at=_utcnow(),
    ))
    study_activity_service.log_chat_activity(
        db, "chat_message_completed", session.id, item_count=1
    )
    _refresh_session_metrics(db, session)
    db.commit()


def _fail_assistant(
    db: Session,
    session: ChatSession,
    assistant: ChatMessage,
    retrieval_query: str,
    model_name: str,
    failure_code: str,
) -> None:
    db.refresh(assistant)
    if assistant.status == "CANCELLED":
        _refresh_session_metrics(db, session)
        db.commit()
        return
    assistant.status = "FAILED"
    assistant.content = "The answer could not be generated. Your question was saved; please retry."
    assistant.model_name = model_name
    assistant.updated_at = _utcnow()
    db.add(ChatGenerationRecord(
        id=str(uuid4()),
        assistant_message_id=assistant.id,
        retrieval_query=retrieval_query[:1000],
        retrieved_chunk_count=0,
        context_character_count=0,
        prompt_version=CHAT_PROMPT_VERSION,
        model_name=model_name,
        generation_status="FAILED",
        failure_code=failure_code,
        created_at=_utcnow(),
    ))
    _refresh_session_metrics(db, session)
    db.commit()


def _run_generation(
    db: Session,
    *,
    session: ChatSession,
    user_message: ChatMessage,
    assistant: ChatMessage,
    mode: str,
    rows: list[tuple[ChatSessionDocument, Document]],
    collection,
    model,
    settings,
    model_available: bool,
    unavailable_reason: str,
) -> None:
    previous = list(db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.sequence_number < user_message.sequence_number,
        )
        .order_by(ChatMessage.sequence_number)
    ).scalars().all())
    retrieval_query = build_retrieval_query(user_message.content, previous)
    model_name = getattr(model, "model_name", None) or settings.gemini_model
    if is_boundary_attack(user_message.content):
        safe = GeneratedChatAnswer(
            answer=BOUNDARY_RESPONSE,
            citations=[],
            insufficient_evidence=True,
            follow_up_suggestions=["Summarize this document"],
            prompt_version=CHAT_PROMPT_VERSION,
            model_name=model_name,
            input_token_estimate=0,
            output_token_estimate=len(BOUNDARY_RESPONSE) // 4,
        )
        _complete_assistant(db, session, assistant, safe, retrieval_query, 0, 0)
        return

    document_ids = [document.id for _, document in rows]
    owner_user_id = rows[0][1].user_id if rows else None
    try:
        chunks = retrieve_chat_chunks(
            collection,
            retrieval_query,
            document_ids,
            top_k=settings.chat_retrieval_top_k,
            relevance_threshold=settings.chat_relevance_threshold,
            max_chunks_per_document=settings.chat_max_chunks_per_document,
            max_context_characters=settings.chat_max_context_characters,
            user_id=owner_user_id,
        )
    except Exception:
        _fail_assistant(db, session, assistant, retrieval_query, model_name, "RETRIEVAL_UNAVAILABLE")
        raise ChatOperationError(503, "Document retrieval is currently unavailable.")
    summary = _store_summary_if_needed(db, session, settings.chat_max_recent_messages)
    history = recent_history(previous, settings.chat_max_recent_messages)
    try:
        answer = generate_chat_answer(
            model,
            model_available=model_available,
            unavailable_reason=unavailable_reason,
            model_name=model_name,
            question=user_message.content,
            mode=mode,
            chunks=chunks,
            selected_document_ids=set(document_ids),
            recent_messages=[(item.role, item.content) for item in history],
            conversation_summary=summary,
            max_retries=settings.chat_max_ai_retries,
        )
    except (ChatProviderUnavailable, ChatInvalidOutput):
        answer = generate_extractive_fallback(
            question=user_message.content,
            chunks=chunks,
            selected_document_ids=set(document_ids),
        )
    except Exception:
        _fail_assistant(db, session, assistant, retrieval_query, model_name, "GENERATION_FAILED")
        raise ChatOperationError(502, "The answer could not be generated.")
    _complete_assistant(
        db, session, assistant, answer, retrieval_query,
        sum(len(chunk.text) for chunk in chunks),
        len(chunks),
    )


def send_message(
    db: Session,
    *,
    session_id: str,
    text: str,
    mode: str | None,
    collection,
    model,
    settings,
    model_available: bool,
    unavailable_reason: str,
) -> tuple[ChatMessageResponse, ChatMessageResponse]:
    session = get_session(db, session_id)
    if session.status != "ACTIVE":
        raise ChatOperationError(409, "Only active chat sessions accept new messages.")
    if len(text) > settings.chat_max_message_characters:
        raise ChatOperationError(
            413, f"Message exceeds the {settings.chat_max_message_characters}-character limit."
        )
    rows = _active_document_rows(db, session)
    latest = db.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == session.id)
        .order_by(ChatMessage.sequence_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest and latest.role == "assistant" and latest.status == "PENDING":
        raise ChatOperationError(409, "A message is already processing.")

    # Retrying the exact immediately-failed request reuses its user message.
    user_message: ChatMessage | None = None
    if latest and latest.role == "assistant" and latest.status == "FAILED":
        prior = db.execute(
            select(ChatMessage).where(
                ChatMessage.chat_session_id == session.id,
                ChatMessage.sequence_number < latest.sequence_number,
                ChatMessage.role == "user",
            ).order_by(ChatMessage.sequence_number.desc()).limit(1)
        ).scalar_one_or_none()
        if prior and prior.content == text:
            user_message = prior
    if user_message is None:
        now = _utcnow()
        user_message = ChatMessage(
            id=str(uuid4()),
            chat_session_id=session.id,
            role="user",
            content=text,
            status="COMPLETED",
            sequence_number=_next_sequence(db, session.id),
            created_at=now,
            updated_at=now,
        )
        db.add(user_message)
        db.flush()
        _refresh_session_metrics(db, session)
        db.commit()  # preserve the question before any provider work

    assistant = _create_pending_assistant(db, session)
    _run_generation(
        db, session=session, user_message=user_message, assistant=assistant,
        mode=mode or session.system_mode, rows=rows, collection=collection,
        model=model, settings=settings, model_available=model_available,
        unavailable_reason=unavailable_reason,
    )
    db.refresh(user_message)
    db.refresh(assistant)
    return serialize_message(db, user_message), serialize_message(db, assistant)


def regenerate(
    db: Session,
    *,
    session_id: str,
    collection,
    model,
    settings,
    model_available: bool,
    unavailable_reason: str,
) -> tuple[ChatMessageResponse, ChatMessageResponse]:
    session = get_session(db, session_id)
    if session.status != "ACTIVE":
        raise ChatOperationError(409, "Only active sessions can regenerate messages.")
    if session.regeneration_count >= settings.chat_max_regenerations:
        raise ChatOperationError(409, "Regeneration limit reached for this session.")
    rows = _active_document_rows(db, session)
    latest_assistant = db.execute(
        select(ChatMessage).where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role == "assistant",
            ChatMessage.status == "COMPLETED",
        ).order_by(ChatMessage.sequence_number.desc()).limit(1)
    ).scalar_one_or_none()
    if not latest_assistant:
        raise ChatOperationError(409, "There is no completed assistant response to regenerate.")
    user_message = db.execute(
        select(ChatMessage).where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role == "user",
            ChatMessage.sequence_number < latest_assistant.sequence_number,
        ).order_by(ChatMessage.sequence_number.desc()).limit(1)
    ).scalar_one_or_none()
    if not user_message:
        raise ChatOperationError(409, "The preceding user message is unavailable.")
    session.regeneration_count += 1
    assistant = _create_pending_assistant(
        db, session, supersedes_message_id=latest_assistant.id
    )
    _run_generation(
        db, session=session, user_message=user_message, assistant=assistant,
        mode=session.system_mode, rows=rows, collection=collection, model=model,
        settings=settings, model_available=model_available,
        unavailable_reason=unavailable_reason,
    )
    return serialize_message(db, user_message), serialize_message(db, assistant)


def cancel_pending(db: Session, session_id: str) -> str | None:
    session = get_session(db, session_id)
    pending = db.execute(
        select(ChatMessage).where(
            ChatMessage.chat_session_id == session.id,
            ChatMessage.role == "assistant",
            ChatMessage.status == "PENDING",
        ).order_by(ChatMessage.sequence_number.desc()).limit(1)
    ).scalar_one_or_none()
    if not pending:
        return None
    pending.status = "CANCELLED"
    pending.content = ""
    pending.updated_at = _utcnow()
    _refresh_session_metrics(db, session)
    db.commit()
    return pending.id


def usage_summary(db: Session) -> dict[str, int]:
    sessions = db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.deleted_at.is_(None))
    ).scalar_one() or 0
    questions = db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.role == "user")
    ).scalar_one() or 0
    documents = db.execute(
        select(func.count(func.distinct(ChatSessionDocument.document_id)))
        .join(ChatSession, ChatSession.id == ChatSessionDocument.chat_session_id)
        .where(ChatSession.deleted_at.is_(None))
    ).scalar_one() or 0
    return {
        "sessions_started": int(sessions),
        "questions_asked": int(questions),
        "documents_discussed": int(documents),
    }
