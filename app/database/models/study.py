"""
database/models/study.py — Phase 4 Smart Study & Review System

Tables:
  topic_performance     — per-topic accuracy aggregates, mastery score/state
  review_items          — tracks questions due for spaced-repetition review
  review_sessions       — groups of review questions assembled into a session
  review_session_items  — immutable snapshot of questions in a session
  study_goals           — daily study goals with server-side progress tracking
  study_activities      — event log for quiz completions, reviews, uploads

Design notes:
  - Phase 7 scopes all root study records to an authenticated user.
  - All datetimes are stored as naive UTC (same convention as Phase 1–3 models).
  - Mastery state uses string literals so it can be read without enum imports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID
from app.database.types import UTC_DATETIME

DateTime = UTC_DATETIME

if TYPE_CHECKING:
    from app.database.models.quiz import Question
    from app.database.models.document import Document


def _utcnow() -> datetime:
    """Return current UTC time (timezone-aware). Works with TIMESTAMP WITH TIME ZONE."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# TopicPerformance
# ---------------------------------------------------------------------------

class TopicPerformance(Base):
    """
    Materialised aggregate of attempt results grouped by topic + document.

    Mastery states:
      insufficient_data — attempted_count < configured minimum
      weak              — accuracy < 50 %
      developing        — 50 % <= accuracy < 80 %
      strong            — accuracy >= 80 %
    """
    __tablename__ = "topic_performance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    topic_label: Mapped[str] = mapped_column(String(200), nullable=False)
    # Lowercased, stripped copy used for uniqueness dedup
    normalized_topic: Mapped[str] = mapped_column(String(200), nullable=False)

    # Nullable so rows survive document soft-delete
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )

    attempted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    incorrect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accuracy_percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    mastery_state: Mapped[str] = mapped_column(
        String(20), default="insufficient_data", nullable=False
    )

    last_attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_correct_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_incorrect_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "document_id", "normalized_topic",
            name="uq_topic_per_document",
        ),
        Index("ix_topic_perf_document_id", "document_id"),
        Index("ix_topic_perf_mastery_state", "mastery_state"),
        Index("ix_topic_perf_accuracy", "accuracy_percentage"),
        CheckConstraint("attempted_count >= 0", name="ck_tp_attempted_non_negative"),
        CheckConstraint("correct_count >= 0", name="ck_tp_correct_non_negative"),
        CheckConstraint("incorrect_count >= 0", name="ck_tp_incorrect_non_negative"),
    )

    def __repr__(self) -> str:
        return (
            f"<TopicPerformance topic={self.topic_label!r} "
            f"doc={self.document_id} "
            f"accuracy={self.accuracy_percentage:.1f}% state={self.mastery_state}>"
        )


# ---------------------------------------------------------------------------
# ReviewItem
# ---------------------------------------------------------------------------

