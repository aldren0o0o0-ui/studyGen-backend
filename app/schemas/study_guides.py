"""
app/schemas/study_guides.py — Wave 5 Schemas for Study Guides & Synthesized Reviewers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


GuideMode = Literal["comprehensive", "condensed", "exam_review"]


class GenerateStudyGuideRequest(BaseModel):
    document_id: Optional[str] = Field(None, description="Single document ID")
    document_ids: Optional[List[str]] = Field(None, description="Selected document IDs")
    folder_id: Optional[str] = Field(None, description="Target folder ID")
    include_subfolders: bool = Field(False, description="Whether to include nested subfolders")
    scope_type: Optional[str] = Field(None, description="DOCUMENT, SELECTED_DOCUMENTS, or FOLDER")
    guide_mode: GuideMode = Field("comprehensive", description="comprehensive, condensed, or exam_review")
    target_sections: Optional[List[str]] = Field(None, description="Specific sections to include")
    topic_query: Optional[str] = Field(None, description="Optional topic focus filter")
    custom_title: Optional[str] = Field(None, description="Optional custom title")


class FolderStudyGuideRequest(BaseModel):
    guide_mode: GuideMode = Field("comprehensive", description="comprehensive, condensed, or exam_review")
    include_subfolders: bool = Field(False, description="Whether to include nested subfolders")
    target_sections: Optional[List[str]] = Field(None, description="Specific sections to include")
    topic_query: Optional[str] = Field(None, description="Optional topic focus filter")
    custom_title: Optional[str] = Field(None, description="Optional custom title")


class UpdateStudyGuideRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = None


class StudyGuideSectionDetail(BaseModel):
    id: str
    study_guide_id: str
    section_type: str
    title: str
    content_markdown: str
    structured_content: List[Dict[str, Any]] = Field(default_factory=list)
    source_references: List[Dict[str, Any]] = Field(default_factory=list)
    position: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StudyGuideSourceDocInfo(BaseModel):
    document_id: str
    filename: Optional[str] = None


class StudyGuideSummary(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    guide_mode: str
    status: str
    scope_type: str
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    document_id: Optional[str] = None
    document_filename: Optional[str] = None
    include_subfolders: bool = False
    source_document_count: int = 0
    section_count: int = 0
    target_sections: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StudyGuideDetail(StudyGuideSummary):
    sections: List[StudyGuideSectionDetail] = Field(default_factory=list)
    source_documents: List[StudyGuideSourceDocInfo] = Field(default_factory=list)
    generation_metrics: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)
