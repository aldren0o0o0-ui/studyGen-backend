"""
routers/review_sessions.py — Phase 4

Review session creation, listing, retrieval, and submission endpoints.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.database.models.study import ReviewSession
from app.database.session import SessionLocal
from app.database.types import normalize_json_value
from app.schemas.quizzes import QuestionSource
from app.schemas.study import (
    ActiveReviewQuestion,
    ActiveReviewSessionResponse,
    CreateReviewSessionRequest,
    ReviewAnswerFeedback,
    ReviewSessionFeedbackResponse,
    ReviewSessionListResponse,
    ReviewSessionSummary,
    ReviewSubmitRequest,
)
from app.services import review_session_service
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review-sessions"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# POST /review-sessions
# ---------------------------------------------------------------------------

@router.post("/review-sessions", response_model=ActiveReviewSessionResponse, status_code=201)
async def create_review_session(body: CreateReviewSessionRequest):
    """
    Create a new targeted review session.
    No Gemini API call is made.
    Returns an ActiveReviewSessionResponse that NEVER exposes correct answers or explanations.
    """
    def _create():
        db = SessionLocal()
        try:
            session = review_session_service.create_review_session(
                db,
                session_type=body.session_type,
                document_id=body.document_id,
                topic_label=body.topic_label,
                question_type=body.question_type,
                difficulty=body.difficulty,
                requested_count=body.question_count,
            )
            return _to_active_response(session)
        finally:
            db.close()

    return await run_in_threadpool(_create)


# ---------------------------------------------------------------------------
# GET /review-sessions
# ---------------------------------------------------------------------------

@router.get("/review-sessions", response_model=ReviewSessionListResponse)
async def list_review_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = review_session_service.list_review_sessions(
                db, page=page, page_size=page_size
            )
            summaries = [
                ReviewSessionSummary.model_validate(s) for s in items
            ]
            return summaries, total
        finally:
            db.close()

    summaries, total = await run_in_threadpool(_query)
    return ReviewSessionListResponse(items=summaries, page=page, page_size=page_size, total=total)


# ---------------------------------------------------------------------------
# GET /review-sessions/{session_id}
# ---------------------------------------------------------------------------

@router.get("/review-sessions/{session_id}")
async def get_review_session(session_id: str):
    """
    Retrieve review session details.
    If active: returns ActiveReviewSessionResponse (hides answers).
    If completed: returns ReviewSessionFeedbackResponse (full feedback).
    """
    def _query():
        db = SessionLocal()
        try:
            session = review_session_service.get_review_session_by_id(db, session_id)
            if not session:
                raise HTTPException(status_code=404, detail=f"ReviewSession '{session_id}' not found.")
            
            if session.status == "completed":
                return _to_feedback_response(session)
            return _to_active_response(session)
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# POST /review-sessions/{session_id}/submit
# ---------------------------------------------------------------------------

@router.post("/review-sessions/{session_id}/submit", response_model=ReviewSessionFeedbackResponse)
async def submit_review_session(session_id: str, body: ReviewSubmitRequest):
    """
    Submit answers for a review session.
    Scores answers server-side, updates spaced repetition, topic metrics, activity, and goals.
    Returns complete feedback with correct answers, explanations, and next review dates.
    """
    answers_dicts = [
        {"question_id": a.question_id, "selected_answer": a.selected_answer}
        for a in body.answers
    ]

    def _submit():
        db = SessionLocal()
        try:
            session = review_session_service.submit_review_session(
                db, session_id, answers_dicts
            )
            return _to_feedback_response(session)
        finally:
            db.close()

    return await run_in_threadpool(_submit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_active_response(session: ReviewSession) -> ActiveReviewSessionResponse:
    active_questions: list[ActiveReviewQuestion] = []
    for item in session.items:
        q = item.question
        if not q:
            continue
        options = normalize_json_value(q.options_json or [], location=f"questions/{q.id}/options_json")
        active_questions.append(
            ActiveReviewQuestion(
                question_id=q.id,
                question_type=getattr(q, "question_type", "multiple_choice"),
                question=q.question_text,
                options=options,
                difficulty=getattr(q, "difficulty", "medium"),
                topic=getattr(q, "topic_label", "General") or "General",
                position=item.position,
            )
        )

    return ActiveReviewSessionResponse(
        session_id=session.id,
        session_type=session.session_type,
        document_id=session.document_id,
        topic_label=session.topic_label,
        status=session.status,
        question_count=session.question_count,
        completed_count=session.completed_count,
        started_at=session.started_at,
        questions=active_questions,
    )


def _to_feedback_response(session: ReviewSession) -> ReviewSessionFeedbackResponse:
    feedback_answers: list[ReviewAnswerFeedback] = []
    for item in session.items:
        q = item.question
        if not q:
            continue

        sources = []
        try:
            raw_refs = normalize_json_value(getattr(q, "source_references_json", []) or [], location=f"questions/{q.id}/source_references_json")
            for ref in raw_refs:
                sources.append(
                    QuestionSource(
                        chunk_index=ref.get("chunk_index", 0),
                        page_number=ref.get("page_number", -1),
                        filename=ref.get("filename", ""),
                        excerpt=ref.get("excerpt", ""),
                    )
                )
        except Exception:
            pass

        accepted = normalize_json_value(getattr(q, "accepted_answers_json", []) or [], location=f"questions/{q.id}/accepted_answers_json")

        feedback_answers.append(
            ReviewAnswerFeedback(
                question_id=q.id,
                question_text=q.question_text,
                question_type=getattr(q, "question_type", "multiple_choice"),
                selected_answer=item.selected_answer or "",
                correct_answer=q.correct_answer,
                accepted_answers=accepted,
                is_correct=bool(item.is_correct),
                explanation=q.explanation,
                difficulty=getattr(q, "difficulty", "medium"),
                topic=getattr(q, "topic_label", None),
                sources=sources,
            )
        )

    pct = round((session.correct_count / session.question_count) * 100, 2) if session.question_count > 0 else 0.0

    return ReviewSessionFeedbackResponse(
        session_id=session.id,
        session_type=session.session_type,
        status=session.status,
        question_count=session.question_count,
        correct_count=session.correct_count,
        completed_count=session.completed_count,
        percentage=pct,
        started_at=session.started_at,
        completed_at=session.completed_at,
        answers=feedback_answers,
    )
