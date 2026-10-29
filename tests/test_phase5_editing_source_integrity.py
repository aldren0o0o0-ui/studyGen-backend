"""
tests/test_phase5_editing_source_integrity.py
Tests for source validation degradation and review state handling on flashcard edits.
"""
from __future__ import annotations

import json
import uuid
import pytest
from app.database.models import StudySet, Flashcard, FlashcardReviewState
from app.schemas.flashcards import FlashcardUpdate
from app.services import flashcard_service


def test_formatting_only_edit_preserves_source_and_review_state(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Genetics Set", set_type="manual", status="ACTIVE")
    db_session.add(st)

    card_id = str(uuid.uuid4())
    card = Flashcard(
        id=card_id,
        study_set_id=st.id,
        front_text="What is DNA?",
        back_text="Deoxyribonucleic acid",
        card_type="concept",
        difficulty="medium",
        source_references_json=json.dumps([{"chunk_index": 0, "page_number": 1, "filename": "gen.pdf", "excerpt": "DNA is..."}]),
        generation_method="document_ai",
        position=1,
        status="ACTIVE",
        is_user_edited=False,
        source_validation_status="valid",
    )
    db_session.add(card)

    state = FlashcardReviewState(
        id=str(uuid.uuid4()),
        flashcard_id=card_id,
        review_status="MASTERED",
        ease_level=2.5,
        review_count=5,
        correct_count=5,
        incorrect_count=0,
        consecutive_correct=5,
        interval_days=10,
        mastery_score=1.0,
    )
    db_session.add(state)
    db_session.commit()

    # Formatting-only edit (adding leading/trailing whitespace & multiple spaces)
    updated = flashcard_service.update_flashcard(
        db_session, card_id, FlashcardUpdate(front_text="  What   is   DNA?  ")
    )

    # Must preserve source validation status and review status
    assert updated.source_validation_status == "valid"
    assert updated.is_user_edited is False
    assert updated.review_state.review_status == "MASTERED"
    assert updated.review_state.consecutive_correct == 5


def test_meaningful_content_edit_invalidates_source_and_resets_learning_state(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Genetics Set 2", set_type="manual", status="ACTIVE")
    db_session.add(st)

    card_id = str(uuid.uuid4())
    card = Flashcard(
        id=card_id,
        study_set_id=st.id,
        front_text="What is DNA?",
        back_text="Deoxyribonucleic acid",
        card_type="concept",
        difficulty="medium",
        source_references_json=json.dumps([{"chunk_index": 0, "page_number": 1, "filename": "gen.pdf", "excerpt": "DNA is..."}]),
        generation_method="document_ai",
        position=1,
        status="ACTIVE",
        is_user_edited=False,
        source_validation_status="valid",
    )
    db_session.add(card)

    state = FlashcardReviewState(
        id=str(uuid.uuid4()),
        flashcard_id=card_id,
        review_status="MASTERED",
        ease_level=2.5,
        review_count=5,
        correct_count=5,
        incorrect_count=0,
        consecutive_correct=5,
        interval_days=10,
        mastery_score=1.0,
    )
    db_session.add(state)
    db_session.commit()

    # Meaningful text edit (changing concept to RNA)
    updated = flashcard_service.update_flashcard(
        db_session, card_id, FlashcardUpdate(front_text="What is RNA?")
    )

    # Must mark as user edited, degrade source validation to needs_review, and reset review status to LEARNING
    assert updated.is_user_edited is True
    assert updated.source_validation_status == "needs_review"
    assert updated.review_state.review_status == "LEARNING"
    assert updated.review_state.consecutive_correct == 0
    assert updated.review_state.interval_days == 0
    assert updated.review_state.mastery_score == 0.0
