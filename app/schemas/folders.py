"""
schemas/folders.py — Pydantic models for Folder and Document Workspace operations
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def _sanitize_folder_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Folder name cannot be empty or blank.")
    if len(cleaned) > 255:
        raise ValueError("Folder name must not exceed 255 characters.")
    # Check for invalid control characters
    if re.search(r"[\x00-\x1f\x7f]", cleaned):
        raise ValueError("Folder name contains invalid control characters.")
    return cleaned


class FolderCreate(BaseModel):
    name: str = Field(..., description="Name of the folder")
    parent_folder_id: Optional[str] = Field(None, description="Optional parent folder ID for nested folders")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _sanitize_folder_name(v)


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Updated name of the folder")
    parent_folder_id: Optional[str] = Field(None, description="Updated parent folder ID (null to move to root)")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _sanitize_folder_name(v)
        return v


class FolderBreadcrumb(BaseModel):
    id: str
    name: str


class FolderSummary(BaseModel):
    id: str
    name: str
    parent_folder_id: Optional[str] = None
    document_count: int = 0
    subfolder_count: int = 0
    created_at: datetime
    updated_at: datetime


class FolderDetail(BaseModel):
    id: str
    name: str
    parent_folder_id: Optional[str] = None
    document_count: int = 0
    subfolder_count: int = 0
    breadcrumbs: List[FolderBreadcrumb] = []
    created_at: datetime
    updated_at: datetime


class FolderListResponse(BaseModel):
    items: List[FolderSummary]
    total: int


class DocumentMoveRequest(BaseModel):
    folder_id: Optional[str] = Field(None, description="Target folder ID or null to move to root")


class BulkMoveRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to move")
    folder_id: Optional[str] = Field(None, description="Target folder ID or null for root")


class BulkFavoriteRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to favorite/unfavorite")
    is_favorite: bool = Field(True, description="Favorite state to apply")


class BulkDeleteRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to soft-delete")


class BulkRestoreRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to restore")


class BulkPermanentDeleteRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to permanently delete")


class BulkActionResponse(BaseModel):
    affected_count: int
    status: str = "success"
