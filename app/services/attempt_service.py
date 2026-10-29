"""
services/attempt_service.py — Phase 3
Handles quiz attempt creation, answer validation, and score calculation.

Phase 3 additions:
  - Identification question scoring with normalization.
  - True/false scoring (case-insensitive).
  - create_retry_quiz: builds a new quiz from incorrect answers, no Gemini call.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.models.attempt import AttemptAnswer, QuizAttempt
from app.database.models.quiz import Question, Quiz
from app.database.types import normalize_json_value
from app.services.question_validation import normalize_identification_answer

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _is_correct_answer(question: Question, selected: str) -> bool:
    """
    Type-aware answer correctness check.
    - multiple_choice: exact string match against correct_answer.
    - true_false: case-insensitive match (True/False).
    - identification: normalized match against accepted_answers list.
    """
    qtype = getattr(question, "question_type", "multiple_choice")

    if qtype == "identification":
        accepted_raw = normalize_json_value(getattr(question, "accepted_answers_json", []) or [], location=f"questions/{question.id}/accepted_answers_json")
        normalized_selected = normalize_identification_answer(selected)
        return normalized_selected in {
            normalize_identification_answer(a) for a in accepted_raw
        }

    if qtype == "true_false":
        return selected.strip().lower() == question.correct_answer.strip().lower()

    # Default: multiple_choice — exact match
    return selected.strip() == question.correct_answer.strip()


def _validate_answer_choice(question: Question, selected: str) -> None:
    """
    For multiple_choice and true_false, enforce that the selected answer
    is one of the defined options. Identification answers are free-text.
    """
    qtype = getattr(question, "question_type", "multiple_choice")
    if qtype == "identification":
        return  # free-text, no validation needed

    valid_options: List[str] = normalize_json_value(question.options_json or [], location=f"questions/{question.id}/options_json")
    if valid_options and selected not in valid_options:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid answer '{selected}' for question '{question.id}'. "
                f"Valid options: {valid_options}"
            ),
        )


# ---------------------------------------------------------------------------
# submit_attempt
# ---------------------------------------------------------------------------

def submit_attempt(
    db: Session,
    quiz_id: str,
    answers: List[dict],
    started_at: Optional[datetime] = None,
) -> QuizAttempt:
    """
    Validate answers and persist the attempt.

    Supports multiple_choice, true_false, and identification question types.
    """
    stmt = (
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.questions))
    )
    quiz = db.execute(stmt).scalar_one_or_none()
    if not quiz:
        raise HTTPException(status_code=404, detail=f"Quiz '{quiz_id}' not found.")

    if not answers:
        raise HTTPException(status_code=400, detail="No answers submitted.")

    question_map: dict[str, Question] = {q.id: q for q in quiz.questions}

    # Validate structure
    seen_question_ids: set[str] = set()
    for ans in answers:
        qid = ans.get("question_id", "")
        if qid in seen_question_ids:
            raise HTTPException(400, f"Duplicate answer for question_id '{qid}'.")
        seen_question_ids.add(qid)

        if qid not in question_map:
            raise HTTPException(
                400,
                f"question_id '{qid}' does not belong to quiz '{quiz_id}'.",
            )
        _validate_answer_choice(question_map[qid], ans.get("selected_answer", ""))

    # Score
    score = 0
    total = len(quiz.questions)
    attempt_id = str(uuid4())

    attempt = QuizAttempt(
        id=attempt_id,
        quiz_id=quiz_id,
        score=0,
        total_questions=total,
        percentage=0.0,
        started_at=started_at,
        completed_at=_utcnow(),
        created_at=_utcnow(),
    )
    db.add(attempt)
    db.flush()

    for ans in answers:
        qid = ans["question_id"]
        selected = ans["selected_answer"]
        question = question_map[qid]
        correct = _is_correct_answer(question, selected)
        if correct:
            score += 1

        db.add(AttemptAnswer(
            id=str(uuid4()),
            attempt_id=attempt_id,
            question_id=qid,
            selected_answer=selected,
            is_correct=correct,
            answered_at=_utcnow(),
        ))

    attempt.score = score
    attempt.percentage = round((score / total) * 100, 2) if total > 0 else 0.0
    db.commit()
    db.refresh(attempt)
    logger.info("Attempt id=%s quiz=%s score=%d/%d", attempt_id, quiz_id, score, total)
    return attempt


# ---------------------------------------------------------------------------
# get_attempt_by_id
# ---------------------------------------------------------------------------

def get_attempt_by_id(db: Session, attempt_id: str) -> Optional[QuizAttempt]:
    stmt = (
        select(QuizAttempt)
        .where(QuizAttempt.id == attempt_id)
        .options(
            selectinload(QuizAttempt.answers).selectinload(AttemptAnswer.question)
        )
    )
    return db.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# list_attempts
# ---------------------------------------------------------------------------

def list_attempts(
    db: Session,
    quiz_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[List[QuizAttempt], int]:
    from sqlalchemy import func
    stmt = select(QuizAttempt)
    if quiz_id:
        stmt = stmt.where(QuizAttempt.quiz_id == quiz_id)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(QuizAttempt.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total


# ---------------------------------------------------------------------------
# create_retry_quiz — Phase 3
# ---------------------------------------------------------------------------

def create_retry_quiz(db: Session, attempt_id: str) -> Quiz:
    """
    Build a new quiz from incorrect answers in the given attempt.
    Copies question rows (new UUIDs) to avoid cross-quiz contamination.
    No Gemini call is made.

    Raises HTTPException 404 if attempt not found.
    Raises HTTPException 400 if all questions were correct (nothing to retry).
    """
    stmt = (
        select(QuizAttempt)
        .where(QuizAttempt.id == attempt_id)
        .options(
            selectinload(QuizAttempt.answers).selectinload(AttemptAnswer.question),
            selectinload(QuizAttempt.quiz),
        )
    )
    attempt = db.execute(stmt).scalar_one_or_none()
    if not attempt:
        raise HTTPException(404, f"Attempt '{attempt_id}' not found.")

    incorrect_answers = [a for a in attempt.answers if not a.is_correct]
    if not incorrect_answers:
        raise HTTPException(409, "All questions were answered correctly - nothing to retry.") #  — nothing to retry.")

    source_quiz = attempt.quiz
    new_quiz_id = str(uuid4())

    db_quiz = Quiz(
        id=new_quiz_id,
        document_id=source_quiz.document_id,
        title=f"Retry: {source_quiz.title}",
        difficulty=source_quiz.difficulty,
        question_count=len(incorrect_answers),
        question_types_json=source_quiz.question_types_json,
        chunks_used=source_quiz.chunks_used,
        coverage_mode=getattr(source_quiz, "coverage_mode", "balanced"),
        configuration_json=getattr(source_quiz, "configuration_json", "{}"),
        prompt_version=getattr(source_quiz, "prompt_version", "legacy_phase2"),
        model_name=getattr(source_quiz, "model_name", "unknown"),
        quiz_mode="retry_incorrect",
        source_quiz_id=source_quiz.id,
        source_attempt_id=attempt_id,
        created_at=_utcnow(),
    )
    db.add(db_quiz)

    for i, ans in enumerate(incorrect_answers):
        orig = ans.question
        db.add(Question(
            id=str(uuid4()),
            quiz_id=new_quiz_id,
            position=i,
            question_text=orig.question_text,
            options_json=orig.options_json,
            correct_answer=orig.correct_answer,
            accepted_answers_json=getattr(orig, "accepted_answers_json", "[]"),
            explanation=orig.explanation,
            source_chunk_indexes_json=orig.source_chunk_indexes_json,
            source_references_json=getattr(orig, "source_references_json", "[]"),
            question_type=getattr(orig, "question_type", "multiple_choice"),
            topic_label=getattr(orig, "topic_label", None),
            difficulty=getattr(orig, "difficulty", "medium"),
            created_at=_utcnow(),
        ))

    db.commit()
    db.refresh(db_quiz)
    # Load questions
    stmt2 = (
        select(Quiz)
        .where(Quiz.id == new_quiz_id)
        .options(selectinload(Quiz.questions))
    )
    return db.execute(stmt2).scalar_one()
