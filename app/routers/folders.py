"""
routers/folders.py — Folder Management Endpoints for File Workspace
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.database.session import SessionLocal
from sqlalchemy.orm import Session
from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.folders import (
    FolderBreadcrumb,
    FolderCreate,
    FolderDetail,
    FolderListResponse,
    FolderSummary,
    FolderUpdate,
)
from app.schemas.quizzes import FolderQuizRequest, GenerateQuizRequest
from app.schemas.flashcards import FolderFlashcardRequest, GenerateFlashcardsRequest
from app.schemas.study_guides import FolderStudyGuideRequest, GenerateStudyGuideRequest
from app.services import folder_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/folders", tags=["folders"], dependencies=[Depends(get_current_user)])


@router.post("", response_model=FolderSummary, status_code=201)
async def create_folder(
    payload: FolderCreate,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _create():
        db = SessionLocal()
        try:
            folder = folder_service.create_folder(
                db,
                user_id=current_user.id,
                name=payload.name,
                parent_folder_id=payload.parent_folder_id,
            )
            return FolderSummary(
                id=folder.id,
                name=folder.name,
                parent_folder_id=folder.parent_folder_id,
                document_count=0,
                subfolder_count=0,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_create)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error creating folder: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}")


@router.get("", response_model=FolderListResponse)
async def list_folders(
    parent_id: Optional[str] = Query(None, description="Parent folder ID or omit for root folders"),
    all_levels: bool = Query(False, description="Whether to list all folders recursively"),
    current_user: CurrentUser = Depends(get_current_user),
):
    def _list():
        db = SessionLocal()
        try:
            results = folder_service.list_folders(
                db,
                user_id=current_user.id,
                parent_folder_id=parent_id,
                all_levels=all_levels,
            )
            items = [
                FolderSummary(
                    id=f.id,
                    name=f.name,
                    parent_folder_id=f.parent_folder_id,
                    document_count=doc_count,
                    subfolder_count=sub_count,
                    created_at=f.created_at,
                    updated_at=f.updated_at,
                )
                for f, doc_count, sub_count in results
            ]
            return FolderListResponse(items=items, total=len(items))
        finally:
            db.close()

    return await run_in_threadpool(_list)


@router.get("/{folder_id}", response_model=FolderDetail)
async def get_folder(
    folder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _get():
        db = SessionLocal()
        try:
            folder = folder_service.get_folder(db, folder_id, current_user.id)
            if not folder:
                raise HTTPException(status_code=404, detail=f"Folder '{folder_id}' not found.")

            # Calculate counts
            all_folders = folder_service.list_folders(
                db, user_id=current_user.id, parent_folder_id=folder.parent_folder_id, all_levels=True
            )
            doc_count = 0
            sub_count = 0
            for f, d_cnt, s_cnt in all_folders:
                if f.id == folder.id:
                    doc_count = d_cnt
                    sub_count = s_cnt
                    break

            raw_crumbs = folder_service.get_breadcrumbs(db, folder.id, current_user.id)
            breadcrumbs = [FolderBreadcrumb(id=c["id"], name=c["name"]) for c in raw_crumbs]

            return FolderDetail(
                id=folder.id,
                name=folder.name,
                parent_folder_id=folder.parent_folder_id,
                document_count=doc_count,
                subfolder_count=sub_count,
                breadcrumbs=breadcrumbs,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        finally:
            db.close()

    return await run_in_threadpool(_get)


@router.patch("/{folder_id}", response_model=FolderSummary)
async def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _update():
        db = SessionLocal()
        try:
            folder = folder_service.update_folder(
                db,
                folder_id=folder_id,
                user_id=current_user.id,
                name=payload.name,
                parent_folder_id=payload.parent_folder_id,
            )
            return FolderSummary(
                id=folder.id,
                name=folder.name,
                parent_folder_id=folder.parent_folder_id,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_update)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error updating folder: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not update folder: {exc}")


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _delete():
        db = SessionLocal()
        try:
            relocated = folder_service.delete_folder(db, folder_id, current_user.id)
            return {
                "folder_id": folder_id,
                "documents_relocated": relocated,
                "status": "deleted",
            }
        finally:
            db.close()

    try:
        return await run_in_threadpool(_delete)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Error deleting folder: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not delete folder: {exc}")


class FolderChatRequest(BaseModel):
    include_subfolders: bool = False


@router.post("/{folder_id}/chat")
async def create_folder_chat_session(
    folder_id: str,
    payload: Optional[FolderChatRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Start an AI Tutor chat session grounded across all ready documents in this folder (or folder + subfolders)."""
    include_subfolders = payload.include_subfolders if payload else False

    def _create_chat():
        db = SessionLocal()
        try:
            from app.schemas.ai_scope import AIScopeRequest, AIScopeType
            from app.services import ai_scope_service, chat_service

            scope_req = AIScopeRequest(
                scope_type=AIScopeType.FOLDER,
                folder_id=folder_id,
                include_subfolders=include_subfolders,
            )
            resolved = ai_scope_service.resolve_ai_scope(db, current_user.id, scope_req)

            session = chat_service.create_session(
                db,
                document_ids=resolved.document_ids,
                title=f"Folder: {resolved.folder_name or 'Folder'}",
                system_mode="document_qa",
                max_documents=50,
                scope_type="folder",
                folder_id=folder_id,
                include_subfolders=include_subfolders,
            )
            return {
                "session_id": session.session_id,
                "title": session.title,
                "folder_id": folder_id,
                "folder_name": resolved.folder_name,
                "include_subfolders": include_subfolders,
                "document_count": resolved.document_count,
                "folder_count": resolved.folder_count,
                "documents": [
                    {"document_id": d.document_id, "filename": d.filename}
                    for d in resolved.documents
                ],
                "created_at": session.created_at,
            }
        except ai_scope_service.AIScopeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        finally:
            db.close()

    try:
        return await run_in_threadpool(_create_chat)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error starting folder chat: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not start folder chat: {exc}")


