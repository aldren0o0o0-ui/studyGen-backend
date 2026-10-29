"""
database/models/document.py — Phase 8 Document Processing & Diagnostics
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME
from app.database.models.auth import LEGACY_OWNER_ID

if TYPE_CHECKING:
    from app.database.models.quiz import Quiz
    from app.database.models.chat import ChatSessionDocument, ChatCitation
    from app.database.models.folder import Folder


_utcnow = utc_now


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING", nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTC_DATETIME, default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTC_DATETIME, default=_utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)

    # --- Phase 8 Extraction & Quality Fields ---
    extraction_method: Mapped[Optional[str]] = mapped_column(String(20), default="native", nullable=True)
    extraction_status: Mapped[Optional[str]] = mapped_column(String(20), default="READY", nullable=True)
    extraction_quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extraction_quality_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    total_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    native_page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ocr_page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failed_page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    empty_page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extraction_warnings_json: Mapped[Optional[list]] = mapped_column(JSON_VALUE, nullable=True)
    extractor_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)

    folder: Mapped[Optional["Folder"]] = relationship(
        "Folder", back_populates="documents", lazy="select"
    )
    quizzes: Mapped[List["Quiz"]] = relationship(
        "Quiz", back_populates="document", lazy="select"
    )
    processing_runs: Mapped[List["DocumentProcessingRun"]] = relationship(
        "DocumentProcessingRun", back_populates="document", cascade="all, delete-orphan", lazy="select"
    )
    pages: Mapped[List["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_user_created", "user_id", "created_at"),
        Index("ix_documents_user_status", "user_id", "status"),
        Index("ix_documents_folder_id", "folder_id"),
        Index("ix_documents_is_favorite", "is_favorite"),
        Index("ix_documents_last_accessed", "last_accessed_at"),
        Index("ix_documents_extraction_status", "extraction_status"),
        Index("ix_documents_extraction_quality", "extraction_quality_label"),
    )

    def soft_delete(self) -> None:
        self.deleted_at = _utcnow()
        self.status = "DELETED"
        self.updated_at = _utcnow()

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.original_filename} status={self.status}>"


class DocumentProcessingRun(Base):
    __tablename__ = "document_processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(20), default="native", nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(50), default="1.0.0", nullable=False)
    ocr_engine: Mapped[str] = mapped_column(String(50), default="none", nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    native_page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    empty_page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality_label: Mapped[str] = mapped_column(String(20), default="unusable", nullable=False)
    warnings_json: Mapped[Optional[list]] = mapped_column(JSON_VALUE, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="processing_runs")

    __table_args__ = (
        Index("ix_processing_runs_document_id", "document_id"),
        Index("ix_processing_runs_status", "status"),
        Index("ix_processing_runs_created_at", "created_at"),
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(20), default="native", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OK", nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    section_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    text_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    warning_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="pages")

    __table_args__ = (
        Index("ix_document_pages_document_page", "document_id", "page_number", unique=True),
        Index("ix_document_pages_status", "status"),
    )
