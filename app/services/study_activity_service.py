"""
services/study_activity_service.py — Phase 4

Append-only event log for study activities.

Activity Types:
  quiz_completed    — a standard or focused quiz attempt was submitted
  review_completed  — a review session was completed
  document_uploaded — a new study document was indexed
  retry_completed   — an incorrect-answer retry quiz was completed
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database.models.attempt import QuizAttempt
from app.database.models.study import ReviewSession, StudyActivity
from app.database.types import ensure_utc, utc_now

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return utc_now()


def log_quiz_completed(db: Session, attempt: QuizAttempt) -> StudyActivity:
    now = _utcnow()
    quiz = attempt.quiz
    is_retry = getattr(quiz, "quiz_mode", "generated") == "retry_incorrect"
    act_type = "retry_completed" if is_retry else "quiz_completed"

    duration = None
    if attempt.started_at and attempt.completed_at:
        duration = int((ensure_utc(attempt.completed_at) - ensure_utc(attempt.started_at)).total_seconds())

    act = StudyActivity(
        id=str(uuid4()),
        activity_type=act_type,
        related_document_id=quiz.document_id if quiz else None,
        related_quiz_id=attempt.quiz_id,
        related_attempt_id=attempt.id,
        item_count=attempt.total_questions,
        duration_seconds=duration,
        occurred_at=attempt.completed_at or now,
        created_at=now,
    )
    db.add(act)
    db.flush()
    return act


def log_review_completed(db: Session, session: ReviewSession) -> StudyActivity:
    now = _utcnow()
    duration = None
    if session.started_at and session.completed_at:
        duration = int((ensure_utc(session.completed_at) - ensure_utc(session.started_at)).total_seconds())

    act = StudyActivity(
        id=str(uuid4()),
        activity_type="review_completed",
        related_document_id=session.document_id,
        related_review_session_id=session.id,
        item_count=session.completed_count,
        duration_seconds=duration,
        occurred_at=session.completed_at or now,
        created_at=now,
    )
    db.add(act)
    db.flush()
    return act


def log_document_uploaded(db: Session, document_id: str) -> StudyActivity:
    now = _utcnow()
    act = StudyActivity(
        id=str(uuid4()),
        activity_type="document_uploaded",
        related_document_id=document_id,
        item_count=1,
        occurred_at=now,
        created_at=now,
    )
    db.add(act)
    db.flush()
    return act


def log_chat_activity(
    db: Session,
    activity_type: str,
    session_id: str,
    *,
    document_id: str | None = None,
    item_count: int = 0,
) -> StudyActivity:
    if activity_type not in {"chat_started", "chat_message_completed", "chat_session_archived"}:
        raise ValueError("Unsupported chat activity type.")
    now = _utcnow()
    activity = StudyActivity(
        id=str(uuid4()),
        activity_type=activity_type,
        related_document_id=document_id,
        related_chat_session_id=session_id,
        item_count=item_count,
        occurred_at=now,
        created_at=now,
    )
    db.add(activity)
    db.flush()
    return activity


def log_flashcard_reviewed(
    db: Session, study_set_id: str, flashcard_id: str, was_correct: bool
) -> StudyActivity:
    now = _utcnow()
    act = StudyActivity(
        id=str(uuid4()),
        activity_type="flashcard_reviewed",
        related_document_id=None,
        item_count=1,
        occurred_at=now,
        created_at=now,
    )
    db.add(act)
    db.flush()
    return act


def list_activities(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[List[StudyActivity], int]:
    stmt = select(StudyActivity)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    stmt = stmt.order_by(StudyActivity.occurred_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total
