"""
tests/test_phase5_reveal_rating.py
Tests for card reveal idempotency, rating requirements, rating idempotency, and review session completion.
"""
from __future__ import annotations

import uuid
import pytest
from fastapi import HTTPException

from app.database.models import StudySet, Flashcard, FlashcardReviewState
from app.services import flashcard_review_service


def _make_study_set_with_cards(db_session, n=2):
    st = StudySet(id=str(uuid.uuid4()), title=f"Test Set {uuid.uuid4().hex[:6]}", set_type="manual", status="ACTIVE")
    db_session.add(st)
    db_session.flush()

    cards = []
    for i in range(n):
        card_id = str(uuid.uuid4())
        card = Flashcard(
            id=card_id, study_set_id=st.id, front_text=f"Q{i+1}", back_text=f"A{i+1}",
            card_type="basic", difficulty="easy", generation_method="manual", position=i + 1, status="ACTIVE"
        )
        db_session.add(card)
        state = FlashcardReviewState(
            id=str(uuid.uuid4()), flashcard_id=card_id, review_status="NEW",
            ease_level=2.5, review_count=0, correct_count=0, incorrect_count=0,
            consecutive_correct=0, interval_days=0, mastery_score=0.0,
        )
        db_session.add(state)
        cards.append(card)

    db_session.commit()
    db_session.refresh(st)
    return st, cards


def test_reveal_and_rating_flow_and_idempotency(db_session):
    # Use 2 cards so rating the first card does NOT auto-complete the session
    st, cards = _make_study_set_with_cards(db_session, n=2)

    session = flashcard_review_service.create_review_session(
        db_session, type("Req", (), {
            "study_set_id": st.id, "session_mode": "all_cards", "card_count": 10,
            "topic_label": None, "difficulty": None, "shuffle": False
        })()
    )
    item_id = session.items[0].id

    # 1. Rating BEFORE reveal must be rejected with 400
    with pytest.raises(HTTPException) as exc_info:
        flashcard_review_service.rate_session_card(db_session, session.id, item_id, "good")
    assert exc_info.value.status_code == 400

    # 2. Reveal card
    revealed1 = flashcard_review_service.reveal_session_card(db_session, session.id, item_id)
    assert revealed1.is_revealed is True
    assert revealed1.back_text == "A1"

    # Repeated reveal is safe & idempotent
    revealed2 = flashcard_review_service.reveal_session_card(db_session, session.id, item_id)
    assert revealed2.is_revealed is True

    # 3. Rate card "good" (session stays ACTIVE because card 2 is pending)
    rated1 = flashcard_review_service.rate_session_card(db_session, session.id, item_id, "good")
    assert rated1.back_text == "A1"
    assert rated1.interval_days >= 1

    # 4. Rating IDEMPOTENCY: Repeated identical rating ("good") returns existing result without error
    rated_again = flashcard_review_service.rate_session_card(db_session, session.id, item_id, "good")
    assert rated_again.back_text == "A1"
    assert rated_again.interval_days == rated1.interval_days

    # 5. Rating CONFLICT: Changing rating ("good" -> "again") returns 409 Conflict
    with pytest.raises(HTTPException) as exc_info:
        flashcard_review_service.rate_session_card(db_session, session.id, item_id, "again")
    assert exc_info.value.status_code == 409


def test_invalid_rating_value_rejected(db_session):
    st, _ = _make_study_set_with_cards(db_session, n=2)

    session = flashcard_review_service.create_review_session(
        db_session, type("Req", (), {
            "study_set_id": st.id, "session_mode": "all_cards", "card_count": 10,
            "topic_label": None, "difficulty": None, "shuffle": False
        })()
    )
    item_id = session.items[0].id

    flashcard_review_service.reveal_session_card(db_session, session.id, item_id)

    with pytest.raises(HTTPException) as exc_info:
        flashcard_review_service.rate_session_card(db_session, session.id, item_id, "super_awesome")
    assert exc_info.value.status_code == 422
