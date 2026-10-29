"""Course and Folder Intelligence AI Scope Router (Wave 1).

Exposes non-mutating preview endpoints for validating and bounding AI context scope.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.dependencies import CurrentUser, get_current_user
from app.schemas.ai_scope import AIScopePreviewResponse, AIScopeRequest
from app.services import ai_scope_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/scope", tags=["AI Scope"])


@router.post("/preview", response_model=AIScopePreviewResponse)
async def preview_ai_scope(
    request: AIScopeRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Preview eligible document counts, subfolder counts, and file lists for an AI scope without side effects."""
    def _preview():
        db = SessionLocal()
        try:
            return ai_scope_service.preview_ai_scope(db, current_user.id, request)
        except ai_scope_service.AIScopeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        except Exception as exc:
            logger.exception("Error previewing AI scope: %s", exc)
            raise HTTPException(status_code=500, detail=f"Could not preview AI scope: {exc}")
        finally:
            db.close()

    return await run_in_threadpool(_preview)
