"""tests/test_folder_flashcards.py — Wave 4 Folder Flashcard Generation Quality & Security Suite
Tests multi-document folder flashcards, recursive subfolder flashcards, selected document flashcards,
FlashcardCoveragePlanner allocations, anti-domination, focus modes, provenance mapping, and SM-2 spaced repetition integration.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from app.database.models.auth import LEGACY_OWNER_ID, User
from app.database.models.document import Document
from app.database.models.folder import Folder
from app.database.models.flashcard import StudySet, StudySetSourceDocument, Flashcard, FlashcardReviewState
from app.schemas.ai_scope import AIScopeType
from app.services.flashcard_coverage_planner import FlashcardCoveragePlanner, ScopedDocumentInfo
from app.services.flashcard_validation_service import filter_and_deduplicate_cards, validate_flashcard_dict


# ===========================================================================
# COVERAGE PLANNER DETERMINISTIC UNIT TESTS
# ===========================================================================

def test_planner_single_document():
    doc = ScopedDocumentInfo(document_id="doc_1", filename="Doc1.pdf", chunk_count=10)
    plan = FlashcardCoveragePlanner.plan_coverage([doc], 10, card_types=["concept", "definition"])

    assert plan.total_cards == 10
    assert plan.allocations_per_document == {"doc_1": 10}
    assert plan.type_allocations == {"concept": 5, "definition": 5}


def test_planner_balanced_allocation_and_anti_domination():
    """Verify large document does not crowd out smaller documents under anti-domination cap."""
    docs = [
        ScopedDocumentInfo(document_id="large_textbook", filename="Textbook.pdf", chunk_count=200),
        ScopedDocumentInfo(document_id="small_notes_1", filename="Notes1.pdf", chunk_count=8),
        ScopedDocumentInfo(document_id="small_notes_2", filename="Notes2.pdf", chunk_count=6),
    ]
    # 20 cards requested across 3 documents
    plan = FlashcardCoveragePlanner.plan_coverage(docs, 20, focus_mode="balanced", max_share_per_doc=0.50)

    assert plan.total_cards == 20
    alloc = plan.allocations_per_document

    # Total must be exactly 20
    assert sum(alloc.values()) == 20
    # Every document must receive at least 1 card
    assert alloc["large_textbook"] >= 1
    assert alloc["small_notes_1"] >= 1
    assert alloc["small_notes_2"] >= 1
    # Large textbook must be capped at 50% (<= 10 cards)
    assert alloc["large_textbook"] <= 10


def test_planner_focus_modes():
    """Verify focus modes prioritize corresponding card types."""
    types = ["concept", "definition", "question_answer"]

    # Definitions mode
    def_plan = FlashcardCoveragePlanner.allocate_card_types(types, 10, focus_mode="definitions")
    assert def_plan["definition"] >= 7

    # Key Concepts mode
    concept_plan = FlashcardCoveragePlanner.allocate_card_types(types, 10, focus_mode="key_concepts")
    assert concept_plan["concept"] >= 6

    # Mixed difficulty
    diff_alloc = FlashcardCoveragePlanner.allocate_difficulties("mixed", 10)
    assert diff_alloc == {"easy": 4, "medium": 3, "hard": 3}


def test_validation_and_deduplication():
    """Verify exact and near-duplicate cards are properly filtered."""
    source_map = {
        "SOURCE_1": {"document_id": "doc_1", "chunk_index": 0, "page_number": 1, "filename": "test.pdf", "excerpt": "..."}
    }
    resolved_ids = {"doc_1"}

    raw_cards = [
        # Card 1: Valid
        {
            "front": "What is the primary function of an operating system?",
            "back": "To manage computer hardware and software resources.",
            "card_type": "concept",
            "difficulty": "medium",
            "source_labels": ["SOURCE_1"],
        },
        # Card 2: Exact duplicate
        {
            "front": "What is the primary function of an operating system?",
            "back": "To manage computer hardware.",
            "card_type": "concept",
            "difficulty": "medium",
            "source_labels": ["SOURCE_1"],
        },
        # Card 3: Near duplicate
        {
            "front": "What is the main function of an operating system?",
            "back": "To manage hardware and software.",
            "card_type": "concept",
            "difficulty": "medium",
            "source_labels": ["SOURCE_1"],
        },
        # Card 4: Invalid fake source
        {
            "front": "What is virtual memory?",
            "back": "A memory management capability of an OS.",
            "card_type": "concept",
            "difficulty": "medium",
            "source_labels": ["SOURCE_999"],
        },
    ]

    valid, inv, dup = filter_and_deduplicate_cards(
        raw_cards,
        source_map=source_map,
        resolved_doc_ids=resolved_ids,
    )

    assert len(valid) == 1
    assert valid[0]["front"] == "What is the primary function of an operating system?"
    assert dup == 2  # 1 exact + 1 near duplicate
    assert inv == 1  # 1 fake source


# ===========================================================================
# END-TO-END FOLDER & MULTI-DOCUMENT FLASHCARD TESTS
# ===========================================================================

def test_folder_flashcards_direct_generation(client: TestClient, db_session, mock_collection):
    """Test generating flashcards from direct documents in a folder."""
    user_id = LEGACY_OWNER_ID

    folder = Folder(id="f_flash_direct", user_id=user_id, name="Databases")
    db_session.add(folder)

    doc_a = Document(
        id="doc_fa",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="SQL.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    doc_b = Document(
        id="doc_fb",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="NoSQL.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    # Add mock chunks to collection
    mock_collection.add(
        documents=["SQL is a structured query language.", "NoSQL databases store non-relational data."],
        ids=["doc_fa:0", "doc_fb:0"],
        metadatas=[
            {"document_id": "doc_fa", "user_id": user_id, "filename": "SQL.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_fb", "user_id": user_id, "filename": "NoSQL.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    mock_ai_response = MagicMock()
    mock_ai_response.text = json.dumps({
        "study_set_title": "Databases Flashcards",
        "cards": [
            {
                "front": "What does SQL stand for?",
                "back": "Structured Query Language.",
                "hint": "Database language",
                "card_type": "definition",
                "difficulty": "easy",
                "topic": "SQL",
                "source_labels": ["SOURCE_1"],
            },
            {
                "front": "What is NoSQL?",
                "back": "Non-relational database management systems.",
                "hint": "Alternative to SQL",
                "card_type": "concept",
                "difficulty": "medium",
                "topic": "NoSQL",
                "source_labels": ["SOURCE_2"],
            },
        ],
    })

    with patch("config.settings.gemini_model", "mock-gemini"), \
         patch("app.routers.flashcards.run_in_threadpool", side_effect=lambda f: f()):
        client.app.state.gemini_model.generate_content.return_value = mock_ai_response
        client.app.state.gemini_available = True

        res = client.post(
            f"/folders/{folder.id}/flashcards",
            json={
                "include_subfolders": False,
                "card_count": 2,
                "difficulty": "medium",
                "focus_mode": "balanced",
            },
        )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["scope_type"] == "FOLDER"
    assert data["folder_id"] == folder.id
    assert data["include_subfolders"] is False
    assert data["source_document_count"] == 2
    assert len(data["cards"]) == 2

    # Verify DB snapshot
    st = db_session.get(StudySet, data["study_set_id"])
    assert st is not None
    assert st.scope_type == "FOLDER"
    assert len(st.source_documents) == 2
    doc_ids_persisted = {s.document_id for s in st.source_documents}
    assert doc_ids_persisted == {"doc_fa", "doc_fb"}

    # Verify card review state
    card = st.cards[0]
    assert card.review_state is not None
    assert card.review_state.review_status == "NEW"
    assert card.review_state.interval_days == 0


def test_folder_flashcards_recursive_subfolders(client: TestClient, db_session, mock_collection):
    """Test generating flashcards across a parent folder and child subfolders."""
    user_id = LEGACY_OWNER_ID

    parent_folder = Folder(id="f_parent_fl", user_id=user_id, name="Math")
    sub_folder = Folder(id="f_sub_fl", user_id=user_id, parent_folder_id="f_parent_fl", name="Calculus")
    db_session.add_all([parent_folder, sub_folder])

    doc_parent = Document(
        id="doc_m1",
        user_id=user_id,
        folder_id=parent_folder.id,
        original_filename="Algebra.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    doc_sub = Document(
        id="doc_m2",
        user_id=user_id,
        folder_id=sub_folder.id,
        original_filename="Derivatives.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    db_session.add_all([doc_parent, doc_sub])
    db_session.commit()

    mock_collection.add(
        documents=["Algebra text", "Derivatives text"],
        ids=["doc_m1:0", "doc_m2:0"],
        metadatas=[
            {"document_id": "doc_m1", "user_id": user_id, "filename": "Algebra.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_m2", "user_id": user_id, "filename": "Derivatives.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    mock_ai_response = MagicMock()
    mock_ai_response.text = json.dumps({
        "study_set_title": "Math Complete Flashcards",
        "cards": [
            {
                "front": "What is a derivative?",
                "back": "Rate of change of a function.",
                "hint": "Calculus concept",
                "card_type": "definition",
                "difficulty": "medium",
                "topic": "Calculus",
                "source_labels": ["SOURCE_2"],
            },
        ],
    })

    with patch("config.settings.gemini_model", "mock-gemini"), \
         patch("app.routers.flashcards.run_in_threadpool", side_effect=lambda f: f()):
        client.app.state.gemini_model.generate_content.return_value = mock_ai_response
        client.app.state.gemini_available = True

        res = client.post(
            f"/folders/{parent_folder.id}/flashcards",
            json={"include_subfolders": True, "card_count": 1, "focus_mode": "key_concepts"},
        )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["scope_type"] == "FOLDER"
    assert data["include_subfolders"] is True
    assert data["source_document_count"] == 2


def test_selected_documents_flashcards_generation(client: TestClient, db_session, mock_collection):
    """Test generating flashcards for explicit multi-selected documents."""
    user_id = LEGACY_OWNER_ID

    doc_1 = Document(
        id="doc_sel_1",
        user_id=user_id,
        original_filename="Networks.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    doc_2 = Document(
        id="doc_sel_2",
        user_id=user_id,
        original_filename="Security.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    db_session.add_all([doc_1, doc_2])
    db_session.commit()

    mock_collection.add(
        documents=["Networks text", "Security text"],
        ids=["doc_sel_1:0", "doc_sel_2:0"],
        metadatas=[
            {"document_id": "doc_sel_1", "user_id": user_id, "filename": "Networks.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_sel_2", "user_id": user_id, "filename": "Security.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    mock_ai_response = MagicMock()
    mock_ai_response.text = json.dumps({
        "study_set_title": "Selected Docs Deck",
        "cards": [
            {
                "front": "What is TCP?",
                "back": "Transmission Control Protocol.",
                "hint": None,
                "card_type": "concept",
                "difficulty": "medium",
                "topic": "Networks",
                "source_labels": ["SOURCE_1"],
            },
        ],
    })

    with patch("config.settings.gemini_model", "mock-gemini"), \
         patch("app.routers.flashcards.run_in_threadpool", side_effect=lambda f: f()):
        client.app.state.gemini_model.generate_content.return_value = mock_ai_response
        client.app.state.gemini_available = True

        res = client.post(
            "/flashcards/generate",
            json={
                "scope_type": "SELECTED_DOCUMENTS",
                "document_ids": ["doc_sel_1", "doc_sel_2"],
                "card_count": 1,
            },
        )

    assert res.status_code == 201, res.text
    data = res.json()
    assert data["scope_type"] == "SELECTED_DOCUMENTS"
    assert data["source_document_count"] == 2


def test_cross_tenant_flashcard_isolation(client: TestClient, db_session):
    """Test that User A cannot generate flashcards from User B's folder or documents."""
    other_user = User(
        id="user_victim_fl",
        email="victim_fl@example.com",
        normalized_email="victim_fl@example.com",
        display_name="Victim User",
        username="victim_fl",
        password_hash="xxx",
    )
    db_session.add(other_user)

    folder_b = Folder(id="f_other_user_fl", user_id=other_user.id, name="Secret Material")
    doc_b = Document(
        id="doc_secret_fl",
        user_id=other_user.id,
        folder_id=folder_b.id,
        original_filename="Confidential.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
    )
    db_session.add_all([folder_b, doc_b])
    db_session.commit()

    with patch("app.routers.flashcards.run_in_threadpool", side_effect=lambda f: f()):
        res = client.post(
            f"/folders/{folder_b.id}/flashcards",
            json={"card_count": 5},
        )

    assert res.status_code in (400, 403, 404), res.text


