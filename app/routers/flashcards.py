"""
app/routers/flashcards.py — Phase 5
Endpoints for flashcard editing, AI generation, and quiz-to-flashcard conversion.
"""
from __future__ import annotations

import logging
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.schemas.flashcards import (
    FlashcardUpdate, FlashcardSafePreview, GenerateFlashcardsRequest,
    ConvertQuizToFlashcardsRequest, StudySetDetail
)
from app.services import flashcard_service
from app.services.flashcard_generation_service import generate_flashcards_from_document
from app.services.quiz_to_flashcard_service import convert_quiz_questions_to_flashcards
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flashcards", tags=["flashcards"], dependencies=[Depends(get_current_user)])


@router.patch("/{flashcard_id}", response_model=FlashcardSafePreview)
async def update_flashcard_endpoint(flashcard_id: str, req: FlashcardUpdate, request: Request):
    def _action():
        db = SessionLocal()
        try:
            card = flashcard_service.update_flashcard(db, flashcard_id, req)
            state = card.review_state
            return FlashcardSafePreview(
                flashcard_id=card.id,
                study_set_id=card.study_set_id,
                front_text=card.front_text,
                back_text=card.back_text,
                hint_text=card.hint_text,
                topic_label=card.topic_label,
                difficulty=card.difficulty,
                card_type=card.card_type,
                generation_method=card.generation_method,
                position=card.position,
                status=card.status,
                is_user_edited=card.is_user_edited,
                review_status=state.review_status if state else "NEW",
                mastery_score=state.mastery_score if state else 0.0,
                next_review_at=state.next_review_at if state else None,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.delete("/{flashcard_id}")
async def delete_flashcard_endpoint(flashcard_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            card = flashcard_service.delete_flashcard(db, flashcard_id)
            return {"flashcard_id": card.id, "status": "deleted"}
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/generate", response_model=StudySetDetail, status_code=201)
async def generate_flashcards_endpoint(
    req: GenerateFlashcardsRequest,
    request: Request,
    current_user: Any = Depends(get_current_user),
):
    collection = request.app.state.collection
    gemini_model = request.app.state.gemini_model
    if not getattr(request.app.state, "gemini_available", True):
        raise HTTPException(
            status_code=503,
            detail=getattr(
                request.app.state,
                "gemini_unavailable_reason",
                "AI flashcard generation is not configured.",
            ),
        )

    def _action():
        db = SessionLocal()
        try:
            st, cards, skipped = generate_flashcards_from_document(
                db, collection, gemini_model, req, user_id=current_user.id
            )
            counts = flashcard_service.get_study_set_counts(db, st.id)
            previews = flashcard_service.list_flashcards_in_set(db, st.id)
            doc_filename = st.document.original_filename if st.document else None
            src_count = len(st.source_documents) if st.source_documents else (1 if st.document_id else 0)
            return StudySetDetail(
                study_set_id=st.id,
                title=st.title,
                description=st.description,
                document_id=st.document_id,
                topic_label=st.topic_label,
                set_type=st.set_type,
                status=st.status,
                scope_type=st.scope_type,
                folder_id=st.folder_id,
                include_subfolders=st.include_subfolders,
                source_document_count=src_count,
                card_count=counts["card_count"],
                due_card_count=counts["due_card_count"],
                new_card_count=counts["new_card_count"],
                mastered_card_count=counts["mastered_card_count"],
                created_at=st.created_at,
                updated_at=st.updated_at,
                last_reviewed_at=None,
                document_filename=doc_filename,
                cards=previews,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/from-quiz", response_model=StudySetDetail, status_code=201)
async def convert_quiz_to_flashcards_endpoint(req: ConvertQuizToFlashcardsRequest, request: Request):
    def _action():
        db = SessionLocal()
        try:
            st, created_cards, skipped = convert_quiz_questions_to_flashcards(
                db,
                quiz_id=req.quiz_id,
                target_study_set_id=req.study_set_id,
                custom_title=req.title,
                question_ids=req.question_ids,
            )
            counts = flashcard_service.get_study_set_counts(db, st.id)
            previews = flashcard_service.list_flashcards_in_set(db, st.id)
            doc_filename = st.document.original_filename if st.document else None
            return StudySetDetail(
                study_set_id=st.id,
                title=st.title,
                description=st.description,
                document_id=st.document_id,
                topic_label=st.topic_label,
                set_type=st.set_type,
                status=st.status,
                card_count=counts["card_count"],
                due_card_count=counts["due_card_count"],
                new_card_count=counts["new_card_count"],
                mastered_card_count=counts["mastered_card_count"],
                created_at=st.created_at,
                updated_at=st.updated_at,
                last_reviewed_at=None,
                document_filename=doc_filename,
                cards=previews,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)
