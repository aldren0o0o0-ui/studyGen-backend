"""
tests/test_phase5_study_sets.py
Comprehensive test suite for Study Set CRUD, search, filters, archiving, and soft deletion.
"""
from __future__ import annotations

import uuid
import pytest
from fastapi.testclient import TestClient

from app.database.models import Document, Quiz, StudySet, Flashcard
from app.services import flashcard_service


def test_create_study_set_manual_and_linked(db_session):
    # 1. Create manual study set
    set_manual = flashcard_service.create_study_set(
        db_session,
        type("Req", (), {
            "title": "Manual Biology Set",
            "description": "Cell structure",
            "document_id": None,
            "topic_label": "Biology",
            "set_type": "manual",
            "initial_cards": [],
        })()
    )
    assert set_manual.id is not None
    assert set_manual.set_type == "manual"
    assert set_manual.card_count == 0

    # 2. Create document-linked set
    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="biology.pdf", file_type="pdf", file_size=2048, status="READY")
    db_session.add(doc)
    db_session.commit()

    set_doc = flashcard_service.create_study_set(
        db_session,
        type("Req", (), {
            "title": "Document Bio Set",
            "description": "From PDF",
            "document_id": doc_id,
            "topic_label": "Cells",
            "set_type": "generated_from_document",
            "initial_cards": [],
        })()
    )
    assert set_doc.document_id == doc_id
    assert set_doc.set_type == "generated_from_document"


def test_list_search_and_filter_study_sets(db_session):
    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="chemistry.pdf", file_type="pdf", file_size=1024, status="READY")
    db_session.add(doc)

    set1 = StudySet(
        id=str(uuid.uuid4()), title="Organic Chemistry 1", topic_label="Chemistry",
        set_type="manual", status="ACTIVE", document_id=doc_id
    )
    set2 = StudySet(
        id=str(uuid.uuid4()), title="Physics Motion", topic_label="Physics",
        set_type="generated_from_document", status="ACTIVE", document_id=doc_id
    )
    set3 = StudySet(
        id=str(uuid.uuid4()), title="Archived Math", topic_label="Math",
        set_type="manual", status="ARCHIVED"
    )
    db_session.add_all([set1, set2, set3])
    db_session.commit()

    # Search by title
    sets, total = flashcard_service.list_study_sets(db_session, search="Organic")
    assert total == 1
    assert sets[0].title == "Organic Chemistry 1"

    # Filter by topic
    sets, total = flashcard_service.list_study_sets(db_session, topic="Physics")
    assert total == 1
    assert sets[0].topic_label == "Physics"

    # Filter by document_id
    sets, total = flashcard_service.list_study_sets(db_session, document_id=doc_id)
    assert total == 2

    # Filter by status ACTIVE (excludes ARCHIVED by default)
    sets, total = flashcard_service.list_study_sets(db_session, status="ACTIVE")
    assert total == 2


def test_archive_and_unarchive_study_set(db_session):
    set1 = StudySet(id=str(uuid.uuid4()), title="History Set", set_type="manual", status="ACTIVE")
    db_session.add(set1)
    db_session.commit()

    # Archive
    updated = flashcard_service.update_study_set(
        db_session, set1.id, type("Req", (), {"title": None, "description": None, "topic_label": None, "status": "ARCHIVED"})()
    )
    assert updated.status == "ARCHIVED"

    # Unarchive
    updated = flashcard_service.update_study_set(
        db_session, set1.id, type("Req", (), {"title": None, "description": None, "topic_label": None, "status": "ACTIVE"})()
    )
    assert updated.status == "ACTIVE"


def test_soft_delete_study_set_preserves_source_resources(db_session):
    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="history.pdf", file_type="pdf", file_size=512, status="READY")
    db_session.add(doc)

    st = StudySet(id=str(uuid.uuid4()), title="WWII History", document_id=doc_id, set_type="manual", status="ACTIVE")
    db_session.add(st)
    db_session.flush()

    card = Flashcard(
        id=str(uuid.uuid4()), study_set_id=st.id, front_text="WWII start year?", back_text="1939",
        card_type="basic", difficulty="easy", generation_method="manual", position=1, status="ACTIVE"
    )
    db_session.add(card)
    db_session.commit()

    # Soft delete study set
    deleted_st = flashcard_service.delete_study_set(db_session, st.id)
    assert deleted_st.status == "DELETED"
    assert deleted_st.deleted_at is not None

    # Source document must still exist intact
    saved_doc = db_session.get(Document, doc_id)
    assert saved_doc is not None
    assert saved_doc.status == "READY"

    # Cards in deleted set must be soft-deleted
    db_session.refresh(card)
    assert card.status == "DELETED"
    assert card.deleted_at is not None