@router.post("/{folder_id}/quiz")
async def create_folder_quiz(
    folder_id: str,
    request: Request,
    payload: Optional[FolderQuizRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate a quiz grounded across all ready documents in this folder (or folder + subfolders)."""
    from app.routers.quizzes import generate_quiz_endpoint

    p = payload or FolderQuizRequest()
    req = GenerateQuizRequest(
        scope_type="FOLDER",
        folder_id=folder_id,
        include_subfolders=p.include_subfolders,
        question_count=p.question_count,
        difficulty=p.difficulty,
        question_types=p.question_types,
        coverage_mode=p.coverage_mode,
        topic_query=p.topic_query,
        include_explanations=p.include_explanations,
        include_sources=p.include_sources,
    )
    return await generate_quiz_endpoint(req, request, current_user=current_user)


@router.post("/{folder_id}/flashcards", status_code=201)
async def create_folder_flashcards(
    folder_id: str,
    request: Request,
    payload: Optional[FolderFlashcardRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate flashcards grounded across all ready documents in this folder (or folder + subfolders)."""
    from app.routers.flashcards import generate_flashcards_endpoint

    p = payload or FolderFlashcardRequest()
    req = GenerateFlashcardsRequest(
        scope_type="FOLDER",
        folder_id=folder_id,
        include_subfolders=p.include_subfolders,
        card_count=p.card_count,
        difficulty=p.difficulty,
        card_types=p.card_types,
        focus_mode=p.focus_mode,
        coverage_mode=p.focus_mode,
        topic_query=p.topic_query,
        include_hints=p.include_hints,
        include_sources=p.include_sources,
    )
    return await generate_flashcards_endpoint(req, request, current_user=current_user)


@router.post("/{folder_id}/study-guides", status_code=201)
async def create_folder_study_guide(
    folder_id: str,
    request: Request,
    payload: Optional[FolderStudyGuideRequest] = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate a comprehensive study guide grounded across all ready documents in this folder."""
    from app.schemas.study_guides import FolderStudyGuideRequest, GenerateStudyGuideRequest
    from app.services.study_guide_generation_service import generate_study_guide_workflow

    p = payload or FolderStudyGuideRequest()
    req = GenerateStudyGuideRequest(
        scope_type="FOLDER",
        folder_id=folder_id,
        include_subfolders=p.include_subfolders,
        guide_mode=p.guide_mode,
        target_sections=p.target_sections,
        topic_query=p.topic_query,
        custom_title=p.custom_title,
    )

    collection = getattr(request.app.state, "collection", None)
    gemini_model = getattr(request.app.state, "gemini_model", None)

    def _generate():
        return generate_study_guide_workflow(
            db=db,
            collection=collection,
            gemini_model=gemini_model,
            req=req,
            user_id=current_user.id,
        )

    return await run_in_threadpool(_generate)


