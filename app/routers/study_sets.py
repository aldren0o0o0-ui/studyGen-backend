"""
app/routers/study_sets.py — Phase 5
Endpoints for Study Sets and Flashcards.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.schemas.flashcards import (
    StudySetCreate, StudySetUpdate, StudySetSummary, StudySetDetail,
    StudySetListResponse, FlashcardCreate, FlashcardSafePreview
)
from app.services import flashcard_service
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/study-sets", tags=["study-sets"], dependencies=[Depends(get_current_user)])


@router.post("", response_model=StudySetDetail, status_code=201)
async def create_study_set_endpoint(req: StudySetCreate, request: Request):
    def _action():
        db = SessionLocal()
        try:
            st = flashcard_service.create_study_set(db, req)
            counts = flashcard_service.get_study_set_counts(db, st.id)
            cards = flashcard_service.list_flashcards_in_set(db, st.id)
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
                cards=cards,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.get("", response_model=StudySetListResponse)
async def list_study_sets_endpoint(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    document_id: Optional[str] = Query(default=None),
    topic: Optional[str] = Query(default=None),
    set_type: Optional[str] = Query(default=None),
    status: str = Query(default="ACTIVE"),
):
    def _action():
        db = SessionLocal()
        try:
            sets, total = flashcard_service.list_study_sets(
                db, page=page, page_size=page_size, search=search,
                document_id=document_id, topic=topic, set_type=set_type, status=status
            )
            summaries: List[StudySetSummary] = []
            for st in sets:
                counts = flashcard_service.get_study_set_counts(db, st.id)
                doc_filename = st.document.original_filename if st.document else None
                src_count = len(st.source_documents) if st.source_documents else (1 if st.document_id else 0)
                summaries.append(
                    StudySetSummary(
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
                    )
                )
            return summaries, total
        finally:
            db.close()

    summaries, total = await run_in_threadpool(_action)
    return StudySetListResponse(items=summaries, page=page, page_size=page_size, total=total)


@router.get("/due", response_model=List[StudySetSummary])
async def get_due_study_sets_endpoint(request: Request):
    def _action():
        db = SessionLocal()
        try:
            sets = flashcard_service.list_due_study_sets(db)
            summaries: List[StudySetSummary] = []
            for st in sets:
                counts = flashcard_service.get_study_set_counts(db, st.id)
                doc_filename = st.document.original_filename if st.document else None
                src_count = len(st.source_documents) if st.source_documents else (1 if st.document_id else 0)
                summaries.append(
                    StudySetSummary(
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
                    )
                )
            return summaries
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.get("/{study_set_id}", response_model=StudySetDetail)
async def get_study_set_endpoint(study_set_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            st = flashcard_service.get_study_set(db, study_set_id)
            counts = flashcard_service.get_study_set_counts(db, st.id)
            cards = flashcard_service.list_flashcards_in_set(db, st.id)
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
                cards=cards,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.put("/{study_set_id}", response_model=StudySetSummary)
@router.patch("/{study_set_id}", response_model=StudySetSummary)
async def update_study_set_endpoint(study_set_id: str, req: StudySetUpdate, request: Request):
    def _action():
        db = SessionLocal()
        try:
            st = flashcard_service.update_study_set(db, study_set_id, req)
            counts = flashcard_service.get_study_set_counts(db, st.id)
            doc_filename = st.document.original_filename if st.document else None
            return StudySetSummary(
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
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.delete("/{study_set_id}")
async def delete_study_set_endpoint(study_set_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            st = flashcard_service.delete_study_set(db, study_set_id)
            return {"study_set_id": st.id, "status": "deleted"}
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/{study_set_id}/cards", response_model=FlashcardSafePreview, status_code=201)
async def add_flashcard_endpoint(study_set_id: str, req: FlashcardCreate, request: Request):
    def _action():
        db = SessionLocal()
        try:
            card = flashcard_service.add_flashcard(db, study_set_id, req)
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


@router.get("/{study_set_id}/cards", response_model=List[FlashcardSafePreview])
async def list_flashcards_in_set_endpoint(study_set_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            return flashcard_service.list_flashcards_in_set(db, study_set_id)
        finally:
            db.close()

    return await run_in_threadpool(_action)
