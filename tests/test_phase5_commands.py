"""
tests/test_phase5_commands.py
Tests for check_flashcard_consistency and rebuild_flashcard_metrics CLI maintenance commands.
"""
from __future__ import annotations

import uuid
import pytest
from app.database.models import StudySet, Flashcard, FlashcardReviewState
from app.commands.check_flashcard_consistency import check_and_repair_consistency
from app.commands.rebuild_flashcard_metrics import rebuild_flashcard_metrics


def test_check_flashcard_consistency_detects_and_repairs_mismatches(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Consistency Set", set_type="manual", status="ACTIVE", card_count=999)
    db_session.add(st)
    card_id = str(uuid.uuid4())
    card = Flashcard(
        id=card_id, study_set_id=st.id, front_text="Front", back_text="Back",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    # Add review state so the missing-state check doesn't add a second issue
    state = FlashcardReviewState(
        id=str(uuid.uuid4()), flashcard_id=card_id, review_status="NEW",
        ease_level=2.5, review_count=0, correct_count=0, incorrect_count=0,
        consecutive_correct=0, interval_days=0, mastery_score=0.0,
    )
    db_session.add(state)
    db_session.commit()

    # Dry-run detects card_count mismatch (stored=999, actual=1)
    res_dry = check_and_repair_consistency()
    assert res_dry == 1

    # Repair card counts
    res_repair = check_and_repair_consistency(repair_card_counts=True)
    assert res_repair == 0

    db_session.refresh(st)
    assert st.card_count == 1


def test_rebuild_flashcard_metrics_dry_run_and_apply(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Rebuild Set", set_type="manual", status="ACTIVE", card_count=0)
    db_session.add(st)
    card = Flashcard(
        id=str(uuid.uuid4()), study_set_id=st.id, front_text="Front", back_text="Back",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # Dry run makes no changes
    rebuild_flashcard_metrics(apply=False)
    db_session.refresh(st)
    assert st.card_count == 0

    # Apply commits recalculation
    rebuild_flashcard_metrics(apply=True)
    db_session.refresh(st)
    assert st.card_count == 1
