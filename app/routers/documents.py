"""
routers/documents.py — Phase 8 OCR and Advanced Document Processing Endpoints
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.database.session import SessionLocal
from app.database.types import normalize_json_value
from app.dependencies import CurrentUser, get_current_user
from app.schemas.study_guides import GenerateStudyGuideRequest, GuideMode, StudyGuideDetail
from app.services.study_guide_generation_service import generate_study_guide_workflow
from app.schemas.documents import (
    DeleteResponse, DocumentDetail, DocumentListResponse, DocumentPageListResponse,
    DocumentPageResponse, DocumentSummary, ProcessingRunListResponse, ProcessingRunResponse,
    ReprocessRequest, ReprocessResponse, UploadResponse,
)
from app.schemas.folders import (
    BulkActionResponse, BulkDeleteRequest, BulkFavoriteRequest, BulkMoveRequest,
    BulkPermanentDeleteRequest, BulkRestoreRequest, DocumentMoveRequest,
)
from app.services import document_service
from app.services.document_indexing import delete_document as chroma_delete, index_document
from app.services.document_reprocessing import reprocess_document
from app.services.hybrid_extraction import extract_document_hybrid, HybridExtractionResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_file_type(filename: str, content_type: str, settings) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "doc":
        raise HTTPException(415, "Legacy .doc files are not supported. Please convert to .docx.")
    if ext not in settings.allowed_extensions:
        raise HTTPException(415, f"Unsupported file type '.{ext}'. Allowed: {settings.allowed_extensions}")
    if ext == "pdf" and content_type and "pdf" not in content_type:
        generic_types = ("application/octet-stream", "binary/octet-stream", "application/x-download", "")
        if content_type.lower() not in generic_types:
            raise HTTPException(415, "File extension says .pdf but MIME type disagrees.")
    return ext


def _validate_size(size_bytes: int, max_bytes: int) -> None:
    if size_bytes > max_bytes:
        raise HTTPException(413, f"File too large. Max: {max_bytes // (1024 * 1024)} MB.")


# ---------------------------------------------------------------------------
# POST /documents/upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    settings = request.app.state.settings
    collection = request.app.state.collection

    file_type = _get_file_type(file.filename or "", file.content_type or "", settings)
    file_bytes = await file.read()
    _validate_size(len(file_bytes), settings.max_file_size_bytes)
    if not file_bytes:
        raise HTTPException(400, "Uploaded file is empty.")

    original_filename = file.filename or "unknown"
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    document_id = str(uuid4())

    # 1. Save file to durable local storage (§28)
    from app.services.durable_storage import store_document_file, durable_relative_path
    store_document_file(
        settings.document_storage_root,
        current_user.id,
        document_id,
        file_type,
        file_bytes,
    )
    rel_path = durable_relative_path(
        settings.document_storage_root,
        current_user.id,
        document_id,
        file_type,
    )

    # 2. Extract & index document
    def _process() -> Tuple[str, int, HybridExtractionResult]:
        db = SessionLocal()
        try:
            doc = document_service.create_processing(
                db, document_id, original_filename, file_type, len(file_bytes),
                content_hash, user_id=current_user.id, folder_id=folder_id,
            )
            doc.durable_file_path = rel_path
            db.commit()

            # Create PENDING/QUEUED job record for background audit
            from app.services import job_service
            job, _ = job_service.create_job(
                db,
                user_id=current_user.id,
                job_type="document_ingestion",
                resource_type="document",
                resource_id=document_id,
                payload={"document_id": document_id, "original_filename": original_filename, "file_type": file_type},
            )
            job_service.mark_running(db, job.id, "api-inline")

            # Extract & Index
            from app.services.durable_storage import resolve_durable_path
            fpath = resolve_durable_path(settings.document_storage_root, rel_path)
            extracted = extract_document_hybrid(fpath, file_type)
            doc_id, chunk_count = index_document(
                collection, extracted, original_filename, file_type,
                settings.chunk_size, settings.chunk_overlap,
                document_id=document_id, user_id=current_user.id,
            )

            document_service.mark_ready_with_diagnostics(db, document_id, chunk_count, extracted)
            job_service.mark_succeeded(db, job.id, result_type="document", result_id=document_id)

            try:
                from app.services.study_activity_service import log_document_uploaded
                log_document_uploaded(db, document_id)
                db.commit()
            except Exception as e:
                logger.error("Failed to log document_uploaded activity: %s", e)

            return doc_id, chunk_count, extracted

        except Exception as exc:
            db = SessionLocal()
            document_service.mark_failed(db, document_id)
            db.close()
            raise
        finally:
            db.close()

    try:
        doc_id, chunk_count, extracted = await run_in_threadpool(_process)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    except Exception as exc:
        import traceback
        logger.error("Unexpected upload error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(500, f"Unexpected error: {exc}")

    return UploadResponse(
        document_id=doc_id,
        filename=original_filename,
        file_type=file_type,
        chunk_count=chunk_count,
        status="ready" if extracted.extraction_status == "READY" else "partial",
        extraction_method=extracted.extraction_method,
        extraction_status=extracted.extraction_status,
        quality_score=extracted.quality_score,
        quality_label=extracted.quality_label,
        warnings=extracted.warnings,
    )


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    folder_id: Optional[str] = Query(default=None),
    root_only: bool = Query(default=False),
    favorite: Optional[bool] = Query(default=None),
    trash: bool = Query(default=False),
    sort: str = Query(default="created_at"),
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = document_service.list_documents(
                db, page=page, page_size=page_size,
                status_filter=status, filename_search=search,
                folder_id=folder_id, root_only=root_only,
                is_favorite=favorite, is_trash=trash,
                sort_by=sort,
            )
            summaries = []
            for doc in items:
                quiz_count = document_service.get_quiz_count(db, doc.id)
                latest = document_service.get_latest_quiz_date(db, doc.id)
                summaries.append(
                    DocumentSummary(
                        document_id=doc.id,
                        filename=doc.original_filename,
                        file_type=doc.file_type,
                        file_size=doc.file_size,
                        chunk_count=doc.chunk_count,
                        status=doc.status,
                        created_at=doc.created_at,
                        quiz_count=quiz_count,
                        latest_quiz_at=latest,
                        folder_id=doc.folder_id,
                        is_favorite=doc.is_favorite,
                        last_accessed_at=doc.last_accessed_at,
                        extraction_method=doc.extraction_method or "native",
                        extraction_status=doc.extraction_status or doc.status,
                        quality_label=doc.extraction_quality_label or "excellent",
                        total_pages=doc.total_pages or 1,
                    )
                )
            return summaries, total
        finally:
            db.close()

    summaries, total = await run_in_threadpool(_query)
    return DocumentListResponse(items=summaries, page=page, page_size=page_size, total=total)


# ---------------------------------------------------------------------------
# GET /documents/{document_id}
# ---------------------------------------------------------------------------

@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")
            # Record meaningful access timestamp
            document_service.record_access(db, document_id, current_user.id)

            quiz_count = document_service.get_quiz_count(db, doc.id)
            latest = document_service.get_latest_quiz_date(db, doc.id)
            warnings = []
            if doc.extraction_warnings_json:
                try:
                    warnings = normalize_json_value(doc.extraction_warnings_json, location=f"documents/{doc.id}/extraction_warnings_json")
                except Exception:
                    pass
            return doc, quiz_count, latest, warnings
        finally:
            db.close()

    doc, quiz_count, latest, warnings = await run_in_threadpool(_query)
    return DocumentDetail(
        document_id=doc.id,
        filename=doc.original_filename,
        file_type=doc.file_type,
        file_size=doc.file_size,
        chunk_count=doc.chunk_count,
        status=doc.status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        deleted_at=doc.deleted_at,
        quiz_count=quiz_count,
        latest_quiz_at=latest,
        folder_id=doc.folder_id,
        is_favorite=doc.is_favorite,
        last_accessed_at=doc.last_accessed_at,
        extraction_method=doc.extraction_method or "native",
        extraction_status=doc.extraction_status or doc.status,
        quality_label=doc.extraction_quality_label or "excellent",
        total_pages=doc.total_pages or 1,
        quality_score=doc.extraction_quality_score or 1.0,
        warnings=warnings,
        native_page_count=doc.native_page_count or 1,
        ocr_page_count=doc.ocr_page_count or 0,
        failed_page_count=doc.failed_page_count or 0,
        empty_page_count=doc.empty_page_count or 0,
        extractor_version=doc.extractor_version or "2.0.0",
        ocr_engine=doc.ocr_engine or "none",
        processed_at=doc.processed_at,
    )


# ---------------------------------------------------------------------------
# Move & Bulk Operations
# ---------------------------------------------------------------------------

@router.patch("/{document_id}/move", response_model=DocumentSummary)
async def move_document_endpoint(
    document_id: str,
    payload: DocumentMoveRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _move():
        db = SessionLocal()
        try:
            doc = document_service.move_document(
                db, document_id=document_id, user_id=current_user.id, target_folder_id=payload.folder_id
            )
            quiz_count = document_service.get_quiz_count(db, doc.id)
            latest = document_service.get_latest_quiz_date(db, doc.id)
            return DocumentSummary(
                document_id=doc.id,
                filename=doc.original_filename,
                file_type=doc.file_type,
                file_size=doc.file_size,
                chunk_count=doc.chunk_count,
                status=doc.status,
                created_at=doc.created_at,
                quiz_count=quiz_count,
                latest_quiz_at=latest,
                folder_id=doc.folder_id,
                is_favorite=doc.is_favorite,
                last_accessed_at=doc.last_accessed_at,
                extraction_method=doc.extraction_method or "native",
                extraction_status=doc.extraction_status or doc.status,
                quality_label=doc.extraction_quality_label or "excellent",
                total_pages=doc.total_pages or 1,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_move)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error moving document: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not move document: {exc}")


@router.post("/bulk/move", response_model=BulkActionResponse)
async def bulk_move_endpoint(
    payload: BulkMoveRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _bulk_move():
        db = SessionLocal()
        try:
            count = document_service.bulk_move_documents(
                db,
                document_ids=payload.document_ids,
                user_id=current_user.id,
                target_folder_id=payload.folder_id,
            )
            return BulkActionResponse(affected_count=count)
        finally:
            db.close()

    try:
        return await run_in_threadpool(_bulk_move)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error bulk moving documents: %s", exc)
        raise HTTPException(status_code=500, detail=f"Bulk move failed: {exc}")


@router.post("/{document_id}/favorite", response_model=DocumentSummary)
async def favorite_document_endpoint(
    document_id: str,
    favorite: Optional[bool] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    def _fav():
        db = SessionLocal()
        try:
            doc = document_service.toggle_favorite(
                db, document_id=document_id, user_id=current_user.id, is_favorite=favorite
            )
            quiz_count = document_service.get_quiz_count(db, doc.id)
            latest = document_service.get_latest_quiz_date(db, doc.id)
            return DocumentSummary(
                document_id=doc.id,
                filename=doc.original_filename,
                file_type=doc.file_type,
                file_size=doc.file_size,
                chunk_count=doc.chunk_count,
                status=doc.status,
                created_at=doc.created_at,
                quiz_count=quiz_count,
                latest_quiz_at=latest,
                folder_id=doc.folder_id,
                is_favorite=doc.is_favorite,
                last_accessed_at=doc.last_accessed_at,
                extraction_method=doc.extraction_method or "native",
                extraction_status=doc.extraction_status or doc.status,
                quality_label=doc.extraction_quality_label or "excellent",
                total_pages=doc.total_pages or 1,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_fav)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/bulk/favorite", response_model=BulkActionResponse)
async def bulk_favorite_endpoint(
    payload: BulkFavoriteRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _bulk_fav():
        db = SessionLocal()
        try:
            count = document_service.bulk_favorite(
                db, document_ids=payload.document_ids, user_id=current_user.id, is_favorite=payload.is_favorite
            )
            return BulkActionResponse(affected_count=count)
        finally:
            db.close()

    return await run_in_threadpool(_bulk_fav)


@router.post("/bulk/delete", response_model=BulkActionResponse)
async def bulk_delete_endpoint(
    payload: BulkDeleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _bulk_del():
        db = SessionLocal()
        try:
            count = 0
            for doc_id in payload.document_ids:
                try:
                    document_service.soft_delete(db, doc_id)
                    count += 1
                except Exception:
                    pass
            return BulkActionResponse(affected_count=count)
        finally:
            db.close()

    return await run_in_threadpool(_bulk_del)


@router.post("/{document_id}/restore", response_model=DocumentSummary)
async def restore_document_endpoint(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _restore():
        db = SessionLocal()
        try:
            doc = document_service.restore_document(db, document_id, current_user.id)
            quiz_count = document_service.get_quiz_count(db, doc.id)
            latest = document_service.get_latest_quiz_date(db, doc.id)
            return DocumentSummary(
                document_id=doc.id,
                filename=doc.original_filename,
                file_type=doc.file_type,
                file_size=doc.file_size,
                chunk_count=doc.chunk_count,
                status=doc.status,
                created_at=doc.created_at,
                quiz_count=quiz_count,
                latest_quiz_at=latest,
                folder_id=doc.folder_id,
                is_favorite=doc.is_favorite,
                last_accessed_at=doc.last_accessed_at,
                extraction_method=doc.extraction_method or "native",
                extraction_status=doc.extraction_status or doc.status,
                quality_label=doc.extraction_quality_label or "excellent",
                total_pages=doc.total_pages or 1,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_restore)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/bulk/restore", response_model=BulkActionResponse)
async def bulk_restore_endpoint(
    payload: BulkRestoreRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _bulk_restore():
        db = SessionLocal()
        try:
            count = document_service.bulk_restore_documents(
                db, document_ids=payload.document_ids, user_id=current_user.id
            )
            return BulkActionResponse(affected_count=count)
        finally:
            db.close()

    return await run_in_threadpool(_bulk_restore)


@router.delete("/{document_id}/permanent", response_model=DeleteResponse)
async def permanent_delete_endpoint(
    document_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    collection = request.app.state.collection

    def _perm_delete():
        db = SessionLocal()
        try:
            chunks_deleted = chroma_delete(collection, document_id, current_user.id)
            document_service.permanent_delete_document(db, document_id, current_user.id)
            return chunks_deleted
        finally:
            db.close()

    try:
        chunks_deleted = await run_in_threadpool(_perm_delete)
        return DeleteResponse(document_id=document_id, chunks_deleted=chunks_deleted, status="permanently_deleted")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/bulk/permanent-delete", response_model=BulkActionResponse)
async def bulk_permanent_delete_endpoint(
    payload: BulkPermanentDeleteRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    collection = request.app.state.collection

    def _bulk_perm():
        db = SessionLocal()
        try:
            count = 0
            for doc_id in payload.document_ids:
                try:
                    chroma_delete(collection, doc_id, current_user.id)
                    document_service.permanent_delete_document(db, doc_id, current_user.id)
                    count += 1
                except Exception:
                    pass
            return BulkActionResponse(affected_count=count)
        finally:
            db.close()

    return await run_in_threadpool(_bulk_perm)


# ---------------------------------------------------------------------------
# POST /documents/{document_id}/reprocess
# ---------------------------------------------------------------------------

@router.post("/{document_id}/reprocess", response_model=ReprocessResponse)
async def reprocess_document_endpoint(
    document_id: str,
    reprocess_req: ReprocessRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    collection = request.app.state.collection
    settings = request.app.state.settings

    def _reprocess():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")

            updated_doc, ext_res, chunk_count = reprocess_document(
                db, collection, doc, mode=reprocess_req.mode,
                chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap,
            )

            return ReprocessResponse(
                document_id=updated_doc.id,
                status=updated_doc.status,
                extraction_method=ext_res.extraction_method,
                quality_score=ext_res.quality_score,
                quality_label=ext_res.quality_label,
                chunk_count=chunk_count,
                warnings=ext_res.warnings,
            )
        finally:
            db.close()

    try:
        return await run_in_threadpool(_reprocess)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Reprocessing error: {exc}")


# ---------------------------------------------------------------------------
# GET /documents/{document_id}/processing
# ---------------------------------------------------------------------------

@router.get("/{document_id}/processing")
async def get_document_processing_diagnostics(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")
            runs = document_service.get_processing_runs(db, document_id)
            res_runs = []
            for r in runs:
                warnings = []
                if r.warnings_json:
                    try:
                        warnings = normalize_json_value(r.warnings_json, location=f"document_processing_runs/{r.id}/warnings_json")
                    except Exception:
                        pass
                res_runs.append(
                    {
                        "run_id": r.id,
                        "document_id": r.document_id,
                        "status": r.status,
                        "extraction_method": r.extraction_method,
                        "extractor_version": r.extractor_version,
                        "ocr_engine": r.ocr_engine,
                        "started_at": r.started_at,
                        "completed_at": r.completed_at,
                        "total_pages": r.total_pages,
                        "native_page_count": r.native_page_count,
                        "ocr_page_count": r.ocr_page_count,
                        "failed_page_count": r.failed_page_count,
                        "empty_page_count": r.empty_page_count,
                        "quality_score": r.quality_score,
                        "quality_label": r.quality_label,
                        "warnings": warnings,
                        "error_code": r.error_code,
                    }
                )
            return {"document_id": document_id, "runs": res_runs}
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# GET /documents/{document_id}/pages
# ---------------------------------------------------------------------------

@router.get("/{document_id}/pages")
async def get_document_page_diagnostics(
    document_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")
            pages = document_service.get_document_pages(db, document_id)

            collection = getattr(request.app.state, "collection", None)
            page_text_map: Dict[int, List[Tuple[int, str]]] = {}
            all_chunks: List[Tuple[int, str, Optional[str]]] = []

            if collection is not None:
                try:
                    where_filter: Dict[str, Any] = {"document_id": document_id}
                    chroma_res = collection.get(
                        where=where_filter,
                        include=["documents", "metadatas"],
                    )
                    docs_list = chroma_res.get("documents") or []
                    metas_list = chroma_res.get("metadatas") or []
                    for chunk_txt, meta in zip(docs_list, metas_list):
                        if not chunk_txt:
                            continue
                        c_idx = int(meta.get("chunk_index", 0))
                        p_num = meta.get("page_number")
                        sec_label = meta.get("section_title") or meta.get("section_path")
                        p_val = int(p_num) if isinstance(p_num, (int, float)) and int(p_num) > 0 else 1
                        page_text_map.setdefault(p_val, []).append((c_idx, str(chunk_txt)))
                        all_chunks.append((c_idx, str(chunk_txt), sec_label))
                except Exception as exc:
                    logger.warning("Could not fetch Chroma chunks for reader: %s", exc)

            res_pages = []
            if pages:
                for p in pages:
                    chunks_for_page = sorted(page_text_map.get(p.page_number, []), key=lambda x: x[0])
                    p_content = "\n\n".join(t for _, t in chunks_for_page) if chunks_for_page else ""
                    res_pages.append(
                        {
                            "page_number": p.page_number,
                            "extraction_method": p.extraction_method,
                            "status": p.status,
                            "character_count": p.character_count or len(p_content),
                            "quality_score": p.quality_score,
                            "section_label": p.section_label,
                            "content": p_content,
                        }
                    )
            elif all_chunks:
                all_chunks.sort(key=lambda x: x[0])
                chunks_per_virtual_page = 3
                for v_idx in range(0, len(all_chunks), chunks_per_virtual_page):
                    slice_chunks = all_chunks[v_idx : v_idx + chunks_per_virtual_page]
                    v_content = "\n\n".join(t for _, t, _ in slice_chunks)
                    v_page_num = (v_idx // chunks_per_virtual_page) + 1
                    first_sec = slice_chunks[0][2] if slice_chunks else None
                    res_pages.append(
                        {
                            "page_number": v_page_num,
                            "extraction_method": doc.extraction_method or "native",
                            "status": "OK",
                            "character_count": len(v_content),
                            "quality_score": float(doc.quality_score or 1.0),
                            "section_label": first_sec or f"Section {v_page_num}",
                            "content": v_content,
                        }
                    )

            return {
                "document_id": document_id,
                "pages": res_pages,
                "total_pages": len(res_pages) if res_pages else 1,
            }
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# DELETE /documents/{document_id}
# ---------------------------------------------------------------------------

@router.delete("/{document_id}", response_model=DeleteResponse)
async def delete_document_endpoint(
    document_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    collection = request.app.state.collection

    def _delete():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")
            chunks_deleted = chroma_delete(collection, document_id, current_user.id)
            document_service.soft_delete(db, document_id)
            return chunks_deleted
        finally:
            db.close()

    chunks_deleted = await run_in_threadpool(_delete)
    return DeleteResponse(document_id=document_id, chunks_deleted=chunks_deleted, status="deleted")


# ---------------------------------------------------------------------------
# GET /documents/{document_id}/study
# ---------------------------------------------------------------------------

@router.get("/{document_id}/study")
async def get_document_study_analytics(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")

            from app.services import topic_performance_service, review_item_service
            topics = topic_performance_service.list_topic_performance(
                db, document_id=document_id, limit=100
            )
            weak_topics = [
                {
                    "topic_label": t.topic_label,
                    "accuracy_percentage": t.accuracy_percentage,
                    "mastery_state": t.mastery_state,
                    "attempted_count": t.attempted_count,
                }
                for t in topics if t.mastery_state in ("weak", "developing")
            ]

            due_items = review_item_service.get_due_review_items(db, document_id=document_id, limit=500)
            due_count = len(due_items)

            avg_mastery = (
                round(sum(t.mastery_score for t in topics) / len(topics), 1)
                if topics else 0.0
            )

            return {
                "document_id": document_id,
                "filename": doc.original_filename,
                "weak_topics": weak_topics,
                "due_review_count": due_count,
                "average_mastery": avg_mastery,
                "topic_count": len(topics),
            }
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# POST /documents/{document_id}/analysis
# ---------------------------------------------------------------------------

class DocumentAnalysisRequest(BaseModel):
    guide_mode: GuideMode = Field("condensed", description="condensed (Summary & Key Points) or comprehensive (In-Depth Analysis)")
    custom_title: Optional[str] = Field(None, description="Optional custom title")
    topic_query: Optional[str] = Field(None, description="Optional topic focus filter")
    target_sections: Optional[List[str]] = Field(None, description="Specific sections to include")


@router.post(
    "/{document_id}/analysis",
    response_model=StudyGuideDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Generate Document Analysis (Summary & Key Points / In-Depth Analysis)",
)
async def generate_document_analysis(
    document_id: str,
    request: Request,
    analysis_req: Optional[DocumentAnalysisRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Generate document analysis (Summary & Key Points or In-Depth Analysis) for a single document.
    Reuses the existing document-analysis/Study Guide workflow, enforcing document ownership.
    """
    def _run():
        db = SessionLocal()
        try:
            doc = document_service.get_by_id(db, document_id)
            if not doc:
                raise HTTPException(404, f"Document '{document_id}' not found.")
            if doc.user_id and doc.user_id != current_user.id:
                raise HTTPException(403, "Access denied to this document.")

            collection = getattr(request.app.state, "collection", None)
            gemini_model = getattr(request.app.state, "gemini_model", None)

            mode = analysis_req.guide_mode if analysis_req else "condensed"
            custom_title = analysis_req.custom_title if analysis_req else None
            topic_query = analysis_req.topic_query if analysis_req else None
            target_sections = analysis_req.target_sections if analysis_req else None

            guide_req = GenerateStudyGuideRequest(
                document_id=document_id,
                guide_mode=mode,
                scope_type="DOCUMENT",
                custom_title=custom_title,
                topic_query=topic_query,
                target_sections=target_sections,
            )

            return generate_study_guide_workflow(
                db=db,
                collection=collection,
                gemini_model=gemini_model,
                req=guide_req,
                user_id=current_user.id,
            )
        finally:
            db.close()

    return await run_in_threadpool(_run)

