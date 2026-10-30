"""
document_indexing.py — Phase 8 Page & Section Aware Vector Indexing
Handles ChromaDB writes, metadata preservation, section pathing, deduplication, and cleanup.
All public functions are synchronous (called via run_in_threadpool).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.database.models.auth import LEGACY_OWNER_ID
from app.services.document_extraction import ExtractedDocument, PageSection

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)


def _build_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )


def index_document(
    collection: "chromadb.Collection",
    extracted: ExtractedDocument,
    filename: str,
    file_type: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
    document_id: Optional[str] = None,
    user_id: str = LEGACY_OWNER_ID,
) -> tuple[str, int]:
    """
    Chunk the extracted document and insert into ChromaDB with enriched metadata.

    Returns:
        (document_id, chunk_count)

    Raises:
        ValueError: If the extracted text produces no chunks.
    """
    document_id = document_id or str(uuid.uuid4())
    splitter = _build_splitter(chunk_size, chunk_overlap)

    chunks = splitter.split_text(extracted.text)
    if not chunks:
        raise ValueError("Document produced zero chunks after splitting.")

    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[dict] = []

    # Build page boundaries and section lookups
    page_boundaries = _build_page_boundaries(extracted.sections)

    ext_method = getattr(extracted, "extraction_method", "native")
    quality_score = float(getattr(extracted, "quality_score", 1.0))

    for idx, chunk in enumerate(chunks):
        chunk_id = f"{document_id}:{idx}"
        page_num, sec_title, sec_path = _find_section_and_page_for_chunk(chunk, page_boundaries, extracted.sections)

        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "filename": filename,
                "file_type": file_type,
                "chunk_index": idx,
                "page_number": page_num if page_num is not None else -1,
                "section_title": sec_title or "General",
                "section_path": sec_path or "General",
                "extraction_method": ext_method,
                "quality_score": quality_score,
            }
        )

    # Atomically insert — remove orphaned chunks from any prior matching doc_id first
    _delete_document_chunks(collection, document_id)

    try:
        batch_size = 8
        for start_idx in range(0, len(documents), batch_size):
            end_idx = start_idx + batch_size
            collection.add(
                documents=documents[start_idx:end_idx],
                ids=ids[start_idx:end_idx],
                metadatas=metadatas[start_idx:end_idx],
            )
    except Exception as exc:
        # Rollback partial writes
        _delete_document_chunks(collection, document_id)
        raise RuntimeError(f"ChromaDB insert failed: {exc}") from exc

    logger.info("Indexed document_id=%s chunks=%d filename=%s", document_id, len(chunks), filename)
    return document_id, len(chunks)


def delete_document(
    collection: "chromadb.Collection",
    document_id: str,
    user_id: str = LEGACY_OWNER_ID,
) -> int:
    """Delete all chunks for a given document_id from ChromaDB."""
    return _delete_document_chunks(collection, document_id, user_id)


def _delete_document_chunks(
    collection: "chromadb.Collection",
    document_id: str,
    user_id: str | None = None,
) -> int:
    """Internal: delete by document_id filter and return count."""
    try:
        where = {"document_id": {"$eq": document_id}}
        if user_id is not None:
            where = {
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"document_id": {"$eq": document_id}},
                ]
            }
        results = collection.get(where=where)
        existing_ids = results.get("ids", [])
        if existing_ids:
            collection.delete(ids=existing_ids)
            logger.info("Deleted %d chunk(s) for document_id=%s", len(existing_ids), document_id)
        return len(existing_ids)
    except Exception as exc:
        logger.warning("Could not delete chunks for document_id=%s: %s", document_id, exc)
        return 0


def _build_page_boundaries(sections: List[PageSection]) -> List[tuple[int, int, Optional[int]]]:
    boundaries = []
    offset = 0
    for section in sections:
        start = offset
        end = offset + len(section.text)
        boundaries.append((start, end, section.page_number))
        offset = end + 2
    return boundaries


def _find_section_and_page_for_chunk(
    chunk: str,
    boundaries: List[tuple[int, int, Optional[int]]],
    sections: List[PageSection],
) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (page_number, section_title, section_path) for chunk."""
    if not sections:
        return None, None, None

    for section in sections:
        snippet = section.text[:40]
        if snippet and snippet in chunk:
            page_num = section.page_number
            title = getattr(section, "section_title", None)
            path = getattr(section, "section_path", None)
            return page_num, title, path

    return sections[0].page_number, getattr(sections[0], "section_title", None), getattr(sections[0], "section_path", None)
