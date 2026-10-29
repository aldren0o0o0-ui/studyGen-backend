"""
routers/attempts.py — Phase 3
Submit and retrieve quiz attempts.
Phase 3: identification/true_false scoring, retry-quiz endpoint, source citations.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.database.models.attempt import AttemptAnswer
from app.database.session import SessionLocal
from app.database.types import normalize_json_value
from app.schemas.attempts import (
    AttemptAnswerDetail, AttemptListResponse,
    AttemptRequest, AttemptResponse, AttemptSummary,
)
from app.schemas.quizzes import ActiveQuestion, ActiveQuizResponse, QuestionSource
from app.services import attempt_service
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["attempts"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# POST /quizzes/{quiz_id}/attempts
# ---------------------------------------------------------------------------

@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptResponse, status_code=201)
async def submit_attempt(quiz_id: str, body: AttemptRequest, request: Request):
    answers_dicts = [
        {"question_id": a.question_id, "selected_answer": a.selected_answer}
        for a in body.answers
    ]

    def _submit():
        db = SessionLocal()
        try:
            attempt = attempt_service.submit_attempt(
                db, quiz_id, answers_dicts, started_at=body.started_at
            )
            # --- Phase 4 Event Hooks ---
            try:
                from app.services import (
                    review_item_service,
                    topic_performance_service,
                    study_activity_service,
                    study_goal_service,
                )
                review_item_service.process_attempt(db, attempt)
                topic_performance_service.update_from_attempt(db, attempt)
                study_activity_service.log_quiz_completed(db, attempt)
                study_goal_service.update_from_attempt(db, attempt)
                db.commit()
            except Exception as hook_err:
                logger.error("Error in Phase 4 attempt post-processing hooks: %s", hook_err)

            return _attempt_to_response(attempt)
        finally:
            db.close()

    return await run_in_threadpool(_submit)


# ---------------------------------------------------------------------------
# POST /attempts/{attempt_id}/retry-quiz
# ---------------------------------------------------------------------------

@router.post("/attempts/{attempt_id}/retry-quiz", response_model=ActiveQuizResponse, status_code=201)
async def retry_incorrect_questions(attempt_id: str):
    """
    Create a new quiz containing only incorrectly answered questions.
    No Gemini API call is made.
    Returns an ActiveQuizResponse (safe — hides correct answers).
    """
    def _create():
        db = SessionLocal()
        try:
            return attempt_service.create_retry_quiz(db, attempt_id)
        finally:
            db.close()

    retry_quiz = await run_in_threadpool(_create)

    active_questions = [
        ActiveQuestion(
            question_id=q.id,
            question_type=getattr(q, "question_type", "multiple_choice"),
            question=q.question_text,
            options=normalize_json_value(q.options_json or [], location=f"questions/{q.id}/options_json"),
            difficulty=getattr(q, "difficulty", "medium"),
            topic=getattr(q, "topic_label", "General") or "General",
        )
        for q in retry_quiz.questions
    ]

    src_quiz = retry_quiz  # same object, use quiz fields
    return ActiveQuizResponse(
        quiz_id=retry_quiz.id,
        document_id=retry_quiz.document_id,
        quiz_title=retry_quiz.title,
        difficulty=retry_quiz.difficulty,
        questions=active_questions,
        source_file="",
        chunks_used=retry_quiz.chunks_used,
        prompt_version=getattr(retry_quiz, "prompt_version", "legacy_phase2"),
    )


# ---------------------------------------------------------------------------
# GET /attempts
# ---------------------------------------------------------------------------

@router.get("/attempts", response_model=AttemptListResponse)
async def list_attempts(
    quiz_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = attempt_service.list_attempts(
                db, quiz_id=quiz_id, page=page, page_size=page_size
            )
            return [
                AttemptSummary(
                    attempt_id=a.id,
                    quiz_id=a.quiz_id,
                    score=a.score,
                    total_questions=a.total_questions,
                    percentage=a.percentage,
                    created_at=a.created_at,
                )
                for a in items
            ], total
        finally:
            db.close()

    summaries, total = await run_in_threadpool(_query)
    return AttemptListResponse(items=summaries, page=page, page_size=page_size, total=total)


# ---------------------------------------------------------------------------
# GET /attempts/{attempt_id}
# ---------------------------------------------------------------------------

@router.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(attempt_id: str):
    def _query():
        db = SessionLocal()
        try:
            attempt = attempt_service.get_attempt_by_id(db, attempt_id)
            if not attempt:
                raise HTTPException(404, f"Attempt '{attempt_id}' not found.")
            return _attempt_to_response(attempt)
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _attempt_to_response(attempt) -> AttemptResponse:
    answer_details = []
    for ans in attempt.answers:
        q = ans.question
        sources = []
        try:
            raw_refs = normalize_json_value(getattr(q, "source_references_json", []) or [], location=f"questions/{q.id}/source_references_json")
            for ref in raw_refs:
                sources.append(QuestionSource(
                    chunk_index=ref.get("chunk_index", 0),
                    page_number=ref.get("page_number", -1),
                    filename=ref.get("filename", ""),
                    excerpt=ref.get("excerpt", ""),
                ))
        except Exception:
            pass

        answer_details.append(
            AttemptAnswerDetail(
                question_id=q.id,
                question_text=q.question_text,
                question_type=getattr(q, "question_type", "multiple_choice"),
                selected_answer=ans.selected_answer,
                correct_answer=q.correct_answer,
                accepted_answers=normalize_json_value(
                    getattr(q, "accepted_answers_json", []) or [], location=f"questions/{q.id}/accepted_answers_json"
                ),
                is_correct=ans.is_correct,
                explanation=q.explanation,
                difficulty=getattr(q, "difficulty", "medium"),
                topic=getattr(q, "topic_label", None),
                sources=sources,
            )
        )
    return AttemptResponse(
        attempt_id=attempt.id,
        quiz_id=attempt.quiz_id,
        score=attempt.score,
        total_questions=attempt.total_questions,
        percentage=attempt.percentage,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        created_at=attempt.created_at,
        answers=answer_details,
    )
