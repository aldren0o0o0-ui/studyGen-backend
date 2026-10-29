"""
services/study_recommendation_service.py — Phase 4

Deterministic next-study recommendation engine.

Priority Order:
  1. Overdue review items (next_review_at <= now)
  2. Critical weak topic (accuracy < 50 %, attempted >= 2)
  3. Weak topic (accuracy < 70 %, attempted >= 2)
  4. Developing topic (50 % <= accuracy < 80 %, attempted >= 2)
  5. Continue most recent document (if quizzes exist)
  6. Insufficient data fallback (when < 1 quiz attempt exists)

No AI or LLM is used. Explanations cite actual stored counts and accuracy rates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from app.database.models.document import Document
from app.database.models.quiz import Quiz
from app.database.models.attempt import QuizAttempt
from app.database.models.study import ReviewItem, TopicPerformance, ReviewSession
from app.services import review_item_service, topic_performance_service

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_next_study_recommendation(db: Session) -> Dict[str, Any]:
    now = _utcnow()

    # Priority 1: Overdue review items or overdue flashcards
    due_count = review_item_service.count_due_items(db, now=now)
    if due_count > 0:
        return {
            "recommendation_type": "review_due_items",
            "title": "Review Due Items",
            "reason": f"You have {due_count} question{'s' if due_count != 1 else ''} due for spaced repetition review today.",
            "document_id": None,
            "topic_label": None,
            "due_item_count": due_count,
            "suggested_question_count": min(due_count, 10),
        }

    # Priority 1b: Overdue flashcards
    from app.database.models.flashcard import Flashcard, FlashcardReviewState
    due_flashcard_count = (
        db.query(FlashcardReviewState)
        .join(Flashcard, FlashcardReviewState.flashcard_id == Flashcard.id)
        .filter(
            Flashcard.status == "ACTIVE",
            FlashcardReviewState.review_status.in_(["DUE", "NEW", "LEARNING"]),
            or_(FlashcardReviewState.next_review_at.is_(None), FlashcardReviewState.next_review_at <= now),
        )
        .count()
    )
    if due_flashcard_count > 0:
        return {
            "recommendation_type": "review_due_flashcards",
            "title": "Review Due Flashcards",
            "reason": f"You have {due_flashcard_count} flashcard{'s' if due_flashcard_count != 1 else ''} ready for review today.",
            "document_id": None,
            "topic_label": None,
            "due_item_count": due_flashcard_count,
            "suggested_question_count": min(due_flashcard_count, 15),
        }

    # Priority 2 & 3: Critical weak topic (<50%) or Weak topic (<70%)
    topics = topic_performance_service.list_topic_performance(
        db, sort_by="weakest", limit=10
    )
    for tp in topics:
        if tp.attempted_count >= 2 and tp.accuracy_percentage < 70.0:
            severity = "critical weak" if tp.accuracy_percentage < 50.0 else "weak"
            return {
                "recommendation_type": "review_weak_topic",
                "title": f"Review {tp.topic_label}",
                "reason": f"'{tp.topic_label}' is marked {severity} with {tp.accuracy_percentage:.1f}% accuracy across {tp.attempted_count} questions.",
                "document_id": tp.document_id,
                "topic_label": tp.topic_label,
                "due_item_count": tp.incorrect_count,
                "suggested_question_count": min(max(tp.incorrect_count, 5), 10),
            }

    # Priority 4: Developing topic (50% <= accuracy < 80%)
    for tp in topics:
        if tp.attempted_count >= 2 and tp.accuracy_percentage < 80.0:
            return {
                "recommendation_type": "review_developing_topic",
                "title": f"Strengthen {tp.topic_label}",
                "reason": f"'{tp.topic_label}' is developing with {tp.accuracy_percentage:.1f}% accuracy. A quick review will boost your mastery.",
                "document_id": tp.document_id,
                "topic_label": tp.topic_label,
                "due_item_count": tp.incorrect_count,
                "suggested_question_count": 5,
            }

    # Priority 5: Continue most recent active document
    doc_stmt = (
        select(Document)
        .where(Document.status != "DELETED")
        .order_by(Document.updated_at.desc())
        .limit(1)
    )
    latest_doc = db.execute(doc_stmt).scalar_one_or_none()
    if latest_doc:
        quiz_count_stmt = select(func.count(Quiz.id)).where(Quiz.document_id == latest_doc.id)
        q_count = db.execute(quiz_count_stmt).scalar_one() or 0
        if q_count > 0:
            return {
                "recommendation_type": "continue_document",
                "title": f"Study {latest_doc.original_filename}",
                "reason": f"You recently worked on '{latest_doc.original_filename}'. Generate a new quiz or review key concepts.",
                "document_id": latest_doc.id,
                "topic_label": None,
                "due_item_count": 0,
                "suggested_question_count": 5,
            }

        # Optional chat recommendation is deliberately below due review,
        # flashcards, weak topics, and existing study continuation.
        from app.database.models.chat import ChatSession, ChatSessionDocument
        chat_count = db.execute(
            select(func.count(ChatSession.id))
            .join(ChatSessionDocument, ChatSessionDocument.chat_session_id == ChatSession.id)
            .where(
                ChatSessionDocument.document_id == latest_doc.id,
                ChatSession.deleted_at.is_(None),
            )
        ).scalar_one() or 0
        if latest_doc.status == "READY" and not latest_doc.deleted_at and chat_count == 0:
            return {
                "recommendation_type": "chat_with_document",
                "title": f"Ask about {latest_doc.original_filename}",
                "reason": "Open a document chat for a source-backed explanation.",
                "document_id": latest_doc.id,
                "topic_label": None,
                "due_item_count": 0,
                "suggested_question_count": 1,
            }

    # Priority 6: Insufficient data fallback
    return {
        "recommendation_type": "insufficient_data",
        "title": "Start Your First Quiz",
        "reason": "Complete a quiz to unlock weak-topic analysis, spaced repetition, and targeted study recommendations.",
        "document_id": None,
        "topic_label": None,
        "due_item_count": 0,
        "suggested_question_count": 5,
    }


def get_study_overview(db: Session) -> Dict[str, Any]:
    """Compile high-level overview metrics for the Study Overview endpoint."""
    now = _utcnow()

    # Total quizzes completed (total quiz_attempts)
    quiz_attempts_count = db.execute(select(func.count(QuizAttempt.id))).scalar_one() or 0

    # Total review sessions completed
    review_sessions_count = db.execute(
        select(func.count(ReviewSession.id)).where(ReviewSession.status == "completed")
    ).scalar_one() or 0

    # Total questions answered across quiz attempts + review sessions
    q_from_attempts = db.execute(select(func.coalesce(func.sum(QuizAttempt.total_questions), 0))).scalar_one() or 0
    q_from_reviews = db.execute(
        select(func.coalesce(func.sum(ReviewSession.completed_count), 0)).where(ReviewSession.status == "completed")
    ).scalar_one() or 0
    total_questions_answered = q_from_attempts + q_from_reviews

    # Overall accuracy
    correct_from_attempts = db.execute(select(func.coalesce(func.sum(QuizAttempt.score), 0))).scalar_one() or 0
    correct_from_reviews = db.execute(
        select(func.coalesce(func.sum(ReviewSession.correct_count), 0)).where(ReviewSession.status == "completed")
    ).scalar_one() or 0

    total_correct = correct_from_attempts + correct_from_reviews
    overall_accuracy = round((total_correct / total_questions_answered) * 100, 1) if total_questions_answered > 0 else 0.0

    # Counts
    due_review_count = review_item_service.count_due_items(db, now=now)
    weak_topic_count = db.execute(
        select(func.count(TopicPerformance.id)).where(TopicPerformance.mastery_state == "weak")
    ).scalar_one() or 0

    # Recommendation
    recommendation = get_next_study_recommendation(db)

    # Active daily goals
    from app.services import study_goal_service
    goals = study_goal_service.get_goals(db)

    return {
        "quizzes_completed": quiz_attempts_count,
        "review_sessions_completed": review_sessions_count,
        "questions_answered": total_questions_answered,
        "overall_accuracy": overall_accuracy,
        "due_review_count": due_review_count,
        "weak_topic_count": weak_topic_count,
        "recommendation": recommendation,
        "daily_goals": goals,
    }
