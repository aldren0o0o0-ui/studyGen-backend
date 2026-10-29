"""
app/routers/flashcard_review_sessions.py — Phase 5
Endpoints for flashcard review sessions, card reveals, and self-assessment ratings.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.schemas.flashcards import (
    CreateReviewSessionRequest, ReviewRatingRequest, FlashcardReviewSessionSummary,
    FlashcardReviewSessionDetail, ActiveReviewCard, RevealedReviewCard
)
from app.services import flashcard_review_service
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flashcard-review-sessions", tags=["flashcard-review-sessions"], dependencies=[Depends(get_current_user)])


@router.post("", response_model=FlashcardReviewSessionDetail, status_code=201)
async def create_review_session_endpoint(req: CreateReviewSessionRequest, request: Request):
    def _action():
        db = SessionLocal()
        try:
            session = flashcard_review_service.create_review_session(db, req)
            items = [flashcard_review_service.get_active_review_card(item) for item in session.items]
            first_card = items[0] if items else None
            return FlashcardReviewSessionDetail(
                session_id=session.id,
                study_set_id=session.study_set_id,
                document_id=session.document_id,
                session_mode=session.session_mode,
                status=session.status,
                requested_count=session.requested_count,
                actual_count=session.actual_count,
                reviewed_count=session.reviewed_count,
                correct_count=session.correct_count,
                started_at=session.started_at,
                completed_at=session.completed_at,
                current_card=first_card,
                revealed_card=None,
                items=items,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.get("/{session_id}", response_model=FlashcardReviewSessionDetail)
async def get_review_session_endpoint(session_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            session = flashcard_review_service.get_review_session(db, session_id)
            items = [flashcard_review_service.get_active_review_card(item) for item in session.items]
            # Find next unreviewed item
            unreviewed = [it for it in items if not it.is_revealed]
            current_card = unreviewed[0] if unreviewed else (items[0] if items else None)
            return FlashcardReviewSessionDetail(
                session_id=session.id,
                study_set_id=session.study_set_id,
                document_id=session.document_id,
                session_mode=session.session_mode,
                status=session.status,
                requested_count=session.requested_count,
                actual_count=session.actual_count,
                reviewed_count=session.reviewed_count,
                correct_count=session.correct_count,
                started_at=session.started_at,
                completed_at=session.completed_at,
                current_card=current_card,
                revealed_card=None,
                items=items,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/{session_id}/items/{item_id}/reveal", response_model=RevealedReviewCard)
@router.post("/{session_id}/cards/{item_id}/reveal", response_model=RevealedReviewCard)
async def reveal_card_endpoint(session_id: str, item_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            return flashcard_review_service.reveal_session_card(db, session_id, item_id)
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/{session_id}/rate", response_model=RevealedReviewCard)
@router.post("/{session_id}/items/{item_id}/rate", response_model=RevealedReviewCard)
@router.post("/{session_id}/cards/{item_id}/rate", response_model=RevealedReviewCard)
async def rate_card_endpoint(
    session_id: str,
    request: Request,
    item_id: Optional[str] = None,
):
    body_data = {}
    try:
        body_data = await request.json()
    except Exception:
        pass

    target_item_id = item_id or body_data.get("item_id")
    rating = body_data.get("rating")

    if not target_item_id or not rating:
        raise HTTPException(422, "Both item_id (path or body) and rating (body) are required.")

    def _action():
        db = SessionLocal()
        try:
            return flashcard_review_service.rate_session_card(
                db, session_id=session_id, item_id=target_item_id, rating=rating
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)


@router.post("/{session_id}/complete", response_model=FlashcardReviewSessionSummary)
async def complete_session_endpoint(session_id: str, request: Request):
    def _action():
        db = SessionLocal()
        try:
            session = flashcard_review_service.complete_review_session(db, session_id)
            return FlashcardReviewSessionSummary(
                session_id=session.id,
                study_set_id=session.study_set_id,
                document_id=session.document_id,
                session_mode=session.session_mode,
                status=session.status,
                requested_count=session.requested_count,
                actual_count=session.actual_count,
                reviewed_count=session.reviewed_count,
                correct_count=session.correct_count,
                started_at=session.started_at,
                completed_at=session.completed_at,
            )
        finally:
            db.close()

    return await run_in_threadpool(_action)
