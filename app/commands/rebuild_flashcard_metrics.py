"""
app/commands/rebuild_flashcard_metrics.py — Phase 5.1
CLI command to recompute study set card counts and review mastery metrics using immutable history.

Usage:
    python -m app.commands.rebuild_flashcard_metrics          # Dry run
    python -m app.commands.rebuild_flashcard_metrics --apply  # Commit changes
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.database.models.flashcard import (
    StudySet, Flashcard, FlashcardReviewState, FlashcardReviewResult
)
from app.database.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def rebuild_flashcard_metrics(apply: bool = False) -> int:
    db = SessionLocal()
    updated_sets = 0
    updated_states = 0

    try:
        # 1. Rebuild StudySet card_count
        study_sets = db.query(StudySet).all()
        for st in study_sets:
            actual_count = db.query(Flashcard).filter(
                Flashcard.study_set_id == st.id,
                Flashcard.status != "DELETED",
            ).count()

            if st.card_count != actual_count:
                logger.info(
                    "[Dry-run=%s] StudySet %s ('%s'): card_count %d -> %d",
                    not apply, st.id, st.title, st.card_count, actual_count
                )
                st.card_count = actual_count
                updated_sets += 1

        # 2. Rebuild review states counters from immutable FlashcardReviewResult history
        active_cards = db.query(Flashcard).filter(Flashcard.status == "ACTIVE").all()
        for card in active_cards:
            results = (
                db.query(FlashcardReviewResult)
                .filter(FlashcardReviewResult.flashcard_id == card.id)
                .order_by(FlashcardReviewResult.reviewed_at.asc())
                .all()
            )
            state = card.review_state
            if not state and apply:
                continue  # check_consistency handles missing states

            if state and results:
                total_reviews = len(results)
                correct = sum(1 for r in results if r.was_correct)
                incorrect = total_reviews - correct
                last_result = results[-1]

                # Recalculate streak
                streak = 0
                for r in reversed(results):
                    if r.was_correct:
                        streak += 1
                    else:
                        break

                if (
                    state.review_count != total_reviews
                    or state.correct_count != correct
                    or state.incorrect_count != incorrect
                ):
                    logger.info(
                        "[Dry-run=%s] Card %s review state counters mismatch: stored(rev=%d,c=%d,inc=%d) vs computed(rev=%d,c=%d,inc=%d)",
                        not apply, card.id, state.review_count, state.correct_count, state.incorrect_count,
                        total_reviews, correct, incorrect
                    )
                    state.review_count = total_reviews
                    state.correct_count = correct
                    state.incorrect_count = incorrect
                    state.consecutive_correct = streak
                    state.last_reviewed_at = last_result.reviewed_at
                    updated_states += 1

        if apply:
            db.commit()
            logger.info("Rebuild COMPLETED: Updated %d study sets and %d review states.", updated_sets, updated_states)
        else:
            logger.info("Dry-run COMPLETED: %d study sets and %d review states need updates. Run with --apply to commit.", updated_sets, updated_states)

        return 0

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Rebuild Flashcard Study Metrics")
    parser.add_argument("--apply", action="store_true", help="Commit recalculations to the database.")
    args = parser.parse_args()

    exit_code = rebuild_flashcard_metrics(apply=args.apply)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
