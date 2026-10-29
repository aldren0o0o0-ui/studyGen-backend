"""
database/models/flashcard.py — Phase 5 Models for Flashcards and Study Sets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME

if TYPE_CHECKING:
    from app.database.models.document import Document
    from app.database.models.quiz import Question
    from app.database.models.folder import Folder


_utcnow = utc_now


class StudySet(Base):
    __tablename__ = "study_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    topic_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    set_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")  # generated_from_document, generated_from_quiz, manual, mixed, generated_from_folder, generated_from_selected_documents
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")  # ACTIVE, ARCHIVED, DELETED
    card_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Wave 1 & 4 Scope Fields
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False, default="DOCUMENT")
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    document: Mapped[Optional["Document"]] = relationship("Document")
    folder: Mapped[Optional["Folder"]] = relationship("Folder", lazy="select")
    cards: Mapped[List["Flashcard"]] = relationship(
        "Flashcard",
        back_populates="study_set",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="Flashcard.position",
    )
    review_sessions: Mapped[List["FlashcardReviewSession"]] = relationship(
        "FlashcardReviewSession",
        back_populates="study_set",
        cascade="all, delete-orphan",
        lazy="select",
    )
    source_documents: Mapped[List["StudySetSourceDocument"]] = relationship(
        "StudySetSourceDocument",
        back_populates="study_set",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_study_sets_status", "status"),
        Index("ix_study_sets_document_id", "document_id"),
        Index("ix_study_sets_folder_id", "folder_id"),
        Index("ix_study_sets_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<StudySet id={self.id} title={self.title} card_count={self.card_count}>"


class StudySetSourceDocument(Base):
    __tablename__ = "study_set_source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_sets.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    study_set: Mapped["StudySet"] = relationship("StudySet", back_populates="source_documents")
    document: Mapped["Document"] = relationship("Document", lazy="select")

    __table_args__ = (
        Index("ix_study_set_source_documents_study_set_id", "study_set_id"),
        Index("ix_study_set_source_documents_document_id", "document_id"),
        UniqueConstraint("study_set_id", "document_id", name="uq_study_set_source_document"),
    )

    def __repr__(self) -> str:
        return f"<StudySetSourceDocument study_set_id={self.study_set_id} document_id={self.document_id}>"


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_sets.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    source_question_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    front_text: Mapped[str] = mapped_column(Text, nullable=False)
    back_text: Mapped[str] = mapped_column(Text, nullable=False)
    hint_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topic_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    card_type: Mapped[str] = mapped_column(String(30), nullable=False, default="concept")  # basic, definition, concept, question_answer
    source_references_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    generation_method: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")  # document_ai, quiz_conversion, manual
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")  # ACTIVE, SUSPENDED, DELETED
    is_user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="valid")  # valid, needs_review, unsupported

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    study_set: Mapped["StudySet"] = relationship("StudySet", back_populates="cards")
    document: Mapped[Optional["Document"]] = relationship("Document")
    source_question: Mapped[Optional["Question"]] = relationship("Question")
    review_state: Mapped[Optional["FlashcardReviewState"]] = relationship(
        "FlashcardReviewState",
        back_populates="flashcard",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="joined",
    )

    __table_args__ = (
        Index("ix_flashcards_study_set_id", "study_set_id"),
        Index("ix_flashcards_document_id", "document_id"),
        Index("ix_flashcards_status", "status"),
        UniqueConstraint("study_set_id", "position", name="uq_flashcard_position_per_set"),
    )

    def __repr__(self) -> str:
        return f"<Flashcard id={self.id} position={self.position} status={self.status}>"


class FlashcardReviewState(Base):
    __tablename__ = "flashcard_review_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flashcard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NEW")  # NEW, LEARNING, DUE, MASTERED, SUSPENDED
    ease_level: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    mastery_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    flashcard: Mapped["Flashcard"] = relationship("Flashcard", back_populates="review_state")

    __table_args__ = (
        Index("ix_flashcard_review_states_status", "review_status"),
        Index("ix_flashcard_review_states_next_review", "next_review_at"),
    )

    def __repr__(self) -> str:
        return f"<FlashcardReviewState flashcard_id={self.flashcard_id} status={self.review_status} interval={self.interval_days}>"


class FlashcardReviewSession(Base):
    __tablename__ = "flashcard_review_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    study_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_sets.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    session_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="due")  # due, all_cards, new_cards, weak_cards, custom
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")  # ACTIVE, COMPLETED, ABANDONED
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    study_set: Mapped["StudySet"] = relationship("StudySet", back_populates="review_sessions")
    items: Mapped[List["FlashcardReviewSessionItem"]] = relationship(
        "FlashcardReviewSessionItem",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="FlashcardReviewSessionItem.position",
    )

    __table_args__ = (
        Index("ix_flashcard_review_sessions_study_set_id", "study_set_id"),
        Index("ix_flashcard_review_sessions_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<FlashcardReviewSession id={self.id} mode={self.session_mode} status={self.status}>"


class FlashcardReviewSessionItem(Base):
    __tablename__ = "flashcard_review_session_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flashcard_review_sessions.id", ondelete="CASCADE"), nullable=False
    )
    flashcard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_revealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # again, hard, good, easy
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    session: Mapped["FlashcardReviewSession"] = relationship("FlashcardReviewSession", back_populates="items")
    flashcard: Mapped["Flashcard"] = relationship("Flashcard")

    __table_args__ = (
        Index("ix_flashcard_review_session_items_session_id", "session_id"),
        UniqueConstraint(
            "session_id",
            "position",
            name="uq_flashcard_review_session_item_position",
        ),
    )

    def __repr__(self) -> str:
        return f"<FlashcardReviewSessionItem session_id={self.session_id} pos={self.position} revealed={self.is_revealed}>"


class FlashcardReviewResult(Base):
    __tablename__ = "flashcard_review_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flashcard_review_sessions.id", ondelete="CASCADE"), nullable=False
    )
    flashcard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(20), nullable=False)  # again, hard, good, easy
    was_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    previous_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    next_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_flashcard_review_results_session_id", "session_id"),
        Index("ix_flashcard_review_results_flashcard_id", "flashcard_id"),
        Index("ix_flashcard_review_results_reviewed_at", "reviewed_at"),
    )

    def __repr__(self) -> str:
        return f"<FlashcardReviewResult flashcard_id={self.flashcard_id} rating={self.rating}>"
