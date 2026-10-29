"""
tests/test_phase5_archive_delete.py
Test suite for study set archiving and soft deletion isolation.
"""
from __future__ import annotations

import uuid
import pytest
from fastapi import HTTPException

from app.database.models import Document, Quiz, StudySet, Flashcard, FlashcardReviewSession
from app.services import flashcard_service, flashcard_review_service


def test_archived_study_set_cannot_start_normal_review_session(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Archived Physics", set_type="manual", status="ARCHIVED")
    db_session.add(st)
    card = Flashcard(
        id=str(uuid.uuid4()), study_set_id=st.id, front_text="Front", back_text="Back",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # Attempting to start a review session on an ARCHIVED study set returns 400 Bad Request
    with pytest.raises(HTTPException) as exc_info:
        flashcard_review_service.create_review_session(
            db_session, type("Req", (), {"study_set_id": st.id, "session_mode": "all_cards", "card_count": 10, "topic_label": None, "difficulty": None, "shuffle": False})()
        )
    assert exc_info.value.status_code == 400


def test_deleted_study_set_preserves_historical_review_sessions(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Chemistry Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    card = Flashcard(
        id=str(uuid.uuid4()), study_set_id=st.id, front_text="H2O", back_text="Water",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # Create review session
    session = flashcard_review_service.create_review_session(
        db_session, type("Req", (), {"study_set_id": st.id, "session_mode": "all_cards", "card_count": 10, "topic_label": None, "difficulty": None, "shuffle": False})()
    )

    # Soft delete study set
    flashcard_service.delete_study_set(db_session, st.id)

    # Session record must still be fetchable from DB for historical analytics
    saved_session = flashcard_review_service.get_review_session(db_session, session.id)
    assert saved_session is not None
    assert saved_session.study_set_id == st.id
