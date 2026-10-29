"""
services/quiz_history_service.py
Persists and retrieves generated quizzes and questions.
All functions are synchronous — call via run_in_threadpool.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.models.quiz import Quiz, Question
from app.schemas.quizzes import QuizResponse

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def persist_quiz(db: Session, quiz_response: QuizResponse) -> Quiz:
    """
    Save a validated QuizResponse to the database in a single transaction.
    Returns the persisted Quiz ORM object.
    """
    quiz_id = str(uuid4())
    quiz = Quiz(
        id=quiz_id,
        document_id=quiz_response.document_id,
        title=quiz_response.quiz_title,
        difficulty=quiz_response.difficulty,
        question_count=len(quiz_response.questions),
        question_types_json=["multiple_choice"],
        chunks_used=quiz_response.chunks_used,
        created_at=_utcnow(),
    )
    db.add(quiz)

    for position, q in enumerate(quiz_response.questions):
        question = Question(
            id=str(uuid4()),
            quiz_id=quiz_id,
            position=position,
            question_text=q.question,
            options_json=q.options,
            correct_answer=q.correct_answer,
            explanation=q.explanation,
            source_chunk_indexes_json=q.source_chunk_indexes,
            created_at=_utcnow(),
        )
        db.add(question)

    db.commit()
    db.refresh(quiz)
    logger.info("Persisted quiz id=%s questions=%d", quiz_id, len(quiz_response.questions))
    return quiz


def get_quiz_by_id(db: Session, quiz_id: str) -> Optional[Quiz]:
    """Return a quiz with its questions eagerly loaded, or None."""
    stmt = (
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(
            selectinload(Quiz.questions),
            selectinload(Quiz.source_documents),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def list_quizzes(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    document_id: Optional[str] = None,
    difficulty: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> tuple[List[Quiz], int]:
    """Return paginated quizzes, newest first."""
    from sqlalchemy import func
    stmt = select(Quiz).options(selectinload(Quiz.source_documents))
    if document_id:
        stmt = stmt.where(Quiz.document_id == document_id)
    if difficulty:
        stmt = stmt.where(Quiz.difficulty == difficulty)
    if folder_id:
        stmt = stmt.where(Quiz.folder_id == folder_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    stmt = stmt.order_by(Quiz.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total
