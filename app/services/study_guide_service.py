"""
app/services/study_guide_service.py — Wave 5 Study Guide Query & Management Service
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.database.models.document import Document
from app.database.models.folder import Folder
from app.database.models.study_guide import StudyGuide, StudyGuideSection, StudyGuideSourceDocument
from app.schemas.study_guides import (
    StudyGuideDetail,
    StudyGuideSummary,
    UpdateStudyGuideRequest,
)
from app.services.study_guide_generation_service import get_study_guide_detail

logger = logging.getLogger(__name__)


def list_study_guides(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    folder_id: Optional[str] = None,
    document_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    guide_mode: Optional[str] = None,
    search: Optional[str] = None,
) -> Tuple[List[StudyGuideSummary], int]:
    """Lists study guides for a user with filtering and pagination."""
    query = (
        db.query(StudyGuide)
        .options(
            selectinload(StudyGuide.sections),
            selectinload(StudyGuide.source_documents),
            selectinload(StudyGuide.document),
            selectinload(StudyGuide.folder),
        )
        .filter(StudyGuide.user_id == user_id)
    )

    if folder_id is not None:
        query = query.filter(StudyGuide.folder_id == folder_id)
    if document_id is not None:
        query = query.filter(StudyGuide.document_id == document_id)
    if scope_type is not None:
        query = query.filter(StudyGuide.scope_type == scope_type.upper())
    if guide_mode is not None:
        query = query.filter(StudyGuide.guide_mode == guide_mode.lower())
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(or_(StudyGuide.title.ilike(s), StudyGuide.description.ilike(s)))

    total = query.count()
    items_db = (
        query.order_by(StudyGuide.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    summaries: List[StudyGuideSummary] = []
    for g in items_db:
        folder_name = g.folder.name if g.folder else None
        doc_filename = g.document.original_filename if g.document else None
        summaries.append(
            StudyGuideSummary(
                id=g.id,
                title=g.title,
                description=g.description,
                guide_mode=g.guide_mode,
                status=g.status,
                scope_type=g.scope_type,
                folder_id=g.folder_id,
                folder_name=folder_name,
                document_id=g.document_id,
                document_filename=doc_filename,
                include_subfolders=g.include_subfolders,
                source_document_count=len(g.source_documents),
                section_count=len(g.sections),
                target_sections=g.target_sections_json or [],
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
        )

    return summaries, total


def get_study_guide(db: Session, guide_id: str, user_id: str) -> StudyGuideDetail:
    """Gets complete detail of a study guide."""
    return get_study_guide_detail(db, guide_id, user_id)


def update_study_guide(
    db: Session,
    guide_id: str,
    user_id: str,
    req: UpdateStudyGuideRequest,
) -> StudyGuideDetail:
    """Updates title or description of a study guide."""
    guide = db.query(StudyGuide).filter(StudyGuide.id == guide_id, StudyGuide.user_id == user_id).first()
    if not guide:
        raise HTTPException(404, "Study guide not found.")

    if req.title is not None:
        guide.title = req.title.strip()
    if req.description is not None:
        guide.description = req.description.strip()

    db.commit()
    db.refresh(guide)
    return get_study_guide_detail(db, guide_id, user_id)


def delete_study_guide(db: Session, guide_id: str, user_id: str) -> None:
    """Deletes a study guide and its sections."""
    guide = db.query(StudyGuide).filter(StudyGuide.id == guide_id, StudyGuide.user_id == user_id).first()
    if not guide:
        raise HTTPException(404, "Study guide not found.")

    db.delete(guide)
    db.commit()
