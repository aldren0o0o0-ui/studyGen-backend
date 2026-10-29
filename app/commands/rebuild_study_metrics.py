"""
app.commands.rebuild_study_metrics — Phase 4

CLI tool to rebuild TopicPerformance aggregates and missing ReviewItems
from raw attempt data.

Usage:
  python -m app.commands.rebuild_study_metrics          # Dry run (default)
  python -m app.commands.rebuild_study_metrics --apply  # Execute changes

Requirements:
  - Idempotent: repeated runs produce identical data.
  - Non-destructive: does NOT modify raw QuizAttempt or AttemptAnswer records.
  - Safe: handles deleted documents without throwing FK errors.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.database.models.auth import User
from app.database.session import SessionLocal
from app.ownership_context import ownership_scope
from app.services import review_item_service, topic_performance_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rebuild_study_metrics")


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Phase 4 TopicPerformance aggregates and ReviewItems from attempts."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes to the database. If omitted, performs a dry run.",
    )
    parsed = parser.parse_args(args)

    mode_str = "APPLY" if parsed.apply else "DRY RUN"
    logger.info("Starting study metrics rebuild in [%s] mode...", mode_str)

    db = SessionLocal()
    try:
        totals = {
            "topics_found": 0, "topics_created": 0, "topics_updated": 0,
            "incorrect_answers_found": 0, "existing_review_items": 0,
            "items_to_create": 0, "items_created": 0,
        }
        users = list(db.execute(select(User.id).where(User.deleted_at.is_(None))).scalars())
        for user_id in users:
            with ownership_scope(user_id):
                tp_report = topic_performance_service.rebuild_topic_performance(
                    db, apply=parsed.apply
                )
                ri_report = review_item_service.rebuild_review_items(
                    db, apply=parsed.apply
                )
            for key in totals:
                totals[key] += int(tp_report.get(key, ri_report.get(key, 0)))

        logger.info(
            "Topic Performance: %d found, %d created, %d updated.",
            totals["topics_found"],
            totals["topics_created"],
            totals["topics_updated"],
        )

        logger.info(
            "Review Items: %d incorrect answers found, %d existing items, %d items %s.",
            totals["incorrect_answers_found"],
            totals["existing_review_items"],
            totals["items_created"] if parsed.apply else totals["items_to_create"],
            "created" if parsed.apply else "would be created",
        )

        logger.info("Rebuild complete. Mode: [%s].", mode_str)
        return 0
    except Exception as exc:
        logger.error("Error during study metrics rebuild: %s", exc, exc_info=True)
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
