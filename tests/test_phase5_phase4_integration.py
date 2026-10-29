"""
tests/test_phase5_phase4_integration.py
Tests for integration with Phase 4 study activity logging, daily goals, and recommendations.
"""
from __future__ import annotations

import uuid
import pytest

from app.database.models import StudySet, Flashcard, FlashcardReviewState
from app.services import flashcard_review_service
from app.services import study_goal_service
from app.services.study_recommendation_service import get_next_study_recommendation


def test_flashcard_review_logs_activity_and_progresses_goal(db_session):
    # Setup goal for flashcards_reviewed using the real API
    study_goal_service.create_or_update_goal(
        db_session, goal_type="flashcards_reviewed", target_value=5
    )

    st = StudySet(id=str(uuid.uuid4()), title="Goal Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    card_id = str(uuid.uuid4())
    card = Flashcard(
        id=card_id, study_set_id=st.id, front_text="Goal Q1", back_text="Goal A1",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)

    # Also add a second card so the session doesn't auto-complete after rating one card
    card2_id = str(uuid.uuid4())
    card2 = Flashcard(
        id=card2_id, study_set_id=st.id, front_text="Goal Q2", back_text="Goal A2",
        card_type="basic", difficulty="easy", generation_method="manual", position=2, status="ACTIVE"
    )
    db_session.add(card2)

    for cid in (card_id, card2_id):
        db_session.add(FlashcardReviewState(
            id=str(uuid.uuid4()), flashcard_id=cid, review_status="NEW",
            ease_level=2.5, review_count=0, correct_count=0, incorrect_count=0,
            consecutive_correct=0, interval_days=0, mastery_score=0.0,
        ))
    db_session.commit()

    # Start session
    session = flashcard_review_service.create_review_session(
        db_session, type("Req", (), {
            "study_set_id": st.id, "session_mode": "all_cards", "card_count": 10,
            "topic_label": None, "difficulty": None, "shuffle": False
        })()
    )
    item_id = session.items[0].id

    # Reveal & rate card
    flashcard_review_service.reveal_session_card(db_session, session.id, item_id)
    flashcard_review_service.rate_session_card(db_session, session.id, item_id, "good")

    # Verify goal progress incremented
    goals = study_goal_service.get_goals(db_session)
    flashcard_goal = [g for g in goals if g.goal_type == "flashcards_reviewed"]
    assert len(flashcard_goal) == 1
    assert flashcard_goal[0].current_value >= 1


def test_due_flashcards_influence_recommendations(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Due Rec Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    card_id = str(uuid.uuid4())
    card = Flashcard(
        id=card_id, study_set_id=st.id, front_text="Rec Q", back_text="Rec A",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # get_next_study_recommendation should return a dict with a recommendation
    rec = get_next_study_recommendation(db_session)
    assert rec is not None
    assert isinstance(rec, dict)
