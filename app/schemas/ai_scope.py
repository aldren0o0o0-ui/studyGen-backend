"""Pydantic schemas for Course and Folder Intelligence AI Scope (Wave 1)."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class AIScopeType(str, Enum):
    DOCUMENT = "document"
    SELECTED_DOCUMENTS = "selected_documents"
    FOLDER = "folder"


class AIScopeRequest(BaseModel):
    scope_type: AIScopeType = Field(
        default=AIScopeType.DOCUMENT,
        description="Type of AI scope: 'document', 'selected_documents', or 'folder'",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Required when scope_type is 'document'",
    )
    document_ids: Optional[List[str]] = Field(
        default=None,
        description="Required when scope_type is 'selected_documents'",
    )
    folder_id: Optional[str] = Field(
        default=None,
        description="Required when scope_type is 'folder'",
    )
    include_subfolders: bool = Field(
        default=False,
        description="Whether to include descendant subfolders when scope_type is 'folder'",
    )


class AIScopeDocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    folder_id: Optional[str] = None


class AIScopePreviewResponse(BaseModel):
    scope_type: str
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    include_subfolders: bool = False
    document_count: int
    folder_count: int
    total_files_in_scope: int
    non_ready_count: int
    documents: List[AIScopeDocumentSummary]
