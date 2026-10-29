"""
services/document_service.py — Phase 8 Document Persistence & Processing Diagnostics
All functions are synchronous — call via run_in_threadpool from async routes.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.document import Document, DocumentProcessingRun, DocumentPage
from app.database.models.quiz import Quiz
from app.services.hybrid_extraction import HybridExtractionResult

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------

def create_processing(
    db: Session,
    document_id: str,
    filename: str,
    file_type: str,
    file_size: int,
    content_hash: str,
    user_id: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> Document:
    """Create a PROCESSING document record before extraction starts."""
    kwargs = {
        "id": document_id,
        "original_filename": filename,
        "file_type": file_type,
        "file_size": file_size,
        "content_hash": content_hash,
        "chunk_count": 0,
        "status": "PROCESSING",
        "extraction_status": "PROCESSING",
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    if user_id:
        kwargs["user_id"] = user_id
    if folder_id:
        kwargs["folder_id"] = folder_id

    doc = Document(**kwargs)
    db.add(doc)

    # Create initial processing run record
    run = DocumentProcessingRun(
        id=str(uuid.uuid4()),
        document_id=document_id,
        status="PROCESSING",
        extraction_method="native",
        started_at=_utcnow(),
        created_at=_utcnow(),
    )
    db.add(run)

    db.commit()
    db.refresh(doc)
    logger.info("Created PROCESSING document id=%s", document_id)
    return doc


def mark_ready_with_diagnostics(
    db: Session,
    document_id: str,
    chunk_count: int,
    extraction_result: Optional[HybridExtractionResult] = None,
) -> Document:
    """Update document with Phase 8 quality metrics & page diagnostics."""
    doc = db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found.")

    status = "READY"
    if extraction_result:
        status = extraction_result.extraction_status
        doc.extraction_method = extraction_result.extraction_method
        doc.extraction_status = extraction_result.extraction_status
        doc.extraction_quality_score = extraction_result.quality_score
        doc.extraction_quality_label = extraction_result.quality_label
        doc.total_pages = extraction_result.total_pages
        doc.native_page_count = extraction_result.native_page_count
        doc.ocr_page_count = extraction_result.ocr_page_count
        doc.failed_page_count = extraction_result.failed_page_count
        doc.empty_page_count = extraction_result.empty_page_count
        doc.extraction_warnings_json = extraction_result.warnings
        doc.extractor_version = extraction_result.extractor_version
        doc.ocr_engine = extraction_result.ocr_engine

    doc.status = status
    doc.chunk_count = chunk_count
    doc.updated_at = _utcnow()
    doc.processed_at = _utcnow()

    # Update active processing run
    run = (
        db.query(DocumentProcessingRun)
        .filter(DocumentProcessingRun.document_id == document_id, DocumentProcessingRun.status == "PROCESSING")
        .order_by(DocumentProcessingRun.created_at.desc())
        .first()
    )

    if not run:
        run = DocumentProcessingRun(
            id=str(uuid.uuid4()),
            document_id=document_id,
            created_at=_utcnow(),
            started_at=_utcnow(),
        )
        db.add(run)

    run.status = status
    run.completed_at = _utcnow()
    if extraction_result:
        run.extraction_method = extraction_result.extraction_method
        run.extractor_version = extraction_result.extractor_version
        run.ocr_engine = extraction_result.ocr_engine
        run.total_pages = extraction_result.total_pages
        run.native_page_count = extraction_result.native_page_count
        run.ocr_page_count = extraction_result.ocr_page_count
        run.failed_page_count = extraction_result.failed_page_count
        run.empty_page_count = extraction_result.empty_page_count
        run.quality_score = extraction_result.quality_score
        run.quality_label = extraction_result.quality_label
        run.warnings_json = extraction_result.warnings

    # Clear prior page diagnostics and insert new
    db.query(DocumentPage).filter(DocumentPage.document_id == document_id).delete()

    if extraction_result and extraction_result.pages:
        for p in extraction_result.pages:
            page_rec = DocumentPage(
                id=str(uuid.uuid4()),
                document_id=document_id,
                page_number=p.page_number,
                extraction_method=p.extraction_method,
                status=p.status,
                character_count=p.character_count,
                quality_score=p.quality_score,
                section_label=p.section_label,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(page_rec)

    db.commit()
    db.refresh(doc)
    logger.info("Marked %s document id=%s chunks=%d", status, document_id, chunk_count)
    return doc


def mark_ready(db: Session, document_id: str, chunk_count: int) -> Document:
    """Legacy helper for backward compatibility."""
    return mark_ready_with_diagnostics(db, document_id, chunk_count)


def mark_failed(db: Session, document_id: str, error_code: Optional[str] = None) -> None:
    """Update document and active run to FAILED."""
    doc = db.get(Document, document_id)
    if doc:
        doc.status = "FAILED"
        doc.extraction_status = "FAILED"
        doc.updated_at = _utcnow()

    run = (
        db.query(DocumentProcessingRun)
        .filter(DocumentProcessingRun.document_id == document_id, DocumentProcessingRun.status == "PROCESSING")
        .order_by(DocumentProcessingRun.created_at.desc())
        .first()
    )
    if run:
        run.status = "FAILED"
        run.completed_at = _utcnow()
        run.error_code = error_code or "EXTRACTION_FAILED"

    db.commit()
    logger.warning("Marked FAILED document id=%s", document_id)


def soft_delete(db: Session, document_id: str) -> Document:
    """Soft-delete a document."""
    doc = db.get(Document, document_id)
    if not doc or doc.is_deleted:
        raise ValueError(f"Document '{document_id}' not found or already deleted.")
    doc.soft_delete()
    db.commit()
    db.refresh(doc)
    logger.info("Soft-deleted document id=%s", document_id)
    return doc


# ---------------------------------------------------------------------------
# Diagnostics & Reprocessing Queries
# ---------------------------------------------------------------------------

def get_by_id(db: Session, document_id: str) -> Optional[Document]:
    """Return non-deleted document or None."""
    doc = db.get(Document, document_id)
    if doc and not doc.is_deleted:
        return doc
    return None


def get_processing_runs(db: Session, document_id: str) -> List[DocumentProcessingRun]:
    """Get all processing runs for a document."""
    return list(
        db.query(DocumentProcessingRun)
        .filter(DocumentProcessingRun.document_id == document_id)
        .order_by(DocumentProcessingRun.created_at.desc())
        .all()
    )


def get_document_pages(db: Session, document_id: str) -> List[DocumentPage]:
    """Get page diagnostics for a document."""
    return list(
        db.query(DocumentPage)
        .filter(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number.asc())
        .all()
    )


def list_documents(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    filename_search: Optional[str] = None,
    folder_id: Optional[str] = None,
    root_only: bool = False,
    is_favorite: Optional[bool] = None,
    is_trash: bool = False,
    sort_by: str = "created_at",
) -> tuple[List[Document], int]:
    """
    List documents with filtering by status, search, folder_id, root, favorites, and trash.
    """
    if is_trash:
        stmt = select(Document).where(Document.deleted_at.is_not(None))
    else:
        stmt = select(Document).where(Document.deleted_at.is_(None))

    if status_filter:
        stmt = stmt.where(Document.status == status_filter.upper())

    if filename_search:
        stmt = stmt.where(Document.original_filename.ilike(f"%{filename_search}%"))

    if not is_trash:
        if folder_id:
            stmt = stmt.where(Document.folder_id == folder_id)
        elif root_only:
            stmt = stmt.where(Document.folder_id.is_(None))

        if is_favorite is not None:
            stmt = stmt.where(Document.is_favorite == is_favorite)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    # Sorting
    if sort_by == "name_asc":
        stmt = stmt.order_by(Document.original_filename.asc())
    elif sort_by == "name_desc":
        stmt = stmt.order_by(Document.original_filename.desc())
    elif sort_by == "last_accessed":
        stmt = stmt.order_by(Document.last_accessed_at.desc().nullslast(), Document.created_at.desc())
    else:
        # default: created_at desc
        stmt = stmt.order_by(Document.created_at.desc())

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())

    return items, total


def move_document(
    db: Session,
    document_id: str,
    user_id: str,
    target_folder_id: Optional[str] = None,
) -> Document:
    """Move a document to target folder (or root if target_folder_id is None)."""
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user_id or doc.is_deleted:
        raise ValueError(f"Document '{document_id}' not found or access denied.")

    if target_folder_id:
        from app.database.models.folder import Folder
        target_folder = db.get(Folder, target_folder_id)
        if not target_folder or target_folder.user_id != user_id or target_folder.is_deleted:
            raise ValueError(f"Target folder '{target_folder_id}' not found or access denied.")

    doc.folder_id = target_folder_id
    doc.updated_at = _utcnow()
    db.commit()
    db.refresh(doc)
    logger.info("Moved document id=%s to folder=%s user=%s", document_id, target_folder_id, user_id)
    return doc


def bulk_move_documents(
    db: Session,
    document_ids: List[str],
    user_id: str,
    target_folder_id: Optional[str] = None,
) -> int:
    """Transactionally move multiple documents. Fails atomically if any document is invalid."""
    if not document_ids:
        return 0

    if target_folder_id:
        from app.database.models.folder import Folder
        target_folder = db.get(Folder, target_folder_id)
        if not target_folder or target_folder.user_id != user_id or target_folder.is_deleted:
            raise ValueError(f"Target folder '{target_folder_id}' not found or access denied.")

    docs = list(
        db.query(Document)
        .filter(Document.id.in_(document_ids), Document.user_id == user_id, Document.deleted_at.is_(None))
        .all()
    )

    if len(docs) != len(set(document_ids)):
        found_ids = {d.id for d in docs}
        missing = set(document_ids) - found_ids
        raise ValueError(f"Cannot move: documents {list(missing)} not found or access denied.")

    now = _utcnow()
    for d in docs:
        d.folder_id = target_folder_id
        d.updated_at = now

    db.commit()
    logger.info("Bulk moved %d documents to folder=%s user=%s", len(docs), target_folder_id, user_id)
    return len(docs)


def toggle_favorite(
    db: Session,
    document_id: str,
    user_id: str,
    is_favorite: Optional[bool] = None,
) -> Document:
    """Toggle or set favorite flag for a document."""
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user_id or doc.is_deleted:
        raise ValueError(f"Document '{document_id}' not found or access denied.")

    if is_favorite is None:
        doc.is_favorite = not doc.is_favorite
    else:
        doc.is_favorite = is_favorite

    doc.updated_at = _utcnow()
    db.commit()
    db.refresh(doc)
    logger.info("Set document id=%s favorite=%s user=%s", document_id, doc.is_favorite, user_id)
    return doc


def bulk_favorite(
    db: Session,
    document_ids: List[str],
    user_id: str,
    is_favorite: bool,
) -> int:
    """Bulk update favorite state for multiple documents."""
    docs = list(
        db.query(Document)
        .filter(Document.id.in_(document_ids), Document.user_id == user_id, Document.deleted_at.is_(None))
        .all()
    )
    now = _utcnow()
    for d in docs:
        d.is_favorite = is_favorite
        d.updated_at = now

    db.commit()
    logger.info("Bulk favorite=%s applied to %d documents user=%s", is_favorite, len(docs), user_id)
    return len(docs)


def record_access(db: Session, document_id: str, user_id: str) -> None:
    """Record access timestamp when user opens/reads the document."""
    doc = db.get(Document, document_id)
    if doc and doc.user_id == user_id and not doc.is_deleted:
        doc.last_accessed_at = _utcnow()
        db.commit()


def restore_document(db: Session, document_id: str, user_id: str) -> Document:
    """
    Restore a soft-deleted document:
    If its previous folder no longer exists or is deleted, relocates to root (folder_id = NULL).
    """
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user_id or not doc.is_deleted:
        raise ValueError(f"Document '{document_id}' not found in trash.")

    if doc.folder_id:
        from app.database.models.folder import Folder
        f = db.get(Folder, doc.folder_id)
        if not f or f.user_id != user_id or f.is_deleted:
            doc.folder_id = None

    doc.deleted_at = None
    doc.status = "READY"
    doc.updated_at = _utcnow()
    db.commit()
    db.refresh(doc)
    logger.info("Restored document id=%s user=%s folder=%s", document_id, user_id, doc.folder_id)
    return doc


def bulk_restore_documents(db: Session, document_ids: List[str], user_id: str) -> int:
    """Bulk restore multiple soft-deleted documents from trash."""
    docs = list(
        db.query(Document)
        .filter(Document.id.in_(document_ids), Document.user_id == user_id, Document.deleted_at.is_not(None))
        .all()
    )
    from app.database.models.folder import Folder
    now = _utcnow()
    for d in docs:
        if d.folder_id:
            f = db.get(Folder, d.folder_id)
            if not f or f.user_id != user_id or f.is_deleted:
                d.folder_id = None
        d.deleted_at = None
        d.status = "READY"
        d.updated_at = now

    db.commit()
    logger.info("Bulk restored %d documents user=%s", len(docs), user_id)
    return len(docs)


def permanent_delete_document(db: Session, document_id: str, user_id: str) -> None:
    """Hard-delete a document record from database."""
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user_id:
        raise ValueError(f"Document '{document_id}' not found.")

    db.delete(doc)
    db.commit()
    logger.info("Permanently deleted document id=%s user=%s", document_id, user_id)


def get_quiz_count(db: Session, document_id: str) -> int:
    stmt = select(func.count()).where(Quiz.document_id == document_id)
    return db.execute(stmt).scalar_one()


def get_latest_quiz_date(db: Session, document_id: str) -> Optional[datetime]:
    stmt = (
        select(Quiz.created_at)
        .where(Quiz.document_id == document_id)
        .order_by(Quiz.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()

