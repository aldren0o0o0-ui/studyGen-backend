"""
services/review_item_service.py — Phase 4

Creates and updates ReviewItem rows when quiz attempts are submitted.
One row per unique question — no duplicates.

On incorrect answer:
  - Create new review item (DUE, next_review_at=now, interval=0) OR
  - Update existing: increase incorrect_count, reset interval, set DUE

On correct answer: no review item created (question was already known)

Used during review session submission to update intervals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.models.attempt import AttemptAnswer, QuizAttempt
from app.database.models.quiz import Question
from app.database.models.study import ReviewItem
from app.services.spaced_repetition_service import (
    calculate_initial_schedule,
    calculate_next_review,
    calculate_priority_score,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# process_attempt
# ---------------------------------------------------------------------------

def process_attempt(db: Session, attempt: QuizAttempt) -> List[ReviewItem]:
    """
    After a quiz attempt is scored, create or update ReviewItem rows
    for each incorrectly answered question.

    Returns the list of affected ReviewItem rows.
    """
    # Load answers with question + quiz for denorm fields
    stmt = (
        select(QuizAttempt)
        .where(QuizAttempt.id == attempt.id)
        .options(
            selectinload(QuizAttempt.answers).selectinload(AttemptAnswer.question)
            .selectinload(Question.quiz)
        )
    )
    loaded = db.execute(stmt).scalar_one_or_none()
    if not loaded:
        return []

    now = _utcnow()
    affected: list[ReviewItem] = []

    for ans in loaded.answers:
        if ans.is_correct:
            continue  # correct answers don't need review

        q = ans.question
        if not q:
            continue

        existing = _get_review_item_for_question(db, q.id)
        doc_id = q.quiz.document_id if q.quiz else None

        if existing is None:
            # Create fresh review item
            next_dt, interval = calculate_initial_schedule(now=now)
            priority = calculate_priority_score(
                incorrect_count=1,
                consecutive_correct=0,
                days_overdue=0,
            )
            item = ReviewItem(
                id=str(uuid4()),
                question_id=q.id,
                document_id=doc_id,
                source_attempt_id=attempt.id,
                topic_label=getattr(q, "topic_label", None),
                question_type=getattr(q, "question_type", "multiple_choice"),
                difficulty=getattr(q, "difficulty", "medium"),
                review_status="DUE",
                priority_score=priority,
                incorrect_count=1,
                correct_review_count=0,
                consecutive_correct=0,
                next_review_at=next_dt,
                interval_days=interval,
                created_at=now,
                updated_at=now,
            )
            db.add(item)
            affected.append(item)
            logger.debug("Created review item for question %s", q.id)
        else:
            # Update existing: increase incorrect count, reset interval
            existing.incorrect_count += 1
            existing.consecutive_correct = 0  # reset streak
            existing.interval_days = 0
            existing.next_review_at = now
            existing.review_status = "DUE"
            existing.updated_at = now
            days_overdue = max(0.0, (now - existing.next_review_at).total_seconds() / 86400)
            existing.priority_score = calculate_priority_score(
                incorrect_count=existing.incorrect_count,
                consecutive_correct=0,
                days_overdue=days_overdue,
            )
            affected.append(existing)
            logger.debug("Updated review item for question %s", q.id)

    db.flush()
    return affected


# ---------------------------------------------------------------------------
# update_after_review_answer
# ---------------------------------------------------------------------------

def update_after_review_answer(
    db: Session,
    *,
    question_id: str,
    is_correct: bool,
    now: Optional[datetime] = None,
) -> Optional[ReviewItem]:
    """
    Update a ReviewItem's spaced-repetition state after a review session answer.
    Returns the updated item, or None if not found.
    """
    if now is None:
        now = _utcnow()

    item = _get_review_item_for_question(db, question_id)
    if item is None:
        return None

    next_dt, new_interval, new_consecutive, new_status = calculate_next_review(
        consecutive_correct=item.consecutive_correct,
        current_interval_days=item.interval_days,
        answered_correctly=is_correct,
        now=now,
    )

    item.last_reviewed_at = now
    item.next_review_at = next_dt
    item.interval_days = new_interval
    item.consecutive_correct = new_consecutive
    item.review_status = new_status
    item.updated_at = now

    if is_correct:
        item.correct_review_count += 1

    # Recalculate priority
    days_overdue = max(0.0, (now - next_dt).total_seconds() / 86400)
    item.priority_score = calculate_priority_score(
        incorrect_count=item.incorrect_count,
        consecutive_correct=new_consecutive,
        days_overdue=days_overdue,
    )

    db.flush()
    return item


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _get_review_item_for_question(db: Session, question_id: str) -> Optional[ReviewItem]:
    stmt = select(ReviewItem).where(ReviewItem.question_id == question_id)
    return db.execute(stmt).scalar_one_or_none()


def get_due_review_items(
    db: Session,
    *,
    document_id: Optional[str] = None,
    topic_label: Optional[str] = None,
    question_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    limit: int = 50,
    now: Optional[datetime] = None,
) -> List[ReviewItem]:
    """Items where next_review_at <= now, ordered by priority desc."""
    if now is None:
        now = _utcnow()
    stmt = select(ReviewItem).where(
        ReviewItem.next_review_at <= now,
        ReviewItem.review_status.in_(["DUE", "UPCOMING"]),
    )
    if document_id:
        stmt = stmt.where(ReviewItem.document_id == document_id)
    if topic_label:
        stmt = stmt.where(ReviewItem.topic_label == topic_label)
    if question_type:
        stmt = stmt.where(ReviewItem.question_type == question_type)
    if difficulty:
        stmt = stmt.where(ReviewItem.difficulty == difficulty)
    stmt = stmt.order_by(ReviewItem.priority_score.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_incorrect_review_items(
    db: Session,
    *,
    document_id: Optional[str] = None,
    topic_label: Optional[str] = None,
    question_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    limit: int = 50,
) -> List[ReviewItem]:
    """All non-mastered review items, ordered by priority desc."""
    stmt = select(ReviewItem).where(
        ReviewItem.review_status.in_(["DUE", "UPCOMING"]),
    )
    if document_id:
        stmt = stmt.where(ReviewItem.document_id == document_id)
    if topic_label:
        stmt = stmt.where(ReviewItem.topic_label == topic_label)
    if question_type:
        stmt = stmt.where(ReviewItem.question_type == question_type)
    if difficulty:
        stmt = stmt.where(ReviewItem.difficulty == difficulty)
    stmt = stmt.order_by(ReviewItem.priority_score.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def count_due_items(db: Session, now: Optional[datetime] = None) -> int:
    """Count of review items due now."""
    if now is None:
        now = _utcnow()
    from sqlalchemy import func
    stmt = select(func.count(ReviewItem.id)).where(
        ReviewItem.next_review_at <= now,
        ReviewItem.review_status.in_(["DUE", "UPCOMING"]),
    )
    return db.execute(stmt).scalar_one() or 0


def rebuild_review_items(db: Session, *, apply: bool = False) -> dict:
    """
    Idempotent rebuild of ReviewItem rows from attempt data.
    Creates missing items for incorrectly answered questions.
    Does NOT reset existing spaced-repetition state.
    """
    from app.database.models.attempt import AttemptAnswer
    stmt = (
        select(AttemptAnswer)
        .where(AttemptAnswer.is_correct == False)  # noqa: E712
        .options(
            selectinload(AttemptAnswer.question).selectinload(Question.quiz)
        )
    )
    incorrect_answers = list(db.execute(stmt).scalars().all())

    # Unique question_ids already having a review_item
    existing_stmt = select(ReviewItem.question_id)
    existing_ids = set(db.execute(existing_stmt).scalars().all())

    missing = [a for a in incorrect_answers if a.question_id not in existing_ids]

    report = {
        "incorrect_answers_found": len(incorrect_answers),
        "existing_review_items": len(existing_ids),
        "items_to_create": len(missing),
        "apply": apply,
    }

    if not apply:
        return report

    now = _utcnow()
    created = 0
    for ans in missing:
        q = ans.question
        if not q:
            continue
        if q.id in existing_ids:
            continue
        existing_ids.add(q.id)
        next_dt, interval = calculate_initial_schedule(now=now)
        item = ReviewItem(
            id=str(uuid4()),
            question_id=q.id,
            document_id=q.quiz.document_id if q.quiz else None,
            source_attempt_id=ans.attempt_id,
            topic_label=getattr(q, "topic_label", None),
            question_type=getattr(q, "question_type", "multiple_choice"),
            difficulty=getattr(q, "difficulty", "medium"),
            review_status="DUE",
            priority_score=calculate_priority_score(1, 0, 0),
            incorrect_count=1,
            correct_review_count=0,
            consecutive_correct=0,
            next_review_at=next_dt,
            interval_days=interval,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        created += 1

    if apply:
        db.commit()

    report["items_created"] = created
    return report
