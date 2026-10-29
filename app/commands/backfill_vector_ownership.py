"""Backfill Chroma chunk user_id metadata from authoritative SQLite documents."""
from __future__ import annotations

import argparse

import chromadb
from sqlalchemy import select

from app.database.models.document import Document
from app.database.session import SessionLocal
from config import settings


def get_collection():
    client = chromadb.PersistentClient(path=settings.chroma_data_path)
    return client.get_or_create_collection(settings.chroma_collection_name)


def backfill(*, apply: bool = False, collection=None) -> dict[str, int]:
    collection = collection or get_collection()
    db = SessionLocal()
    try:
        owners = dict(db.execute(select(Document.id, Document.user_id)).all())
        raw = collection.get(include=["metadatas"])
        ids = raw.get("ids") or []
        metas = raw.get("metadatas") or []
        changed_ids, changed_metas = [], []
        orphan = wrong = missing = 0
        for chunk_id, metadata in zip(ids, metas):
            metadata = dict(metadata or {})
            document_id = str(metadata.get("document_id") or "")
            owner = owners.get(document_id)
            if not owner:
                orphan += 1
                continue
            current = metadata.get("user_id")
            if current == owner:
                continue
            if current is None:
                missing += 1
            else:
                wrong += 1
            metadata["user_id"] = owner
            changed_ids.append(chunk_id)
            changed_metas.append(metadata)
        if apply and changed_ids:
            collection.update(ids=changed_ids, metadatas=changed_metas)
        return {
            "chunks_scanned": len(ids),
            "missing_owner_metadata": missing,
            "wrong_owner_metadata": wrong,
            "orphan_vectors": orphan,
            "chunks_updated": len(changed_ids) if apply else 0,
            "chunks_would_update": len(changed_ids),
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = backfill(apply=args.apply)
    for key, value in report.items():
        print(f"{key}: {value}")
    print("mode:", "apply" if args.apply else "dry-run")
    return 1 if report["orphan_vectors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
