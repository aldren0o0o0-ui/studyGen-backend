"""
schemas/documents.py — Phase 8 Pydantic Schemas
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    status: str = "ready"
    extraction_method: str = "native"
    extraction_status: str = "READY"
    quality_score: float = 1.0
    quality_label: str = "excellent"
    warnings: List[str] = Field(default_factory=list)


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    file_size: int
    chunk_count: int
    status: str
    created_at: datetime
    quiz_count: int = 0
    latest_quiz_at: Optional[datetime] = None
    folder_id: Optional[str] = None
    is_favorite: bool = False
    last_accessed_at: Optional[datetime] = None
    extraction_method: Optional[str] = "native"
    extraction_status: Optional[str] = "READY"
    quality_label: Optional[str] = "excellent"
    total_pages: Optional[int] = 1

    model_config = {"from_attributes": True}


class DocumentDetail(DocumentSummary):
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    quality_score: Optional[float] = 1.0
    warnings: List[str] = Field(default_factory=list)
    native_page_count: Optional[int] = 1
    ocr_page_count: Optional[int] = 0
    failed_page_count: Optional[int] = 0
    empty_page_count: Optional[int] = 0
    extractor_version: Optional[str] = "2.0.0"
    ocr_engine: Optional[str] = "none"
    processed_at: Optional[datetime] = None


class DocumentListResponse(BaseModel):
    items: List[DocumentSummary]
    page: int
    page_size: int
    total: int


class DeleteResponse(BaseModel):
    document_id: str
    chunks_deleted: int
    status: str = "deleted"


class APIError(BaseModel):
    detail: str
    code: Optional[str] = None


# --- Phase 8 Diagnostics & Reprocessing Schemas ---

class ReprocessRequest(BaseModel):
    mode: str = "auto"  # 'auto' | 'native_only' | 'force_ocr'


class ReprocessResponse(BaseModel):
    document_id: str
    status: str
    extraction_method: str
    quality_score: float
    quality_label: str
    chunk_count: int
    warnings: List[str] = Field(default_factory=list)


class ProcessingRunResponse(BaseModel):
    run_id: str
    document_id: str
    status: str
    extraction_method: str
    extractor_version: str
    ocr_engine: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_pages: int
    native_page_count: int
    ocr_page_count: int
    failed_page_count: int
    empty_page_count: int
    quality_score: float
    quality_label: str
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None


class ProcessingRunListResponse(BaseModel):
    document_id: str
    runs: List[ProcessingRunResponse]


class DocumentPageResponse(BaseModel):
    page_number: int
    extraction_method: str
    status: str
    character_count: int
    quality_score: float
    section_label: Optional[str] = None


class DocumentPageListResponse(BaseModel):
    document_id: str
    pages: List[DocumentPageResponse]
    total_pages: int
