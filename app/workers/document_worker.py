"""
workers/document_worker.py — Phase 9 Document Ingestion & Reprocessing Worker Tasks
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.database.session import SessionLocal
from app.ownership_context import set_ownership_context
from app.services import document_service, job_service
from app.services.document_indexing import index_document, delete_document as chroma_delete
from app.services.document_reprocessing import reprocess_document
from app.services.durable_storage import resolve_durable_path
from app.services.hybrid_extraction import extract_document_hybrid
from app.services.resource_lock import acquire_resource_lock

logger = logging.getLogger(__name__)


async def run_document_ingestion_task(ctx: dict, job_id: str) -> str:
    worker_id = ctx.get("worker_id", "worker-doc-1")
    db = SessionLocal()
    try:
        job = job_service.mark_running(db, job_id, worker_id)
        if job.status == "CANCELLED":
            return "CANCELLED"

        user_id = job.user_id
        doc_id = job.resource_id
        set_ownership_context(user_id)

        # 1. Acquire resource lock
        if not acquire_resource_lock(db, doc_id, job_id):
            job_service.mark_failed(db, job_id, "RESOURCE_LOCKED", "Document is currently being processed by another job.")
            return "RESOURCE_LOCKED"

        # 2. Check document
        doc = document_service.get_by_id(db, doc_id)
        if not doc or doc.is_deleted:
            job_service.mark_failed(db, job_id, "DOCUMENT_NOT_FOUND", "Document record missing or deleted.")
            return "DOCUMENT_NOT_FOUND"

        # 3. Resolve durable file path
        settings = ctx.get("settings")
        storage_root = settings.document_storage_root if settings else "storage"
        file_path = resolve_durable_path(storage_root, doc.durable_file_path or "") if doc.durable_file_path else None
        
        if not file_path or not file_path.exists():
            job_service.mark_failed(db, job_id, "FILE_NOT_FOUND", f"Durable source file missing at path: {doc.durable_file_path}")
            document_service.mark_failed(db, doc_id)
            return "FILE_NOT_FOUND"

        # Stage 1: Native Extraction & OCR
        job_service.update_progress(db, job_id, "extracting_native_text")
        
        collection = ctx.get("collection")
        
        extracted = extract_document_hybrid(file_path, doc.file_type)
        
        # Check cancellation point 1
        if job_service.check_cancellation(db, job_id):
            job_service.mark_cancelled(db, job_id)
            return "CANCELLED"

        # Stage 2: Chunking & Embedding
        job_service.update_progress(db, job_id, "chunking")
        
        # Check cancellation point 2
        if job_service.check_cancellation(db, job_id):
            job_service.mark_cancelled(db, job_id)
            return "CANCELLED"

        job_service.update_progress(db, job_id, "indexing")
        chunk_count = index_document(
            collection, extracted, doc.original_filename, doc.file_type,
            settings.chunk_size if settings else 1000,
            settings.chunk_overlap if settings else 100,
            document_id=doc_id, user_id=user_id
        )[1]

        # Stage 3: Finalizing
        document_service.mark_ready_with_diagnostics(db, doc_id, chunk_count, extracted)
        job_service.mark_succeeded(db, job_id, result_type="document", result_id=doc_id)
        logger.info("Document ingestion job %s succeeded for doc_id=%s", job_id, doc_id)
        return "SUCCEEDED"

    except Exception as exc:
        logger.error("Document ingestion worker failed for job_id=%s: %s", job_id, exc, exc_info=True)
        job_service.mark_failed(db, job_id, "INGESTION_ERROR", str(exc))
        return "FAILED"
    finally:
        db.close()


async def run_document_reprocessing_task(ctx: dict, job_id: str) -> str:
    worker_id = ctx.get("worker_id", "worker-doc-1")
    db = SessionLocal()
    try:
        job = job_service.mark_running(db, job_id, worker_id)
        if job.status == "CANCELLED":
            return "CANCELLED"

        user_id = job.user_id
        doc_id = job.resource_id
        set_ownership_context(user_id)

        if not acquire_resource_lock(db, doc_id, job_id):
            job_service.mark_failed(db, job_id, "RESOURCE_LOCKED", "Document is currently being reprocessed by another job.")
            return "RESOURCE_LOCKED"

        doc = document_service.get_by_id(db, doc_id)
        if not doc or doc.is_deleted:
            job_service.mark_failed(db, job_id, "DOCUMENT_NOT_FOUND", "Document record missing or deleted.")
            return "DOCUMENT_NOT_FOUND"

        payload = job.payload_json or {}
        mode = payload.get("mode", "auto")

        job_service.update_progress(db, job_id, "extracting_native_text")
        collection = ctx.get("collection")
        settings = ctx.get("settings")

        updated_doc, ext_res, chunk_count = reprocess_document(
            db, collection, doc, mode=mode,
            chunk_size=settings.chunk_size if settings else 1000,
            chunk_overlap=settings.chunk_overlap if settings else 100
        )

        job_service.mark_succeeded(db, job_id, result_type="document", result_id=doc_id)
        logger.info("Document reprocessing job %s succeeded for doc_id=%s", job_id, doc_id)
        return "SUCCEEDED"

    except Exception as exc:
        logger.error("Document reprocessing worker failed for job_id=%s: %s", job_id, exc, exc_info=True)
        job_service.mark_failed(db, job_id, "REPROCESS_ERROR", str(exc))
        return "FAILED"
    finally:
        db.close()
