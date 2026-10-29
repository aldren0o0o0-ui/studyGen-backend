"""
app/commands/check_document_consistency.py
Dry-run consistency checker between SQLite document records and ChromaDB vectors.

Usage:
    python -m app.commands.check_document_consistency [--repair-orphan-vectors]

Reports:
  1. READY SQLite documents with no ChromaDB chunks.
  2. ChromaDB document IDs with no active SQLite document.
  3. Deleted SQLite documents that still have ChromaDB chunks.
  4. Chunk-count mismatches (SQLite chunk_count vs actual ChromaDB count).

--repair-orphan-vectors:
  Removes ChromaDB chunks that belong to IDs with no active SQLite document,
  or chunks belonging to soft-deleted documents.
  Does NOT automatically mark SQLite records as FAILED.
  Does NOT delete relational records.
  Prints a summary of what was repaired.
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Set

import chromadb


def _load_db_documents():
    """Return all non-deleted documents from SQLite."""
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    from app.database.session import SessionLocal
    from app.database.models.document import Document
    from sqlalchemy import select

    db = SessionLocal()
    try:
        active = list(
            db.execute(
                select(Document).where(Document.deleted_at.is_(None))
            ).scalars().all()
        )
        deleted = list(
            db.execute(
                select(Document).where(Document.deleted_at.isnot(None))
            ).scalars().all()
        )
        return active, deleted
    finally:
        db.close()


def _load_chroma_ids(collection) -> Dict[str, int]:
    """Return {document_id: chunk_count} from ChromaDB."""
    result = collection.get(include=["metadatas"])
    metas = result.get("metadatas") or []
    counts: Dict[str, int] = {}
    for meta in metas:
        doc_id = meta.get("document_id")
        if doc_id:
            counts[doc_id] = counts.get(doc_id, 0) + 1
    return counts


def _get_collection():
    """Load the ChromaDB collection using settings."""
    import os
    from config import settings
    client = chromadb.PersistentClient(path=settings.chroma_data_path)
    try:
        return client.get_collection(settings.chroma_collection_name)
    except Exception:
        return None


def run_check(repair_orphan_vectors: bool = False) -> int:
    """
    Run all consistency checks. Returns exit code (0=clean, 1=issues found).
    """
    print("=" * 60)
    print("SQLite <-> ChromaDB Consistency Check")
    print("=" * 60)

    active_docs, deleted_docs = _load_db_documents()
    active_ids: Set[str] = {d.id for d in active_docs}
    deleted_ids: Set[str] = {d.id for d in deleted_docs}
    sqlite_counts: Dict[str, int] = {d.id: d.chunk_count for d in active_docs}

    collection = _get_collection()
    if collection is None:
        print("⚠  ChromaDB collection not found — skipping vector checks.")
        return 0

    chroma_counts = _load_chroma_ids(collection)
    chroma_ids: Set[str] = set(chroma_counts.keys())

    issues: List[str] = []

    # 1. READY SQLite documents with no ChromaDB chunks
    for doc in active_docs:
        if doc.status == "READY" and doc.id not in chroma_ids:
            msg = f"  MISSING VECTORS: doc_id={doc.id} file={doc.original_filename} status=READY has no chunks in ChromaDB."
            issues.append(msg)
            print(msg)

    # 2. ChromaDB IDs with no active SQLite document
    orphan_ids = chroma_ids - active_ids - deleted_ids
    for oid in sorted(orphan_ids):
        msg = f"  ORPHAN VECTOR: doc_id={oid} has {chroma_counts[oid]} chunks in ChromaDB but no SQLite document."
        issues.append(msg)
        print(msg)

    # 3. Deleted SQLite documents with remaining ChromaDB chunks
    for did in sorted(deleted_ids):
        if did in chroma_ids:
            msg = f"  STALE VECTORS: doc_id={did} is deleted in SQLite but still has {chroma_counts[did]} chunks in ChromaDB."
            issues.append(msg)
            print(msg)

    # 4. Chunk-count mismatches
    for doc in active_docs:
        if doc.id in chroma_ids:
            actual = chroma_counts[doc.id]
            expected = doc.chunk_count
            if actual != expected:
                msg = f"  COUNT MISMATCH: doc_id={doc.id} SQLite.chunk_count={expected} vs ChromaDB count={actual}."
                issues.append(msg)
                print(msg)

    if not issues:
        print("✓ No consistency issues found.")

    # --- Optional repair ---
    if repair_orphan_vectors and (orphan_ids or any(did in chroma_ids for did in deleted_ids)):
        to_delete = list(orphan_ids | {did for did in deleted_ids if did in chroma_ids})
        if to_delete:
            print(f"\n[REPAIR] Removing ChromaDB chunks for {len(to_delete)} document ID(s)...")
            for doc_id in to_delete:
                chunk_count = chroma_counts.get(doc_id, 0)
                # Get all chunk IDs for this document
                result = collection.get(
                    where={"document_id": {"$eq": doc_id}},
                    include=[],
                )
                ids_to_delete = result.get("ids") or []
                if ids_to_delete:
                    collection.delete(ids=ids_to_delete)
                    print(f"  Deleted {len(ids_to_delete)} chunks for doc_id={doc_id}")
            print("[REPAIR] Done.")

    print(f"\nTotal issues: {len(issues)}")
    print("=" * 60)
    return 1 if issues else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check SQLite <-> ChromaDB document consistency."
    )
    parser.add_argument(
        "--repair-orphan-vectors",
        action="store_true",
        help=(
            "Remove ChromaDB chunks for orphaned or deleted document IDs. "
            "Does NOT modify SQLite records."
        ),
    )
    args = parser.parse_args()
    sys.exit(run_check(repair_orphan_vectors=args.repair_orphan_vectors))
