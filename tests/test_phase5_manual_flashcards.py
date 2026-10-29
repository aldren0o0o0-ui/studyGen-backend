"""
tests/test_phase5_manual_flashcards.py
Test suite for manual flashcard CRUD, content validation, duplication rejection, suspension, and deletion.
"""
from __future__ import annotations

import uuid
import pytest
from fastapi import HTTPException

from app.database.models import StudySet, Flashcard
from app.schemas.flashcards import FlashcardCreate, FlashcardUpdate
from app.services import flashcard_service


def test_create_manual_flashcard_validation(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Physics Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    db_session.commit()

    # Valid card
    card = flashcard_service.add_flashcard(
        db_session, st.id, FlashcardCreate(front_text="Speed of Light", back_text="3 x 10^8 m/s", difficulty="easy", card_type="definition")
    )
    assert card.id is not None
    assert card.position == 1

    # Identical front & back rejected by Pydantic schema validator
    with pytest.raises(ValueError, match="cannot be identical"):
        FlashcardCreate(front_text="Same", back_text="Same")


def test_duplicate_front_text_rejected(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Chem Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    db_session.commit()

    flashcard_service.add_flashcard(
        db_session, st.id, FlashcardCreate(front_text="Atomic Number of H", back_text="1")
    )

    # Adding card with same front text (with different spacing/case) in same set returns 409
    with pytest.raises(HTTPException) as exc_info:
        flashcard_service.add_flashcard(
            db_session, st.id, FlashcardCreate(front_text="  atomic number of h  ", back_text="One")
        )
    assert exc_info.value.status_code == 409


def test_suspend_and_soft_delete_flashcard(db_session):
    st = StudySet(id=str(uuid.uuid4()), title="Bio Set", set_type="manual", status="ACTIVE")
    db_session.add(st)
    db_session.commit()

    card = flashcard_service.add_flashcard(
        db_session, st.id, FlashcardCreate(front_text="Mitochondria", back_text="Powerhouse of the cell")
    )

    # Suspend
    updated = flashcard_service.update_flashcard(
        db_session, card.id, FlashcardUpdate(status="SUSPENDED")
    )
    assert updated.status == "SUSPENDED"

    # Soft delete
    deleted = flashcard_service.delete_flashcard(db_session, card.id)
    assert deleted.status == "DELETED"
    assert deleted.deleted_at is not None

    # Fetching deleted card returns 404
    with pytest.raises(HTTPException) as exc_info:
        flashcard_service.get_flashcard(db_session, card.id)
    assert exc_info.value.status_code == 404
