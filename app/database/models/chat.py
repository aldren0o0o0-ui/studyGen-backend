"""Phase 6 persistent, document-scoped chat models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME


_utcnow = utc_now


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    document_scope_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(30), default="document", nullable=False)
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", name="fk_chat_sessions_folder_id", ondelete="SET NULL"),
        nullable=True
    )
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_mode: Mapped[str] = mapped_column(String(30), default="document_qa", nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    regeneration_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    folder = relationship("Folder", lazy="select")
    document_links: Mapped[List["ChatSessionDocument"]] = relationship(
        "ChatSessionDocument", back_populates="session", order_by="ChatSessionDocument.position"
    )
    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", order_by="ChatMessage.sequence_number"
    )

    __table_args__ = (
        Index("ix_chat_sessions_status", "status"),
        Index("ix_chat_sessions_last_message_at", "last_message_at"),
        Index("ix_chat_sessions_created_at", "created_at"),
        Index("ix_chat_sessions_user_recent", "user_id", "last_message_at"),
        Index("ix_chat_sessions_folder_id", "folder_id"),
        CheckConstraint("message_count >= 0", name="ck_chat_session_message_count"),
        CheckConstraint("regeneration_count >= 0", name="ck_chat_session_regeneration_count"),
    )


class ChatSessionDocument(Base):
    __tablename__ = "chat_session_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="document_links")
    document = relationship("Document")

    __table_args__ = (
        UniqueConstraint("chat_session_id", "document_id", name="uq_chat_session_document"),
        UniqueConstraint("chat_session_id", "position", name="uq_chat_session_document_position"),
        Index("ix_chat_session_documents_session", "chat_session_id"),
        Index("ix_chat_session_documents_document", "document_id"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    input_token_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_token_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    follow_up_suggestions_json: Mapped[list] = mapped_column(JSON_VALUE, nullable=False, default=list)
    insufficient_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supersedes_message_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
    citations: Mapped[List["ChatCitation"]] = relationship(
        "ChatCitation", back_populates="message", order_by="ChatCitation.source_order"
    )

    __table_args__ = (
        UniqueConstraint("chat_session_id", "sequence_number", name="uq_chat_message_sequence"),
        Index("ix_chat_messages_session", "chat_session_id"),
        Index("ix_chat_messages_status", "status"),
        CheckConstraint("sequence_number >= 1", name="ck_chat_message_sequence"),
    )


class ChatCitation(Base):
    __tablename__ = "chat_citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    message: Mapped["ChatMessage"] = relationship("ChatMessage", back_populates="citations")
    document = relationship("Document")

    __table_args__ = (
        UniqueConstraint("message_id", "document_id", "chunk_index", name="uq_chat_citation_chunk"),
        UniqueConstraint("message_id", "source_order", name="uq_chat_citation_order"),
        Index("ix_chat_citations_message", "message_id"),
        Index("ix_chat_citations_document", "document_id"),
    )


class ChatGenerationRecord(Base):
    __tablename__ = "chat_generation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assistant_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    retrieval_query: Mapped[str] = mapped_column(String(1000), nullable=False)
    retrieved_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_character_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    generation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_chat_generation_message", "assistant_message_id"),
        Index("ix_chat_generation_status", "generation_status"),
    )
