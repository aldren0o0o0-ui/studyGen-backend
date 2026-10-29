"""
routers/quizzes.py — Phase 3
Generate, persist, and retrieve quizzes.

Phase 3 changes:
  - /generate returns ActiveQuizResponse (hides correct answers).
  - Full Phase 3 generation pipeline (context coverage, source mapping, validation).
  - Legacy /migrate endpoint disabled by default (returns 410).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.database.models.quiz import Question as QuestionModel, Quiz as QuizModel
from app.database.session import SessionLocal
from app.database.types import normalize_json_value
from app.schemas.quizzes import (
    ActiveQuestion, ActiveQuizResponse,
    GenerateQuizRequest, Question, QuizDetail,
    QuizListResponse, QuizResponse, QuizSummary, QuestionSource,
)
from app.services import document_service, quiz_history_service
from app.services.context_selection import select_context_chunks
from app.services.quiz_generation import generate_quiz, GeneratedQuiz
from app.services.question_validation import ValidatedQuestion, SourceReference
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quizzes", tags=["quizzes"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# POST /quizzes/generate
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=ActiveQuizResponse)
async def generate_quiz_endpoint(
    body: GenerateQuizRequest,
    request: Request,
    current_user: Any = Depends(get_current_user),
):
    """
    Generate a new quiz from a single document, a multi-selected set of documents,
    or a folder (direct or recursive).
    Returns ActiveQuizResponse which OMITS correct answers, accepted answers,
    and explanations until the attempt is submitted.
    """
    collection = request.app.state.collection
    model = request.app.state.gemini_model
    settings = request.app.state.settings
    if not getattr(request.app.state, "gemini_available", True):
        raise HTTPException(
            status_code=503,
            detail=getattr(
                request.app.state,
                "gemini_unavailable_reason",
                "AI quiz generation is not configured.",
            ),
        )

    # 1. Resolve authorized document scope via Wave 1 AIScopeResolver
    from app.schemas.ai_scope import AIScopeRequest, AIScopeType
    from app.services.ai_scope_service import resolve_ai_scope, AIScopeError
    from app.services.quiz_coverage_planner import QuizCoveragePlanner, ScopedDocumentInfo
    from app.services.retrieval_service import retrieve_context

    if body.scope_type:
        st = body.scope_type.lower()
        if st in ("folder", AIScopeType.FOLDER.value):
            scope_type = AIScopeType.FOLDER
        elif st in ("selected_documents", AIScopeType.SELECTED_DOCUMENTS.value):
            scope_type = AIScopeType.SELECTED_DOCUMENTS
        else:
            scope_type = AIScopeType.DOCUMENT
    elif body.folder_id:
        scope_type = AIScopeType.FOLDER
    elif body.document_ids and len(body.document_ids) > 1:
        scope_type = AIScopeType.SELECTED_DOCUMENTS
    else:
        scope_type = AIScopeType.DOCUMENT

    doc_id = body.document_id or (body.document_ids[0] if body.document_ids and len(body.document_ids) == 1 else None)
    doc_ids = body.document_ids or ([body.document_id] if body.document_id else None)

    scope_req = AIScopeRequest(
        scope_type=scope_type,
        document_id=doc_id if scope_type == AIScopeType.DOCUMENT else None,
        document_ids=doc_ids if scope_type == AIScopeType.SELECTED_DOCUMENTS else None,
        folder_id=body.folder_id if scope_type == AIScopeType.FOLDER else None,
        include_subfolders=body.include_subfolders,
    )

    def _resolve_and_plan():
        db = SessionLocal()
        try:
            try:
                resolved = resolve_ai_scope(db, current_user.id, scope_req)
            except AIScopeError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail)
            except Exception as exc:
                status_code = getattr(exc, "status_code", 400)
                detail = getattr(exc, "detail", str(exc))
                raise HTTPException(status_code=status_code, detail=detail)

            if not resolved.document_ids:
                raise HTTPException(404, "No ready documents found for quiz generation.")

            doc_infos = [
                ScopedDocumentInfo(
                    document_id=d.document_id,
                    filename=d.filename,
                    chunk_count=1,
                )
                for d in resolved.documents
            ]
            plan = QuizCoveragePlanner.plan_coverage(
                doc_infos,
                body.question_count,
                coverage_mode=body.coverage_mode,
                question_types=body.question_types,
                difficulty=body.difficulty,
            )
            return resolved, plan
        finally:
            db.close()

    resolved_scope, coverage_plan = await run_in_threadpool(_resolve_and_plan)

    # 2. Select context chunks using Wave 2 RetrievalService
    context_chunks, metrics = await run_in_threadpool(
        retrieve_context,
        collection,
        body.topic_query or "quiz key concepts and facts",
        resolved_scope.document_ids,
        user_id=current_user.id,
        purpose="quiz_generation",
    )

    if not context_chunks:
        raise HTTPException(
            status_code=404,
            detail="Insufficient context found in selected materials to generate a quiz.",
        )

    # 3. Determine source label for quiz title
    if resolved_scope.scope_type == "document":
        source_label = resolved_scope.documents[0].filename if resolved_scope.documents else "Document"
    elif resolved_scope.scope_type == "folder":
        source_label = resolved_scope.folder_name or "Folder"
    else:
        source_label = f"{len(resolved_scope.document_ids)} Documents"

    # 4. Generate quiz via Gemini (Phase 3 & Wave 3 pipeline)
    generated: GeneratedQuiz = await run_in_threadpool(
        generate_quiz,
        model,
        body,
        context_chunks,
        source_label,
        settings.max_quiz_generation_retries,
        coverage_plan=coverage_plan,
        scope_type=resolved_scope.scope_type.upper(),
        folder_id=resolved_scope.folder_id,
        include_subfolders=resolved_scope.include_subfolders,
        source_document_ids=resolved_scope.document_ids,
    )

    # 5. Persist atomically to database
    def _persist():
        db = SessionLocal()
        try:
            from app.database.models.quiz import QuizSourceDocument
            quiz_id = str(uuid.uuid4())
            primary_doc_id = resolved_scope.document_ids[0] if resolved_scope.document_ids else None
            db_quiz = QuizModel(
                id=quiz_id,
                user_id=current_user.id,
                document_id=primary_doc_id,
                scope_type=generated.scope_type,
                folder_id=generated.folder_id,
                include_subfolders=generated.include_subfolders,
                title=generated.title,
                difficulty=generated.difficulty,
                question_count=len(generated.questions),
                question_types_json=generated.question_types,
                chunks_used=generated.chunks_used,
                coverage_mode=generated.coverage_mode,
                configuration_json=generated.configuration_json,
                prompt_version=generated.prompt_version,
                model_name=generated.model_name,
                quiz_mode="generated",
                created_at=datetime.now(timezone.utc),
            )
            db.add(db_quiz)

            # Persist snapshot source documents
            for doc_id in resolved_scope.document_ids:
                db_src = QuizSourceDocument(
                    id=str(uuid.uuid4()),
                    quiz_id=quiz_id,
                    document_id=doc_id,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(db_src)

            db_questions = []
            for i, vq in enumerate(generated.questions):
                q_id = str(uuid.uuid4())
                refs = [
                    {
                        "document_id": s.document_id or primary_doc_id,
                        "chunk_index": s.chunk_index,
                        "page_number": s.page_number,
                        "filename": s.filename,
                        "excerpt": s.excerpt,
                    }
                    for s in vq.sources
                ]
                db_q = QuestionModel(
                    id=q_id,
                    quiz_id=quiz_id,
                    position=i,
                    question_text=vq.question_text,
                    options_json=vq.options,
                    correct_answer=vq.correct_answer,
                    accepted_answers_json=vq.accepted_answers,
                    explanation=vq.explanation,
                    source_chunk_indexes_json=[s.chunk_index for s in vq.sources],
                    source_references_json=refs,
                    question_type=vq.question_type,
                    topic_label=vq.topic,
                    difficulty=vq.difficulty,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(db_q)
                db_questions.append((q_id, vq))

            db.commit()
            return quiz_id, db_questions
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    quiz_id, db_questions = await run_in_threadpool(_persist)

    # 6. Build safe ActiveQuizResponse (no correct answers, no explanations)
    active_questions = [
        ActiveQuestion(
            question_id=q_id,
            question_type=vq.question_type,
            question=vq.question_text,
            options=vq.options,
            difficulty=vq.difficulty,
            topic=vq.topic,
        )
        for q_id, vq in db_questions
    ]

    return ActiveQuizResponse(
        quiz_id=quiz_id,
        document_id=generated.document_id,
        scope_type=resolved_scope.scope_type.upper(),
        folder_id=resolved_scope.folder_id,
        include_subfolders=resolved_scope.include_subfolders,
        source_document_count=len(resolved_scope.document_ids),
        quiz_title=generated.title,
        difficulty=generated.difficulty,
        questions=active_questions,
        source_file=source_label,
        chunks_used=generated.chunks_used,
        prompt_version=generated.prompt_version,
    )


# ---------------------------------------------------------------------------
# GET /quizzes
# ---------------------------------------------------------------------------

@router.get("", response_model=QuizListResponse)
async def list_quizzes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: Optional[str] = Query(default=None),
    difficulty: Optional[str] = Query(default=None),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = quiz_history_service.list_quizzes(
                db, page=page, page_size=page_size,
                document_id=document_id, difficulty=difficulty,
            )
            summaries = [
                QuizSummary(
                    quiz_id=q.id,
                    document_id=q.document_id,
                    scope_type=getattr(q, "scope_type", "DOCUMENT"),
                    folder_id=getattr(q, "folder_id", None),
                    include_subfolders=getattr(q, "include_subfolders", False),
                    source_document_count=len(getattr(q, "source_documents", []) or [1]),
                    title=q.title,
                    difficulty=q.difficulty,
                    question_count=q.question_count,
                    chunks_used=q.chunks_used,
                    coverage_mode=getattr(q, "coverage_mode", "balanced"),
                    quiz_mode=getattr(q, "quiz_mode", "generated"),
                    prompt_version=getattr(q, "prompt_version", "legacy_phase2"),
                    created_at=q.created_at,
                )
                for q in items
            ]
            return summaries, total
        finally:
            db.close()

    summaries, total = await run_in_threadpool(_query)
    return QuizListResponse(items=summaries, page=page, page_size=page_size, total=total)


# ---------------------------------------------------------------------------
# GET /quizzes/{quiz_id}
# ---------------------------------------------------------------------------

@router.get("/{quiz_id}", response_model=QuizDetail)
async def get_quiz(quiz_id: str):
    def _query():
        db = SessionLocal()
        try:
            from app.services.quiz_history_service import get_quiz_by_id
            quiz = get_quiz_by_id(db, quiz_id)
            if not quiz:
                raise HTTPException(404, f"Quiz '{quiz_id}' not found.")
            return quiz
        finally:
            db.close()

    quiz = await run_in_threadpool(_query)
    questions = [_model_to_schema(q) for q in quiz.questions]
    config = None
    try:
        config = normalize_json_value(quiz.configuration_json or {}, location=f"quizzes/{quiz.id}/configuration_json")
    except Exception:
        pass

    return QuizDetail(
        quiz_id=quiz.id,
        document_id=quiz.document_id,
        scope_type=getattr(quiz, "scope_type", "DOCUMENT"),
        folder_id=getattr(quiz, "folder_id", None),
        include_subfolders=getattr(quiz, "include_subfolders", False),
        source_document_count=len(getattr(quiz, "source_documents", []) or [1]),
        title=quiz.title,
        difficulty=quiz.difficulty,
        question_count=quiz.question_count,
        chunks_used=quiz.chunks_used,
        coverage_mode=getattr(quiz, "coverage_mode", "balanced"),
        quiz_mode=getattr(quiz, "quiz_mode", "generated"),
        prompt_version=getattr(quiz, "prompt_version", "legacy_phase2"),
        created_at=quiz.created_at,
        questions=questions,
        configuration=config,
    )


# ---------------------------------------------------------------------------
# POST /quizzes/migrate (Legacy — disabled by default)
# ---------------------------------------------------------------------------

class LegacyQuestion(BaseModel):
    question: str
    options: Optional[List[str]] = None
    answer: str

class LegacyQuiz(BaseModel):
    id: str
    quiz_title: str
    timestamp: str
    questions: List[LegacyQuestion]


@router.post("/migrate", status_code=201)
async def migrate_legacy_quizzes(legacy_quizzes: List[LegacyQuiz], request: Request):
    """
    One-time migration endpoint for legacy AsyncStorage quizzes.
    Disabled by default (ENABLE_LEGACY_HISTORY_MIGRATION=false).
    Returns 410 Gone when disabled.
    """
    settings = request.app.state.settings
    if not settings.enable_legacy_history_migration:
        raise HTTPException(
            status_code=410,
            detail=(
                "The legacy migration endpoint is disabled. "
                "Set ENABLE_LEGACY_HISTORY_MIGRATION=true to enable it during migration."
            ),
        )

    def _migrate():
        db = SessionLocal()
        try:
            from app.database.models.document import Document

            legacy_doc_id = "00000000-0000-0000-0000-000000000000"
            doc = db.get(Document, legacy_doc_id)
            if not doc:
                doc = Document(
                    id=legacy_doc_id,
                    original_filename="Legacy App Data",
                    file_type="txt",
                    file_size=0,
                    chunk_count=0,
                    status="READY",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(doc)
                db.commit()

            imported = 0
            for lq in legacy_quizzes:
                # Idempotency: skip if a quiz with this exact title already exists as legacy
                existing = (
                    db.query(QuizModel)
                    .filter(
                        QuizModel.document_id == legacy_doc_id,
                        QuizModel.title == (lq.quiz_title or "Legacy Quiz"),
                    )
                    .first()
                )
                if existing:
                    continue

                quiz_id = str(uuid.uuid4())
                db_quiz = QuizModel(
                    id=quiz_id,
                    document_id=legacy_doc_id,
                    title=lq.quiz_title or "Legacy Quiz",
                    difficulty="medium",
                    question_count=len(lq.questions),
                    question_types_json=["multiple_choice"],
                    chunks_used=0,
                    quiz_mode="generated",
                    prompt_version="legacy_migration",
                    model_name="unknown",
                    coverage_mode="balanced",
                    configuration_json={},
                    created_at=datetime.now(timezone.utc),
                )
                db.add(db_quiz)

                for i, q in enumerate(lq.questions):
                    options = q.options or []
                    # Sanitize: correct_answer must be in options
                    correct = q.answer if q.answer in options else (options[0] if options else q.answer)
                    db.add(QuestionModel(
                        id=str(uuid.uuid4()),
                        quiz_id=quiz_id,
                        position=i,
                        question_text=q.question,
                        options_json=options,
                        correct_answer=correct,
                        accepted_answers_json=[correct.lower()],
                        explanation=None,
                        source_chunk_indexes_json=[],
                        source_references_json=[],
                        question_type="multiple_choice",
                        difficulty="medium",
                        created_at=datetime.now(timezone.utc),
                    ))
                imported += 1

            db.commit()
            return imported
        finally:
            db.close()

    count = await run_in_threadpool(_migrate)
    return {"status": "success", "imported": count}


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _model_to_schema(q: QuestionModel) -> Question:
    sources = []
    try:
        raw_refs = normalize_json_value(q.source_references_json or [], location=f"questions/{q.id}/source_references_json")
        for ref in raw_refs:
            sources.append(QuestionSource(
                chunk_index=ref.get("chunk_index", 0),
                page_number=ref.get("page_number", -1),
                filename=ref.get("filename", ""),
                excerpt=ref.get("excerpt", ""),
            ))
    except Exception:
        pass

    return Question(
        question_id=q.id,
        question_type=getattr(q, "question_type", "multiple_choice"),
        question=q.question_text,
        options=normalize_json_value(q.options_json or [], location=f"questions/{q.id}/options_json"),
        correct_answer=q.correct_answer,
        accepted_answers=normalize_json_value(getattr(q, "accepted_answers_json", []) or [], location=f"questions/{q.id}/accepted_answers_json"),
        explanation=q.explanation,
        difficulty=getattr(q, "difficulty", "medium"),
        topic=getattr(q, "topic_label", None),
        sources=sources,
        source_chunk_indexes=normalize_json_value(q.source_chunk_indexes_json or [], location=f"questions/{q.id}/source_chunk_indexes_json"),
    )
