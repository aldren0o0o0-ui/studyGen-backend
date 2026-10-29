"""
app/routers/study_guides.py — Wave 5 API Endpoints for Study Guides & Synthesized Reviewers.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.dependencies import get_current_user, get_db
from app.schemas.study_guides import (
    GenerateStudyGuideRequest,
    StudyGuideDetail,
    StudyGuideSummary,
    UpdateStudyGuideRequest,
)
from app.services.study_guide_generation_service import generate_study_guide_workflow
from app.services.study_guide_service import (
    delete_study_guide,
    get_study_guide,
    list_study_guides,
    update_study_guide,
)

router = APIRouter(prefix="/study-guides", tags=["study_guides"])


@router.post("/generate", response_model=StudyGuideDetail, status_code=status.HTTP_201_CREATED)
def generate_study_guide_endpoint(
    req: GenerateStudyGuideRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generates a structured, source-aware study guide from document(s) or folder scope."""
    collection = getattr(request.app.state, "collection", None)
    gemini_model = getattr(request.app.state, "gemini_model", None)

    return generate_study_guide_workflow(
        db=db,
        collection=collection,
        gemini_model=gemini_model,
        req=req,
        user_id=current_user.id,
    )


@router.get("", response_model=dict)
def list_study_guides_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    folder_id: Optional[str] = None,
    document_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    guide_mode: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists study guides for the authenticated user."""
    items, total = list_study_guides(
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        folder_id=folder_id,
        document_id=document_id,
        scope_type=scope_type,
        guide_mode=guide_mode,
        search=search,
    )
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{study_guide_id}", response_model=StudyGuideDetail)
def get_study_guide_endpoint(
    study_guide_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves full detail of a study guide with sections and source documents."""
    return get_study_guide(db=db, guide_id=study_guide_id, user_id=current_user.id)


@router.patch("/{study_guide_id}", response_model=StudyGuideDetail)
def update_study_guide_endpoint(
    study_guide_id: str,
    req: UpdateStudyGuideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Updates title or description of a study guide."""
    return update_study_guide(
        db=db,
        guide_id=study_guide_id,
        user_id=current_user.id,
        req=req,
    )


@router.delete("/{study_guide_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_study_guide_endpoint(
    study_guide_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes a study guide."""
    delete_study_guide(db=db, guide_id=study_guide_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