class ReviewItem(Base):
    """
    One row per unique question that has been answered incorrectly at least once.
    Tracks spaced-repetition state: interval, next review date, consecutive correct streak.

    Review statuses:
      DUE       — next_review_at <= now (should be reviewed today)
      UPCOMING  — next_review_at > now (scheduled for a future date)
      MASTERED  — consecutive_correct >= 4 (long interval achieved)
      SUSPENDED — manually excluded from review
    """
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )

    # The original question that needs review.  Cascade-delete if question is purged.
    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_attempt_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("quiz_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Denormalised from question for fast filtering without joins
    topic_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    question_type: Mapped[str] = mapped_column(String(50), nullable=False, default="multiple_choice")
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")

    review_status: Mapped[str] = mapped_column(String(20), default="DUE", nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Attempt history
    incorrect_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    correct_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_correct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_review_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    interval_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    question: Mapped["Question"] = relationship("Question", lazy="select")

    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_review_item_question"),
        Index("ix_review_items_next_review_at", "next_review_at"),
        Index("ix_review_items_status", "review_status"),
        Index("ix_review_items_document_id", "document_id"),
        Index("ix_review_items_topic_label", "topic_label"),
        CheckConstraint("incorrect_count >= 1", name="ck_ri_incorrect_ge_one"),
        CheckConstraint("correct_review_count >= 0", name="ck_ri_correct_non_negative"),
        CheckConstraint("interval_days >= 0", name="ck_ri_interval_non_negative"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReviewItem question={self.question_id} "
            f"status={self.review_status} next={self.next_review_at}>"
        )


# ---------------------------------------------------------------------------
# ReviewSession
# ---------------------------------------------------------------------------

class ReviewSession(Base):
    """
    A grouped study session of review questions.

    session_type values:
      incorrect_answers — questions answered wrong in past quizzes
      weak_topics       — questions from topics classified as weak or developing
      due_review        — questions where next_review_at <= now
      custom            — filtered by document/topic/type/difficulty

    status values:
      active    — started, not yet submitted
      completed — all questions answered and scored
      abandoned — started but never completed
    """
    __tablename__ = "review_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)

    document_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    topic_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    items: Mapped[List["ReviewSessionItem"]] = relationship(
        "ReviewSessionItem",
        back_populates="session",
        order_by="ReviewSessionItem.position",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_review_sessions_status", "status"),
        Index("ix_review_sessions_created_at", "created_at"),
        Index("ix_review_sessions_document_id", "document_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReviewSession id={self.id} type={self.session_type} "
            f"status={self.status} {self.correct_count}/{self.question_count}>"
        )


# ---------------------------------------------------------------------------
# ReviewSessionItem
# ---------------------------------------------------------------------------

class ReviewSessionItem(Base):
    """
    Immutable snapshot of one question within a ReviewSession.
    Records the answer once the session is submitted.
    """
    __tablename__ = "review_session_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("review_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    selected_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    session: Mapped["ReviewSession"] = relationship("ReviewSession", back_populates="items")
    question: Mapped[Optional["Question"]] = relationship("Question", lazy="select")

    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_session_item_position"),
        Index("ix_rsi_session_id", "session_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReviewSessionItem session={self.session_id} "
            f"pos={self.position} correct={self.is_correct}>"
        )


# ---------------------------------------------------------------------------
# StudyGoal
# ---------------------------------------------------------------------------

class StudyGoal(Base):
    """
    A daily study goal set by the student.

    goal_type values:
      questions_answered        — answer N questions today
      review_items_completed    — complete N review items today
      study_sessions_completed  — complete N study sessions today

    goal_date: "YYYY-MM-DD" UTC date string.
    status: active | completed | expired
    """
    __tablename__ = "study_goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    goal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_value: Mapped[int] = mapped_column(Integer, nullable=False)
    current_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    goal_date: Mapped[str] = mapped_column(String(10), nullable=False)  # "YYYY-MM-DD"
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "goal_type", "goal_date", name="uq_goal_per_type_per_day"),
        Index("ix_study_goals_goal_date", "goal_date"),
        Index("ix_study_goals_status", "status"),
        CheckConstraint("target_value >= 1", name="ck_goal_target_ge_one"),
        CheckConstraint("current_value >= 0", name="ck_goal_current_non_negative"),
    )

    def __repr__(self) -> str:
        return (
            f"<StudyGoal type={self.goal_type} date={self.goal_date} "
            f"{self.current_value}/{self.target_value} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# StudyActivity
# ---------------------------------------------------------------------------

class StudyActivity(Base):
    """
    Append-only event log for student study activity.

    activity_type values:
      quiz_completed      — a quiz attempt was submitted
      review_completed    — a review session was completed
      document_uploaded   — a new document was uploaded
      retry_completed     — a retry quiz attempt was submitted
    """
    __tablename__ = "study_activities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)

    related_document_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    related_quiz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    related_attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    related_review_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    related_chat_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_study_activities_occurred_at", "occurred_at"),
        Index("ix_study_activities_type", "activity_type"),
        Index("ix_study_activities_chat_session", "related_chat_session_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<StudyActivity type={self.activity_type} "
            f"items={self.item_count} at={self.occurred_at}>"
        )
