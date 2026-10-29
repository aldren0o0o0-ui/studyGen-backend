"""
database/models/study_guide.py — Wave 5 Models for Study Guides & Synthesized Reviewers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME

if TYPE_CHECKING:
    from app.database.models.document import Document
    from app.database.models.folder import Folder


_utcnow = utc_now


class StudyGuide(Base):
    __tablename__ = "study_guides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Optional single document linkage
    document_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )

    # Canonical Wave 1 Scope Fields
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False, default="DOCUMENT")
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Guide Mode: comprehensive, condensed, exam_review
    guide_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="comprehensive")

    # Status: GENERATING, READY, FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="READY")

    # Metadata & Generation Metrics
    target_sections_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    generation_metrics_json: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    document: Mapped[Optional["Document"]] = relationship("Document")
    folder: Mapped[Optional["Folder"]] = relationship("Folder", lazy="select")
    sections: Mapped[List["StudyGuideSection"]] = relationship(
        "StudyGuideSection",
        back_populates="study_guide",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="StudyGuideSection.position",
    )
    source_documents: Mapped[List["StudyGuideSourceDocument"]] = relationship(
        "StudyGuideSourceDocument",
        back_populates="study_guide",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_study_guides_user_id", "user_id"),
        Index("ix_study_guides_status", "status"),
        Index("ix_study_guides_document_id", "document_id"),
        Index("ix_study_guides_folder_id", "folder_id"),
        Index("ix_study_guides_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<StudyGuide id={self.id} title={self.title} mode={self.guide_mode} status={self.status}>"


class StudyGuideSection(Base):
    __tablename__ = "study_guide_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_guide_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_guides.id", ondelete="CASCADE"), nullable=False
    )
    section_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    structured_content_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    source_references_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    study_guide: Mapped["StudyGuide"] = relationship("StudyGuide", back_populates="sections")

    __table_args__ = (
        Index("ix_study_guide_sections_guide_id", "study_guide_id"),
        Index("ix_study_guide_sections_type", "section_type"),
        Index("ix_study_guide_sections_guide_pos", "study_guide_id", "position"),
    )

    def __repr__(self) -> str:
        return f"<StudyGuideSection id={self.id} guide_id={self.study_guide_id} type={self.section_type}>"


class StudyGuideSourceDocument(Base):
    __tablename__ = "study_guide_source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    study_guide_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_guides.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    study_guide: Mapped["StudyGuide"] = relationship("StudyGuide", back_populates="source_documents")
    document: Mapped["Document"] = relationship("Document", lazy="select")

    __table_args__ = (
        UniqueConstraint("study_guide_id", "document_id", name="uq_study_guide_source_doc"),
        Index("ix_guide_source_docs_guide_id", "study_guide_id"),
        Index("ix_guide_source_docs_doc_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<StudyGuideSourceDocument guide_id={self.study_guide_id} doc_id={self.document_id}>"
