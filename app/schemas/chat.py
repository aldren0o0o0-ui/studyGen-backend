"""Typed public schemas for Phase 6 document chat."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ChatMode = Literal["document_qa", "explain", "simplify", "compare", "reviewer"]
ChatStatus = Literal["ACTIVE", "ARCHIVED", "DELETED"]


class ChatSessionCreate(BaseModel):
    document_ids: Optional[list[str]] = None
    scope_type: Optional[str] = None
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    title: Optional[str] = Field(default=None, max_length=255)
    system_mode: ChatMode = "document_qa"

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value.strip()]
        return list(dict.fromkeys(cleaned))


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    status: Optional[Literal["ACTIVE", "ARCHIVED"]] = None
    system_mode: Optional[ChatMode] = None


class SelectedDocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    status: str
    position: int


class ChatCitationResponse(BaseModel):
    citation_id: str
    document_id: str
    document_name: str
    page_number: Optional[int] = None
    section_label: Optional[str] = None
    excerpt: str
    source_order: int


class ChatMessageResponse(BaseModel):
    message_id: str
    role: Literal["user", "assistant", "system_summary"]
    content: str
    status: Literal["PENDING", "COMPLETED", "FAILED", "CANCELLED"]
    sequence_number: int
    citations: list[ChatCitationResponse] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False
    created_at: datetime


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    status: ChatStatus
    system_mode: ChatMode
    document_scope_mode: str
    scope_type: str = "document"
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    include_subfolders: bool = False
    documents: list[SelectedDocumentSummary]
    message_count: int
    latest_message_preview: Optional[str] = None
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    regeneration_count: int


class ChatSessionListResponse(BaseModel):
    items: list[ChatSessionSummary]
    page: int
    page_size: int
    total: int


class UserMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    mode: Optional[ChatMode] = None

    @field_validator("message")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be empty.")
        return value


class ChatMessagePage(BaseModel):
    items: list[ChatMessageResponse]
    page: int
    page_size: int
    total: int


class ChatGenerationResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse


class ChatDeleteResponse(BaseModel):
    session_id: str
    status: Literal["DELETED"]
    source_documents_deleted: bool = False


class ChatCancelResponse(BaseModel):
    session_id: str
    cancelled_message_id: Optional[str] = None
    status: str


class ChatUsageSummary(BaseModel):
    sessions_started: int
    questions_asked: int
    documents_discussed: int
