"""
app/commands/check_flashcard_consistency.py — Phase 5.1
CLI command to check and repair flashcard database integrity.

Usage:
    python -m app.commands.check_flashcard_consistency
    python -m app.commands.check_flashcard_consistency --repair-card-counts
    python -m app.commands.check_flashcard_consistency --repair-missing-review-states
    python -m app.commands.check_flashcard_consistency --unschedule-deleted-cards
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

from app.database.models.flashcard import (
    StudySet, Flashcard, FlashcardReviewState, FlashcardReviewSessionItem
)
from app.database.session import SessionLocal
from app.database.types import normalize_json_value

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def check_and_repair_consistency(
    repair_card_counts: bool = False,
    repair_missing_review_states: bool = False,
    unschedule_deleted_cards: bool = False,
) -> int:
    db = SessionLocal()
    issues_found = 0
    repaired_count = 0

    try:
        # 1. Active cards with missing or deleted study set
        active_cards = db.query(Flashcard).filter(Flashcard.status == "ACTIVE").all()
        for card in active_cards:
            st = db.get(StudySet, card.study_set_id)
            if not st or st.status == "DELETED":
                logger.warning("Card %s has missing/deleted StudySet %s", card.id, card.study_set_id)
                issues_found += 1

        # 2. Active cards missing a review state
        cards_without_state = []
        for card in active_cards:
            if not card.review_state:
                logger.warning("Active Card %s is missing FlashcardReviewState", card.id)
                issues_found += 1
                cards_without_state.append(card)

        if repair_missing_review_states and cards_without_state:
            for card in cards_without_state:
                state = FlashcardReviewState(
                    id=str(uuid4()),
                    flashcard_id=card.id,
                    review_status="NEW",
                    ease_level=2.5,
                    review_count=0,
                    correct_count=0,
                    incorrect_count=0,
                    consecutive_correct=0,
                    interval_days=0,
                    last_reviewed_at=None,
                    next_review_at=None,
                    mastery_score=0.0,
                )
                db.add(state)
                repaired_count += 1
            db.commit()
            logger.info("Repaired %d missing FlashcardReviewState records.", len(cards_without_state))

        # 3. Deleted cards still scheduled
        scheduled_deleted = (
            db.query(FlashcardReviewState)
            .join(Flashcard, FlashcardReviewState.flashcard_id == Flashcard.id)
            .filter(Flashcard.status == "DELETED", FlashcardReviewState.next_review_at.isnot(None))
            .all()
        )
        if scheduled_deleted:
            logger.warning("Found %d deleted cards still scheduled for review.", len(scheduled_deleted))
            issues_found += len(scheduled_deleted)
            if unschedule_deleted_cards:
                for state in scheduled_deleted:
                    state.next_review_at = None
                    repaired_count += 1
                db.commit()
                logger.info("Unscheduled %d deleted cards.", len(scheduled_deleted))

        # 4. Check study set card counts
        study_sets = db.query(StudySet).filter(StudySet.status != "DELETED").all()
        for st in study_sets:
            actual_count = db.query(Flashcard).filter(
                Flashcard.study_set_id == st.id,
                Flashcard.status != "DELETED",
            ).count()
            if st.card_count != actual_count:
                logger.warning(
                    "StudySet %s ('%s') card_count mismatch: stored=%d, actual=%d",
                    st.id, st.title, st.card_count, actual_count
                )
                issues_found += 1
                if repair_card_counts:
                    st.card_count = actual_count
                    repaired_count += 1

        if repair_card_counts and repaired_count > 0:
            db.commit()
            logger.info("Repaired card counts for study sets.")

        # 5. Invalid source references JSON
        all_cards = db.query(Flashcard).all()
        for card in all_cards:
            if card.source_references_json:
                try:
                    data = normalize_json_value(card.source_references_json, location=f"flashcards/{card.id}/source_references_json")
                    if not isinstance(data, list):
                        logger.warning("Card %s source_references_json is not a JSON list", card.id)
                        issues_found += 1
                except Exception:
                    logger.warning("Card %s source_references_json has invalid JSON", card.id)
                    issues_found += 1

        # Summary
        if issues_found == 0:
            logger.info("Consistency Check PASSED: No issues found.")
            return 0
        else:
            logger.warning(
                "Consistency Check COMPLETED: Found %d issues. Repaired: %d",
                issues_found, repaired_count
            )
            return 0 if (repair_card_counts or repair_missing_review_states or unschedule_deleted_cards) and repaired_count == issues_found else 1

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Flashcard Database Consistency Check")
    parser.add_argument("--repair-card-counts", action="store_true", help="Repair StudySet card_count mismatches.")
    parser.add_argument("--repair-missing-review-states", action="store_true", help="Create missing review states.")
    parser.add_argument("--unschedule-deleted-cards", action="store_true", help="Clear next_review_at for deleted cards.")
    args = parser.parse_args()

    exit_code = check_and_repair_consistency(
        repair_card_counts=args.repair_card_counts,
        repair_missing_review_states=args.repair_missing_review_states,
        unschedule_deleted_cards=args.unschedule_deleted_cards,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
