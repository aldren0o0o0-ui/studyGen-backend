"""
services/review_session_service.py — Phase 4

Creates, retrieves, and scores targeted review sessions.

Session Generation Rules:
  - NO Gemini API call is made. Uses existing persisted questions.
  - Selects questions based on session_type:
      * incorrect_answers: items with past incorrect quiz answers
      * weak_topics: items from topics with mastery_state weak or developing
      * due_review: items where next_review_at <= now
      * custom: optional filters (document_id, topic_label, question_type, difficulty)
  - Prevents duplicate questions within the same session.
  - Returns an immutable snapshot in review_session_items.
  - Active review sessions NEVER expose correct answers or explanations.
  - Scoring is performed server-side reusing attempt_service validation logic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.database.models.quiz import Question
from app.database.models.study import (
    ReviewItem,
    ReviewSession,
    ReviewSessionItem,
    TopicPerformance,
)
from app.services import attempt_service, review_item_service
from app.services.topic_performance_service import _normalize_topic

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Session Creation
# ---------------------------------------------------------------------------

def create_review_session(
    db: Session,
    *,
    session_type: str,  # incorrect_answers | weak_topics | due_review | custom
    document_id: Optional[str] = None,
    topic_label: Optional[str] = None,
    question_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    requested_count: int = 10,
) -> ReviewSession:
    """
    Select candidate questions from review_items or questions table,
    create immutable ReviewSession + ReviewSessionItem records.
    """
    now = _utcnow()
    candidate_question_ids: List[str] = []

    if session_type == "due_review":
        items = review_item_service.get_due_review_items(
            db,
            document_id=document_id,
            topic_label=topic_label,
            question_type=question_type,
            difficulty=difficulty,
            limit=requested_count,
            now=now,
        )
        candidate_question_ids = [item.question_id for item in items]

    elif session_type == "incorrect_answers":
        items = review_item_service.get_incorrect_review_items(
            db,
            document_id=document_id,
            topic_label=topic_label,
            question_type=question_type,
            difficulty=difficulty,
            limit=requested_count,
        )
        candidate_question_ids = [item.question_id for item in items]

    elif session_type == "weak_topics":
        # Find weak or developing topics first
        topic_stmt = select(TopicPerformance.topic_label).where(
            TopicPerformance.mastery_state.in_(["weak", "developing"])
        )
        if document_id:
            topic_stmt = topic_stmt.where(TopicPerformance.document_id == document_id)
        if topic_label:
            norm = _normalize_topic(topic_label)
            topic_stmt = topic_stmt.where(TopicPerformance.normalized_topic == norm)
        
        weak_topics = list(db.execute(topic_stmt).scalars().all())

        if weak_topics:
            stmt = select(ReviewItem).where(
                ReviewItem.topic_label.in_(weak_topics),
                ReviewItem.review_status.in_(["DUE", "UPCOMING"]),
            )
            if document_id:
                stmt = stmt.where(ReviewItem.document_id == document_id)
            if question_type:
                stmt = stmt.where(ReviewItem.question_type == question_type)
            if difficulty:
                stmt = stmt.where(ReviewItem.difficulty == difficulty)
            stmt = stmt.order_by(ReviewItem.priority_score.desc()).limit(requested_count)
            items = list(db.execute(stmt).scalars().all())
            candidate_question_ids = [item.question_id for item in items]
        
        # Fallback if no review_items exist for weak topics: select directly from questions
        if not candidate_question_ids and weak_topics:
            q_stmt = select(Question.id).where(Question.topic_label.in_(weak_topics))
            if document_id:
                q_stmt = q_stmt.join(Question.quiz).where(Question.quiz.has(document_id=document_id))
            if question_type:
                q_stmt = q_stmt.where(Question.question_type == question_type)
            if difficulty:
                q_stmt = q_stmt.where(Question.difficulty == difficulty)
            q_stmt = q_stmt.limit(requested_count)
            candidate_question_ids = list(db.execute(q_stmt).scalars().all())

    elif session_type == "custom":
        # Pull from review_items first
        items = review_item_service.get_incorrect_review_items(
            db,
            document_id=document_id,
            topic_label=topic_label,
            question_type=question_type,
            difficulty=difficulty,
            limit=requested_count,
        )
        candidate_question_ids = [item.question_id for item in items]

        # If not enough review items, complement with questions from questions table
        if len(candidate_question_ids) < requested_count:
            needed = requested_count - len(candidate_question_ids)
            q_stmt = select(Question.id)
            if candidate_question_ids:
                q_stmt = q_stmt.where(Question.id.not_in(candidate_question_ids))
            if topic_label:
                q_stmt = q_stmt.where(Question.topic_label == topic_label)
            if question_type:
                q_stmt = q_stmt.where(Question.question_type == question_type)
            if difficulty:
                q_stmt = q_stmt.where(Question.difficulty == difficulty)
            if document_id:
                q_stmt = q_stmt.join(Question.quiz).where(Question.quiz.has(document_id=document_id))
            q_stmt = q_stmt.limit(needed)
            extra_ids = list(db.execute(q_stmt).scalars().all())
            candidate_question_ids.extend(extra_ids)

    else:
        raise HTTPException(status_code=400, detail=f"Invalid session_type '{session_type}'.")

    # Deduplicate while preserving order
    seen: set[str] = set()
    final_qids: list[str] = []
    for qid in candidate_question_ids:
        if qid not in seen:
            seen.add(qid)
            final_qids.append(qid)

    if not final_qids:
        raise HTTPException(
            status_code=404,
            detail="No questions available matching the specified review session criteria.",
        )

    session_id = str(uuid4())
    session = ReviewSession(
        id=session_id,
        session_type=session_type,
        document_id=document_id,
        topic_label=topic_label,
        status="active",
        question_count=len(final_qids),
        correct_count=0,
        completed_count=0,
        started_at=now,
        created_at=now,
    )
    db.add(session)

    for i, qid in enumerate(final_qids):
        db.add(
            ReviewSessionItem(
                id=str(uuid4()),
                session_id=session_id,
                question_id=qid,
                position=i,
            )
        )

    db.commit()
    return get_review_session_by_id(db, session_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Session Submission & Scoring
# ---------------------------------------------------------------------------

def submit_review_session(
    db: Session,
    session_id: str,
    answers: List[dict],
) -> ReviewSession:
    """
    Score review session answers server-side, update session state,
    update spaced-repetition schedules, topic performance, and study goal/activity.
    """
    session = get_review_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"ReviewSession '{session_id}' not found.")

    if session.status == "completed":
        raise HTTPException(status_code=400, detail=f"ReviewSession '{session_id}' is already completed.")

    if not answers:
        raise HTTPException(status_code=400, detail="No answers submitted.")

    # Map session items by question_id
    session_items_map: Dict[str, ReviewSessionItem] = {
        item.question_id: item for item in session.items if item.question_id
    }

    # Validate submitted answer structure
    seen_qids: set[str] = set()
    for ans in answers:
        qid = ans.get("question_id", "")
        if qid in seen_qids:
            raise HTTPException(status_code=400, detail=f"Duplicate answer for question_id '{qid}'.")
        seen_qids.add(qid)

        if qid not in session_items_map:
            raise HTTPException(
                status_code=400,
                detail=f"question_id '{qid}' does not belong to review session '{session_id}'.",
            )
        
        rsi = session_items_map[qid]
        if rsi.question:
            attempt_service._validate_answer_choice(rsi.question, ans.get("selected_answer", ""))

    now = _utcnow()
    correct_count = 0

    for ans in answers:
        qid = ans["question_id"]
        selected = ans["selected_answer"]
        rsi = session_items_map[qid]
        q = rsi.question

        is_correct = False
        if q:
            is_correct = attempt_service._is_correct_answer(q, selected)

        rsi.selected_answer = selected
        rsi.is_correct = is_correct
        rsi.answered_at = now

        if is_correct:
            correct_count += 1

        # Update spaced repetition schedule for this question
        review_item_service.update_after_review_answer(
            db,
            question_id=qid,
            is_correct=is_correct,
            now=now,
        )

    session.correct_count = correct_count
    session.completed_count = len(answers)
    session.status = "completed"
    session.completed_at = now

    # Update study goals & activity logging
    from app.services import study_goal_service, study_activity_service, topic_performance_service
    study_activity_service.log_review_completed(db, session)
    study_goal_service.update_from_review_session(db, session)

    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_review_session_by_id(db: Session, session_id: str) -> Optional[ReviewSession]:
    stmt = (
        select(ReviewSession)
        .where(ReviewSession.id == session_id)
        .options(
            selectinload(ReviewSession.items)
            .selectinload(ReviewSessionItem.question)
            .selectinload(Question.quiz)
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def list_review_sessions(
    db: Session,
    page: int = 1,
    page_size: int = 20,
) -> tuple[List[ReviewSession], int]:
    stmt = select(ReviewSession)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    stmt = stmt.order_by(ReviewSession.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total
