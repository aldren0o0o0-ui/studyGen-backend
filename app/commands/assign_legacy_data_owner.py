"""Assign placeholder-owned legacy data to one existing user (dry run by default)."""
from __future__ import annotations

import argparse

from sqlalchemy import func, select, update

from app.commands.backfill_vector_ownership import backfill
from app.database.models.auth import LEGACY_OWNER_ID, User
from app.database.models.attempt import QuizAttempt
from app.database.models.chat import ChatSession
from app.database.models.document import Document
from app.database.models.flashcard import FlashcardReviewSession, StudySet
from app.database.models.quiz import Quiz
from app.database.models.study import (
    ReviewItem, ReviewSession, StudyActivity, StudyGoal, TopicPerformance,
)
from app.database.session import SessionLocal

OWNED_MODELS = (
    Document, Quiz, QuizAttempt, TopicPerformance, ReviewItem, ReviewSession,
    StudyGoal, StudyActivity, StudySet, FlashcardReviewSession, ChatSession,
)


def assign(email: str, *, apply: bool = False, collection=None) -> dict[str, int]:
    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(User.normalized_email == email.strip().casefold())
        ).scalar_one_or_none()
        if not user or user.id == LEGACY_OWNER_ID:
            raise ValueError("Target account does not exist or is the disabled placeholder owner.")
        report: dict[str, int] = {}
        for model in OWNED_MODELS:
            count = int(db.execute(
                select(func.count()).select_from(model)
                .where(model.user_id == LEGACY_OWNER_ID)
            ).scalar_one())
            report[model.__tablename__] = count
            if apply and count:
                db.execute(
                    update(model)
                    .where(model.user_id == LEGACY_OWNER_ID)
                    .values(user_id=user.id)
                )
        if apply:
            db.commit()
        else:
            db.rollback()
        vector = backfill(apply=apply, collection=collection)
        report["vectors_would_update"] = vector["chunks_would_update"]
        report["orphan_vectors"] = vector["orphan_vectors"]
        return report
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = assign(args.email, apply=args.apply)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    for key, value in report.items():
        print(f"{key}: {value}")
    print("mode:", "apply" if args.apply else "dry-run")
    return 1 if report["orphan_vectors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
