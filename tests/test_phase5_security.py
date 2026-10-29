"""
tests/test_phase5_security.py
Active Flashcard Security verification — recursively verifies that unrevealed card payloads omit hidden answers, sources, and schedule internal state.
"""
from __future__ import annotations

import uuid
import pytest
from app.database.models import StudySet, Flashcard, FlashcardReviewSession, FlashcardReviewSessionItem
from app.services import flashcard_review_service

HIDDEN_FIELDS_BEFORE_REVEAL = {
    "back_text",
    "answer",
    "correct_answer",
    "sources",
    "source_references_json",
    "source_excerpt",
    "review_result",
    "was_correct",
    "next_interval_days",
    "mastery_score",
}


def assert_no_hidden_fields_recursive(obj, context="payload"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in HIDDEN_FIELDS_BEFORE_REVEAL, f"Security Violation: '{key}' found in unrevealed {context}"
            assert_no_hidden_fields_recursive(value, f"{context}.{key}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            assert_no_hidden_fields_recursive(item, f"{context}[{idx}]")


def test_active_review_card_security_hides_answers_and_sources(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Security Test Set", set_type="manual", status="ACTIVE")
    db_session.add(st)

    card = Flashcard(
        id=str(uuid.uuid4()), study_set_id=st.id, front_text="What is 2+2?", back_text="4",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # Start review session
    session = flashcard_review_service.create_review_session(
        db_session, type("Req", (), {"study_set_id": st.id, "session_mode": "all_cards", "card_count": 10, "topic_label": None, "difficulty": None, "shuffle": False})()
    )

    # Convert session items to ActiveReviewCard view
    item = session.items[0]
    active_view = flashcard_review_service.get_active_review_card(item)
    active_dict = active_view.model_dump()

    # Must NOT contain back_text, sources, or schedule internals before reveal
    assert "back_text" not in active_dict
    assert "sources" not in active_dict
    assert active_dict["is_revealed"] is False

    # Recursive payload audit
    assert_no_hidden_fields_recursive(active_dict)
