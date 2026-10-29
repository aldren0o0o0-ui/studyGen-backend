"""
services/topic_performance_service.py — Phase 4

Manages the topic_performance aggregate table.

Mastery formula (deterministic):
  mastery_score = clamp(
    (accuracy_percentage * 0.70)
    + (recent_accuracy * 0.20)
    + (review_bonus * 0.10)
  , 0, 100)

  recent_accuracy = accuracy over last 5 attempts on this topic
                    (falls back to overall accuracy if < 5 attempts)
  review_bonus    = min(correct_review_count * 5, 10)

Topic classification:
  insufficient_data:  attempted_count < MIN_ATTEMPTS (default 2)
  weak:               accuracy < WEAK_THRESHOLD (default 50 %)
  developing:         WEAK_THRESHOLD <= accuracy < STRONG_THRESHOLD (default 80 %)
  strong:             accuracy >= STRONG_THRESHOLD
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database.models.attempt import AttemptAnswer, QuizAttempt
from app.database.models.quiz import Question, Quiz
from app.database.models.study import TopicPerformance

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable thresholds (can be moved to Settings later)
# ---------------------------------------------------------------------------
MIN_ATTEMPTS: int = 2
WEAK_THRESHOLD: float = 50.0
STRONG_THRESHOLD: float = 80.0
MASTERY_REVIEW_WEIGHT: float = 0.10
MASTERY_RECENT_WEIGHT: float = 0.20
MASTERY_ACCURACY_WEIGHT: float = 0.70
RECENT_WINDOW: int = 5  # last N question attempts considered "recent"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_topic(topic: str) -> str:
    """Lowercase, strip, collapse whitespace for dedup key."""
    return " ".join(topic.strip().lower().split())


def _classify_state(attempted_count: int, accuracy: float) -> str:
    if attempted_count < MIN_ATTEMPTS:
        return "insufficient_data"
    if accuracy < WEAK_THRESHOLD:
        return "weak"
    if accuracy < STRONG_THRESHOLD:
        return "developing"
    return "strong"


def _calculate_mastery_score(
    accuracy_percentage: float,
    recent_accuracy: float,
    correct_review_count: int,
) -> float:
    review_bonus = min(correct_review_count * 5.0, 10.0)
    raw = (
        accuracy_percentage * MASTERY_ACCURACY_WEIGHT
        + recent_accuracy * MASTERY_RECENT_WEIGHT
        + review_bonus
    )
    return max(0.0, min(100.0, raw))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_create_topic_performance(
    db: Session,
    *,
    topic_label: str,
    document_id: Optional[str],
) -> TopicPerformance:
    """Get existing TopicPerformance row or create a fresh one."""
    norm = _normalize_topic(topic_label)
    stmt = select(TopicPerformance).where(
        TopicPerformance.normalized_topic == norm,
        TopicPerformance.document_id == document_id,
    )
    tp = db.execute(stmt).scalar_one_or_none()
    if tp is None:
        tp = TopicPerformance(
            id=str(uuid4()),
            topic_label=topic_label,
            normalized_topic=norm,
            document_id=document_id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(tp)
        db.flush()
    return tp


def update_from_attempt(db: Session, attempt: QuizAttempt) -> None:
    """
    Update TopicPerformance rows after an attempt is scored.
    Groups answers by (topic_label, document_id) and updates each aggregate.
    Called inside the attempt submission transaction.
    """
    # Load answers with question + quiz for document_id
    from sqlalchemy.orm import selectinload
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
        return

    # Group answers by (topic_label, document_id)
    groups: dict[tuple[str, Optional[str]], list[AttemptAnswer]] = {}
    for ans in loaded.answers:
        q = ans.question
        if not q:
            continue
        topic = getattr(q, "topic_label", None) or "General"
        doc_id = q.quiz.document_id if q.quiz else None
        key = (topic, doc_id)
        groups.setdefault(key, []).append(ans)

    now = _utcnow()
    for (topic_label, doc_id), answers in groups.items():
        tp = get_or_create_topic_performance(db, topic_label=topic_label, document_id=doc_id)
        for ans in answers:
            tp.attempted_count += 1
            if ans.is_correct:
                tp.correct_count += 1
                tp.last_correct_at = now
            else:
                tp.incorrect_count += 1
                tp.last_incorrect_at = now
        tp.last_attempted_at = now

        # Recalculate accuracy
        if tp.attempted_count > 0:
            tp.accuracy_percentage = round(
                (tp.correct_count / tp.attempted_count) * 100, 2
            )
        else:
            tp.accuracy_percentage = 0.0

        # Recent accuracy (last RECENT_WINDOW answers for this topic in this doc)
        recent_accuracy = _fetch_recent_accuracy(db, topic_label=topic_label, document_id=doc_id)

        # Review bonus: fetch correct_review_count from review_items for this topic
        review_bonus_count = _fetch_review_correct_count(db, topic_label=topic_label, document_id=doc_id)

        tp.mastery_score = _calculate_mastery_score(
            tp.accuracy_percentage,
            recent_accuracy,
            review_bonus_count,
        )
        tp.mastery_state = _classify_state(tp.attempted_count, tp.accuracy_percentage)
        tp.updated_at = now
    db.flush()


def _fetch_recent_accuracy(
    db: Session,
    *,
    topic_label: str,
    document_id: Optional[str],
) -> float:
    """
    Calculate accuracy for the last RECENT_WINDOW answers on this topic+doc.
    Returns accuracy as a percentage (0–100).
    """
    norm = _normalize_topic(topic_label)
    # Join: attempt_answers → questions (topic_label) → quizzes (document_id)
    stmt = (
        select(AttemptAnswer.is_correct)
        .join(AttemptAnswer.question)
        .join(Question.quiz)
        .where(
            func.lower(func.trim(Question.topic_label)) == norm,
        )
    )
    if document_id:
        stmt = stmt.where(Quiz.document_id == document_id)
    stmt = stmt.order_by(AttemptAnswer.answered_at.desc()).limit(RECENT_WINDOW)
    rows = list(db.execute(stmt).scalars().all())
    if not rows:
        return 0.0
    return round(sum(1 for r in rows if r) / len(rows) * 100, 2)


def _fetch_review_correct_count(
    db: Session,
    *,
    topic_label: str,
    document_id: Optional[str],
) -> int:
    """Sum of correct_review_count for review items on this topic+doc."""
    from app.database.models.study import ReviewItem
    norm = _normalize_topic(topic_label)
    stmt = select(func.coalesce(func.sum(ReviewItem.correct_review_count), 0)).where(
        func.lower(func.trim(ReviewItem.topic_label)) == norm,
    )
    if document_id:
        stmt = stmt.where(ReviewItem.document_id == document_id)
    result = db.execute(stmt).scalar_one()
    return int(result or 0)


def list_topic_performance(
    db: Session,
    *,
    document_id: Optional[str] = None,
    mastery_state: Optional[str] = None,
    sort_by: str = "weakest",  # weakest | strongest | recent
    limit: int = 100,
) -> List[TopicPerformance]:
    stmt = select(TopicPerformance)
    if document_id:
        stmt = stmt.where(TopicPerformance.document_id == document_id)
    if mastery_state:
        stmt = stmt.where(TopicPerformance.mastery_state == mastery_state)

    if sort_by == "strongest":
        stmt = stmt.order_by(TopicPerformance.accuracy_percentage.desc())
    elif sort_by == "recent":
        stmt = stmt.order_by(TopicPerformance.last_attempted_at.desc())
    else:  # weakest (default)
        stmt = stmt.order_by(TopicPerformance.accuracy_percentage.asc())

    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_topic_performance_by_id(db: Session, tp_id: str) -> Optional[TopicPerformance]:
    return db.get(TopicPerformance, tp_id)


def rebuild_topic_performance(db: Session, *, apply: bool = False) -> dict:
    """
    Idempotent rebuild of all TopicPerformance rows from attempt data.
    Used by the rebuild_study_metrics command.

    Returns a report dict with counts.
    If apply=False, performs a dry run (no writes).
    """
    # Get all attempt answers with topic info
    stmt = (
        select(AttemptAnswer)
        .join(AttemptAnswer.question)
        .join(Question.quiz)
        .where(Question.topic_label.isnot(None))
    )
    answers = list(db.execute(stmt).scalars().all())

    # Group by (topic_label, document_id)
    from collections import defaultdict
    groups: dict[tuple[str, Optional[str]], list[AttemptAnswer]] = defaultdict(list)
    for ans in answers:
        q = ans.question
        topic = q.topic_label or "General"
        doc_id = q.quiz.document_id if q.quiz else None
        groups[(topic, doc_id)].append(ans)

    report = {
        "topics_found": len(groups),
        "topics_updated": 0,
        "topics_created": 0,
        "apply": apply,
    }

    if not apply:
        return report

    for (topic_label, doc_id), topic_answers in groups.items():
        norm = _normalize_topic(topic_label)
        stmt2 = select(TopicPerformance).where(
            TopicPerformance.normalized_topic == norm,
            TopicPerformance.document_id == doc_id,
        )
        tp = db.execute(stmt2).scalar_one_or_none()

        correct = sum(1 for a in topic_answers if a.is_correct)
        total = len(topic_answers)
        accuracy = round((correct / total) * 100, 2) if total > 0 else 0.0
        now = _utcnow()

        if tp is None:
            tp = TopicPerformance(
                id=str(uuid4()),
                topic_label=topic_label,
                normalized_topic=norm,
                document_id=doc_id,
                created_at=now,
                updated_at=now,
            )
            db.add(tp)
            report["topics_created"] += 1
        else:
            report["topics_updated"] += 1

        tp.attempted_count = total
        tp.correct_count = correct
        tp.incorrect_count = total - correct
        tp.accuracy_percentage = accuracy
        tp.mastery_state = _classify_state(total, accuracy)

        review_bonus = _fetch_review_correct_count(db, topic_label=topic_label, document_id=doc_id)
        recent_accuracy = _fetch_recent_accuracy(db, topic_label=topic_label, document_id=doc_id)
        tp.mastery_score = _calculate_mastery_score(accuracy, recent_accuracy, review_bonus)

        dated_answers = [a for a in topic_answers if a.answered_at]
        if dated_answers:
            tp.last_attempted_at = max(a.answered_at for a in dated_answers)
            correct_dated = [a for a in dated_answers if a.is_correct]
            if correct_dated:
                tp.last_correct_at = max(a.answered_at for a in correct_dated)
            incorrect_dated = [a for a in dated_answers if not a.is_correct]
            if incorrect_dated:
                tp.last_incorrect_at = max(a.answered_at for a in incorrect_dated)
        tp.updated_at = now

    if apply:
        db.commit()

    return report
