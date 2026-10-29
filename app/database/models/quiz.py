"""
database/models/quiz.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME
from app.database.models.auth import LEGACY_OWNER_ID

if TYPE_CHECKING:
    from app.database.models.document import Document
    from app.database.models.attempt import QuizAttempt
    from app.database.models.folder import Folder


_utcnow = utc_now


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    question_types_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=lambda: ["multiple_choice"])
    chunks_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Wave 1 & 3 Scope Fields
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False, default="DOCUMENT")
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 3 Fields
    coverage_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="balanced")
    generation_seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    configuration_json: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="legacy_phase2")
    model_name: Mapped[str] = mapped_column(String(100), nullable=False, default="gemini-1.5-flash")
    source_quiz_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True)
    source_attempt_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("quiz_attempts.id", ondelete="SET NULL", use_alter=True, name="fk_quiz_source_attempt_id"),
        nullable=True,
    )
    quiz_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="generated")

    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    document: Mapped[Optional["Document"]] = relationship("Document", back_populates="quizzes")
    folder: Mapped[Optional["Folder"]] = relationship("Folder", lazy="select")
    questions: Mapped[List["Question"]] = relationship(
        "Question",
        back_populates="quiz",
        order_by="Question.position",
        cascade="all, delete-orphan",
        lazy="select",
    )
    attempts: Mapped[List["QuizAttempt"]] = relationship(
        "QuizAttempt",
        foreign_keys="QuizAttempt.quiz_id",
        back_populates="quiz",
        cascade="all, delete-orphan",
        lazy="select",
    )
    source_documents: Mapped[List["QuizSourceDocument"]] = relationship(
        "QuizSourceDocument",
        back_populates="quiz",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_quizzes_document_id", "document_id"),
        Index("ix_quizzes_folder_id", "folder_id"),
        Index("ix_quizzes_created_at", "created_at"),
        Index("ix_quizzes_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Quiz id={self.id} title={self.title} difficulty={self.difficulty}>"


class QuizSourceDocument(Base):
    __tablename__ = "quiz_source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    quiz_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    quiz: Mapped["Quiz"] = relationship("Quiz", back_populates="source_documents")
    document: Mapped["Document"] = relationship("Document", lazy="select")

    __table_args__ = (
        Index("ix_quiz_source_documents_quiz_id", "quiz_id"),
        Index("ix_quiz_source_documents_document_id", "document_id"),
        UniqueConstraint("quiz_id", "document_id", name="uq_quiz_source_document"),
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    quiz_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_chunk_indexes_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    
    # Phase 3 Fields
    question_type: Mapped[str] = mapped_column(String(50), nullable=False, default="multiple_choice")
    source_references_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    topic_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    accepted_answers_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    quiz: Mapped["Quiz"] = relationship("Quiz", back_populates="questions")
    answers: Mapped[List["AttemptAnswer"]] = relationship(
        "AttemptAnswer",
        back_populates="question",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_questions_quiz_id", "quiz_id"),
        UniqueConstraint("quiz_id", "position", name="uq_question_position"),
    )

    def __repr__(self) -> str:
        return f"<Question id={self.id} position={self.position}>"


# Import here to avoid circular import
from app.database.models.attempt import AttemptAnswer  # noqa: E402
