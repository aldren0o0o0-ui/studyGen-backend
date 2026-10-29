"""
app/commands/rebuild_document_processing_metadata.py — Phase 8 Metadata Rebuild CLI Command

Usage:
    python -m app.commands.rebuild_document_processing_metadata [--apply]

Rebuilds missing metadata (extraction_method, extraction_status, quality_score, quality_label)
for legacy documents deterministically without re-extracting text or invoking OCR.
"""
from __future__ import annotations

import argparse
import sys
from typing import List


def run_rebuild(apply: bool = False) -> int:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    from app.database.session import SessionLocal
    from app.database.models.document import Document

    print("=" * 60)
    print("Phase 8 Document Processing Metadata Rebuild")
    print("=" * 60)

    db = SessionLocal()
    updated_count = 0

    try:
        docs = db.query(Document).filter(Document.deleted_at.is_(None)).all()
        for doc in docs:
            needs_update = False
            if doc.extraction_method is None:
                doc.extraction_method = "legacy"
                needs_update = True
            if doc.extraction_status is None:
                doc.extraction_status = doc.status
                needs_update = True
            if doc.extraction_quality_score is None:
                doc.extraction_quality_score = 1.0 if doc.status == "READY" else 0.0
                needs_update = True
            if doc.extraction_quality_label is None:
                doc.extraction_quality_label = "excellent" if doc.status == "READY" else "unusable"
                needs_update = True
            if doc.total_pages is None:
                doc.total_pages = 1
                doc.native_page_count = 1
                doc.ocr_page_count = 0
                doc.failed_page_count = 0
                doc.empty_page_count = 0
                needs_update = True

            if needs_update:
                updated_count += 1
                print(f"  REBUILD: doc_id={doc.id} filename={doc.original_filename}")

        if apply and updated_count > 0:
            db.commit()
            print(f"\n[APPLY] Rebuilt metadata for {updated_count} document(s).")
        elif not apply and updated_count > 0:
            print(f"\n[DRY-RUN] {updated_count} document(s) need metadata rebuild. Run with --apply to write.")
        else:
            print("\n✓ All documents have complete processing metadata.")

        print("=" * 60)
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild document processing metadata.")
    parser.add_argument("--apply", action="store_true", help="Apply metadata updates to database.")
    args = parser.parse_args()
    sys.exit(run_rebuild(apply=args.apply))
