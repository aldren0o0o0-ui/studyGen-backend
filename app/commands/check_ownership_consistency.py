"""Audit SQL and vector ownership relationships without guessing repairs."""
from __future__ import annotations

import argparse

from sqlalchemy import func, select

from app.commands.backfill_vector_ownership import backfill
from app.database.models.auth import RefreshToken, User
from app.database.models.attempt import QuizAttempt
from app.database.models.chat import ChatSession, ChatSessionDocument
from app.database.models.document import Document
from app.database.models.flashcard import StudySet
from app.database.models.quiz import Quiz
from app.database.session import SessionLocal


def audit(*, collection=None) -> dict[str, int]:
    db = SessionLocal()
    try:
        counts = {
            "attempt_quiz_owner_mismatch": int(db.execute(
                select(func.count(QuizAttempt.id)).join(Quiz, Quiz.id == QuizAttempt.quiz_id)
                .where(QuizAttempt.user_id != Quiz.user_id)
            ).scalar_one()),
            "quiz_document_owner_mismatch": int(db.execute(
                select(func.count(Quiz.id)).join(Document, Document.id == Quiz.document_id)
                .where(Quiz.user_id != Document.user_id)
            ).scalar_one()),
            "study_set_document_owner_mismatch": int(db.execute(
                select(func.count(StudySet.id)).join(Document, Document.id == StudySet.document_id)
                .where(StudySet.user_id != Document.user_id)
            ).scalar_one()),
            "chat_document_owner_mismatch": int(db.execute(
                select(func.count(ChatSessionDocument.id))
                .join(ChatSession, ChatSession.id == ChatSessionDocument.chat_session_id)
                .join(Document, Document.id == ChatSessionDocument.document_id)
                .where(ChatSession.user_id != Document.user_id)
            ).scalar_one()),
            "refresh_token_missing_or_inactive_user": int(db.execute(
                select(func.count(RefreshToken.id)).outerjoin(User, User.id == RefreshToken.user_id)
                .where((User.id.is_(None)) | (User.deleted_at.is_not(None)))
            ).scalar_one()),
        }
    finally:
        db.close()
    vectors = backfill(apply=False, collection=collection)
    counts["vector_missing_owner"] = vectors["missing_owner_metadata"]
    counts["vector_wrong_owner"] = vectors["wrong_owner_metadata"]
    counts["orphan_vectors"] = vectors["orphan_vectors"]
    return counts


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = audit()
    for key, value in report.items():
        print(f"{key}: {value}")
    print("mode: dry-run")
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
