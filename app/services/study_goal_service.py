"""
services/study_goal_service.py — Phase 4

Daily study goal tracking service.

Goal types:
  questions_answered        — answer N questions today
  review_items_completed    — complete N review items today
  study_sessions_completed  — complete N study sessions today

Rules:
  - Progress is updated strictly by backend events (attempts, review sessions).
  - React Native client CANNOT directly set progress values.
  - Unique goal per (goal_type, goal_date) where goal_date is YYYY-MM-DD UTC date string.
  - Status updates to 'completed' when current_value >= target_value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.attempt import QuizAttempt
from app.database.models.study import ReviewSession, StudyGoal

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_str(now: Optional[datetime] = None) -> str:
    if now is None:
        now = _utcnow()
    return now.strftime("%Y-%m-%d")


def create_or_update_goal(
    db: Session,
    *,
    goal_type: str,
    target_value: int,
    goal_date: Optional[str] = None,
) -> StudyGoal:
    """Create a new daily goal or update the target of an existing active goal."""
    if target_value < 1:
        raise HTTPException(status_code=400, detail="Target value must be at least 1.")

    valid_types = {
        "questions_answered",
        "review_items_completed",
        "study_sessions_completed",
        "flashcards_reviewed",
    }
    if goal_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid goal_type '{goal_type}'. Supported: {sorted(valid_types)}",
        )

    date_str = goal_date or _today_str()
    now = _utcnow()

    stmt = select(StudyGoal).where(
        StudyGoal.goal_type == goal_type,
        StudyGoal.goal_date == date_str,
    )
    goal = db.execute(stmt).scalar_one_or_none()

    if goal is None:
        goal = StudyGoal(
            id=str(uuid4()),
            goal_type=goal_type,
            target_value=target_value,
            current_value=0,
            goal_date=date_str,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(goal)
    else:
        goal.target_value = target_value
        goal.updated_at = now
        if goal.current_value >= goal.target_value:
            goal.status = "completed"
            if not goal.completed_at:
                goal.completed_at = now
        else:
            goal.status = "active"
            goal.completed_at = None

    db.commit()
    db.refresh(goal)
    return goal


def update_from_attempt(db: Session, attempt: QuizAttempt) -> None:
    """Event hook: update questions_answered goal on quiz completion."""
    now = _utcnow()
    date_str = _today_str(now)
    q_count = attempt.total_questions or 0
    if q_count <= 0:
        return

    _increment_goal(db, goal_type="questions_answered", date_str=date_str, delta=q_count, now=now)


def update_from_review_session(db: Session, session: ReviewSession) -> None:
    """Event hook: update goals when a review session is submitted."""
    now = _utcnow()
    date_str = _today_str(now)

    # 1. Update questions_answered
    if session.completed_count > 0:
        _increment_goal(
            db, goal_type="questions_answered", date_str=date_str, delta=session.completed_count, now=now
        )

    # 2. Update review_items_completed
    if session.completed_count > 0:
        _increment_goal(
            db, goal_type="review_items_completed", date_str=date_str, delta=session.completed_count, now=now
        )

    # 3. Update study_sessions_completed
    _increment_goal(
        db, goal_type="study_sessions_completed", date_str=date_str, delta=1, now=now
    )


def record_flashcards_reviewed_progress(db: Session, cards_count: int = 1) -> None:
    """Event hook: update flashcards_reviewed goal when a flashcard is rated."""
    if cards_count <= 0:
        return
    now = _utcnow()
    date_str = _today_str(now)
    _increment_goal(db, goal_type="flashcards_reviewed", date_str=date_str, delta=cards_count, now=now)


def _increment_goal(
    db: Session,
    *,
    goal_type: str,
    date_str: str,
    delta: int,
    now: datetime,
) -> None:
    stmt = select(StudyGoal).where(
        StudyGoal.goal_type == goal_type,
        StudyGoal.goal_date == date_str,
    )
    goal = db.execute(stmt).scalar_one_or_none()
    if goal and goal.status != "expired":
        goal.current_value += delta
        goal.updated_at = now
        if goal.current_value >= goal.target_value:
            goal.status = "completed"
            if not goal.completed_at:
                goal.completed_at = now
        db.flush()


def get_goals(
    db: Session,
    *,
    goal_date: Optional[str] = None,
) -> List[StudyGoal]:
    date_str = goal_date or _today_str()
    stmt = (
        select(StudyGoal)
        .where(StudyGoal.goal_date == date_str)
        .order_by(StudyGoal.created_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def get_goal_by_id(db: Session, goal_id: str) -> Optional[StudyGoal]:
    return db.get(StudyGoal, goal_id)
