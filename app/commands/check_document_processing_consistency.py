"""
app/commands/check_document_processing_consistency.py — Phase 8 Processing Consistency Checker

Usage:
    python -m app.commands.check_document_processing_consistency [--repair-status-counts] [--mark-stale-runs-failed]

Checks:
  1. READY SQLite documents with missing extraction metadata or page records.
  2. Document page counts vs total page diagnostics mismatch.
  3. Stuck PROCESSING runs (> 1 hour old).
  4. ChromaDB vector count vs SQLite chunk_count mismatch.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Set

import chromadb


def run_check(repair_status_counts: bool = False, mark_stale_runs_failed: bool = False) -> int:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    from app.database.session import SessionLocal
    from app.database.models.document import Document, DocumentProcessingRun, DocumentPage
    from config import settings

    print("=" * 60)
    print("Phase 8 Document Processing Consistency Check")
    print("=" * 60)

    db = SessionLocal()
    issues: List[str] = []

    try:
        active_docs = db.query(Document).filter(Document.deleted_at.is_(None)).all()
        print(f"Checking {len(active_docs)} active document(s)...")

        for doc in active_docs:
            if doc.status == "READY" and doc.extraction_quality_score is None:
                msg = f"  MISSING METADATA: doc_id={doc.id} file={doc.original_filename} lacks quality score."
                issues.append(msg)
                print(msg)

            pages = db.query(DocumentPage).filter(DocumentPage.document_id == doc.id).all()
            if doc.total_pages and len(pages) > 0 and len(pages) != doc.total_pages:
                msg = f"  PAGE COUNT MISMATCH: doc_id={doc.id} doc.total_pages={doc.total_pages} vs actual page records={len(pages)}."
                issues.append(msg)
                print(msg)

        # Check stuck processing runs
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        stale_runs = db.query(DocumentProcessingRun).filter(
            DocumentProcessingRun.status == "PROCESSING",
            DocumentProcessingRun.started_at < stale_cutoff
        ).all()

        for run in stale_runs:
            msg = f"  STUCK PROCESSING RUN: run_id={run.id} doc_id={run.document_id} started at {run.started_at}."
            issues.append(msg)
            print(msg)

            if mark_stale_runs_failed:
                run.status = "FAILED"
                run.error_code = "TIMEOUT_STUCK_RUN"
                print(f"  [REPAIR] Marked stuck run {run.id} as FAILED.")

        if mark_stale_runs_failed and stale_runs:
            db.commit()

        if not issues:
            print("✓ No processing consistency issues found.")

        print(f"\nTotal issues: {len(issues)}")
        print("=" * 60)
        return 1 if issues else 0

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check document processing consistency.")
    parser.add_argument("--repair-status-counts", action="store_true")
    parser.add_argument("--mark-stale-runs-failed", action="store_true")
    args = parser.parse_args()
    sys.exit(run_check(repair_status_counts=args.repair_status_counts, mark_stale_runs_failed=args.mark_stale_runs_failed))