def test_trashed_document_excluded_from_flashcards(client: TestClient, db_session):
    """Test that trashed documents are excluded from flashcard scope."""
    from datetime import datetime, timezone

    user_id = LEGACY_OWNER_ID
    folder = Folder(id="f_trash_scope_fl", user_id=user_id, name="Trash Only")
    doc_trashed = Document(
        id="doc_trashed_fl",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="Deleted.pdf",
        file_type="pdf",
        status="READY",
        deleted_at=datetime.now(timezone.utc),
        file_size=1024,
    )
    db_session.add_all([folder, doc_trashed])
    db_session.commit()

    with patch("app.routers.flashcards.run_in_threadpool", side_effect=lambda f: f()):
        res = client.post(
            f"/folders/{folder.id}/flashcards",
            json={"card_count": 5},
        )

    assert res.status_code in (400, 422), res.text


def test_spaced_repetition_compatibility_on_folder_deck(client: TestClient, db_session):
    """Verify that cards generated from a folder deck seamlessly participate in SM-2 spaced repetition review."""
    user_id = LEGACY_OWNER_ID

    folder = Folder(id="f_sr_deck", user_id=user_id, name="Physics")
    doc = Document(id="doc_sr_1", user_id=user_id, folder_id=folder.id, original_filename="Forces.pdf", file_type="pdf", status="READY", file_size=1024)
    st = StudySet(
        id="set_sr_1",
        user_id=user_id,
        title="Physics Forces Flashcards",
        scope_type="FOLDER",
        folder_id=folder.id,
        set_type="generated_from_folder",
        card_count=1,
    )
    card = Flashcard(
        id="card_sr_1",
        study_set_id=st.id,
        document_id=doc.id,
        front_text="What is Newton's Second Law?",
        back_text="Force equals mass times acceleration (F=ma).",
        generation_method="folder_ai",
        position=1,
    )
    state = FlashcardReviewState(
        id="state_sr_1",
        flashcard_id=card.id,
        review_status="NEW",
        ease_level=2.5,
        review_count=0,
        interval_days=0,
    )
    db_session.add_all([folder, doc, st, card, state])
    db_session.commit()

    with patch("app.routers.flashcard_review_sessions.run_in_threadpool", side_effect=lambda f: f()):
        # 1. Start review session
        start_res = client.post(
            "/flashcard-review-sessions",
            json={"study_set_id": st.id, "session_mode": "all_cards"},
        )
        assert start_res.status_code == 201, start_res.text
        session_data = start_res.json()
        session_id = session_data["session_id"]
        item_id = session_data["items"][0]["item_id"]

        # 2. Reveal card
        rev_res = client.post(f"/flashcard-review-sessions/{session_id}/items/{item_id}/reveal")
        assert rev_res.status_code == 200, rev_res.text

        # 3. Rate card as 'good'
        rate_res = client.post(
            f"/flashcard-review-sessions/{session_id}/items/{item_id}/rate",
            json={"rating": "good"},
        )
        assert rate_res.status_code == 200, rate_res.text
        rate_data = rate_res.json()
        assert rate_data["interval_days"] == 1
        assert rate_data["review_status"] == "LEARNING"

    # Verify state in DB
    db_session.refresh(state)
    assert state.review_status == "LEARNING"
    assert state.interval_days == 1
    assert state.consecutive_correct == 1
    assert state.next_review_at is not None
