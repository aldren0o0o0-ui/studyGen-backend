"""Dry-run consistency audit for Phase 6 chat data."""
from __future__ import annotations

import argparse

from sqlalchemy import func, select

from app.database.models.chat import ChatCitation, ChatMessage, ChatSession, ChatSessionDocument
from app.database.models.document import Document
from app.database.session import SessionLocal


def audit(*, repair_message_counts: bool = False, remove_invalid_document_links: bool = False) -> dict[str, int]:
    db = SessionLocal()
    counts: dict[str, int] = {}
    try:
        sessions = list(db.execute(select(ChatSession)).scalars().all())
        no_documents = []
        wrong_counts = []
        deleted_document_links = []
        for session in sessions:
            links = list(db.execute(
                select(ChatSessionDocument).where(ChatSessionDocument.chat_session_id == session.id)
            ).scalars().all())
            if session.status == "ACTIVE" and not links:
                no_documents.append(session.id)
            actual_count = db.execute(
                select(func.count(ChatMessage.id)).where(ChatMessage.chat_session_id == session.id)
            ).scalar_one()
            if session.message_count != actual_count:
                wrong_counts.append(session.id)
                if repair_message_counts:
                    session.message_count = int(actual_count)
            for link in links:
                document = db.get(Document, link.document_id)
                if not document or document.deleted_at is not None:
                    deleted_document_links.append(link.id)
                    if remove_invalid_document_links:
                        db.delete(link)

        duplicate_sequences = db.execute(
            select(ChatMessage.chat_session_id, ChatMessage.sequence_number)
            .group_by(ChatMessage.chat_session_id, ChatMessage.sequence_number)
            .having(func.count(ChatMessage.id) > 1)
        ).all()
        empty_completed = db.execute(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.role == "assistant",
                ChatMessage.status == "COMPLETED",
                func.length(func.trim(ChatMessage.content)) == 0,
            )
        ).scalar_one()
        invalid_citations = db.execute(
            select(func.count(ChatCitation.id))
            .outerjoin(ChatMessage, ChatMessage.id == ChatCitation.message_id)
            .where(ChatMessage.id.is_(None))
        ).scalar_one()
        unselected_citations = db.execute(
            select(func.count(ChatCitation.id))
            .join(ChatMessage, ChatMessage.id == ChatCitation.message_id)
            .outerjoin(
                ChatSessionDocument,
                (ChatSessionDocument.chat_session_id == ChatMessage.chat_session_id)
                & (ChatSessionDocument.document_id == ChatCitation.document_id),
            )
            .where(ChatSessionDocument.id.is_(None))
        ).scalar_one()

        counts = {
            "active_sessions_without_documents": len(no_documents),
            "invalid_or_deleted_document_links": len(deleted_document_links),
            "duplicate_message_sequences": len(duplicate_sequences),
            "completed_assistant_messages_without_content": int(empty_completed),
            "citations_without_messages": int(invalid_citations),
            "citations_from_unselected_documents": int(unselected_citations),
            "incorrect_session_message_counts": len(wrong_counts),
        }
        if repair_message_counts or remove_invalid_document_links:
            db.commit()
        else:
            db.rollback()
        return counts
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit document-chat consistency (dry run by default).")
    parser.add_argument("--repair-message-counts", action="store_true")
    parser.add_argument("--remove-invalid-document-links", action="store_true")
    args = parser.parse_args()
    counts = audit(
        repair_message_counts=args.repair_message_counts,
        remove_invalid_document_links=args.remove_invalid_document_links,
    )
    for key, value in counts.items():
        print(f"{key}: {value}")
    print("mode:", "repair" if any(vars(args).values()) else "dry-run")
    return 1 if any(counts.values()) and not any(vars(args).values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
