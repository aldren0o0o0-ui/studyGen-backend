"""
tests/test_folder_quiz.py — Wave 3 Folder Quiz Generation Quality & Security Suite
Tests multi-document folder quizzes, recursive subfolder quizzes, selected document quizzes,
QuizCoveragePlanner allocations, anti-domination, provenance mapping, and security boundaries.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from app.database.models.auth import LEGACY_OWNER_ID, User
from app.database.models.document import Document
from app.database.models.folder import Folder
from app.database.models.quiz import Quiz, Question, QuizSourceDocument
from app.schemas.ai_scope import AIScopeType
from app.services.quiz_coverage_planner import QuizCoveragePlanner, ScopedDocumentInfo


# ===========================================================================
# COVERAGE PLANNER DETERMINISTIC UNIT TESTS
# ===========================================================================

def test_planner_single_document():
    doc = ScopedDocumentInfo(document_id="doc_1", filename="Doc1.pdf", chunk_count=10)
    plan = QuizCoveragePlanner.plan_coverage([doc], 10, question_types=["multiple_choice", "true_false"])

    assert plan.total_questions == 10
    assert plan.allocations_per_document == {"doc_1": 10}
    assert plan.type_allocations == {"multiple_choice": 5, "true_false": 5}


def test_planner_balanced_allocation_and_anti_domination():
    """Verify large document does not crowd out smaller documents under anti-domination cap."""
    docs = [
        ScopedDocumentInfo(document_id="large_textbook", filename="Textbook.pdf", chunk_count=200),
        ScopedDocumentInfo(document_id="small_notes_1", filename="Notes1.pdf", chunk_count=8),
        ScopedDocumentInfo(document_id="small_notes_2", filename="Notes2.pdf", chunk_count=6),
    ]
    # 20 questions requested across 3 documents
    plan = QuizCoveragePlanner.plan_coverage(docs, 20, coverage_mode="balanced", max_share_per_doc=0.50)

    assert plan.total_questions == 20
    alloc = plan.allocations_per_document

    # Total must be exactly 20
    assert sum(alloc.values()) == 20
    # Every document must receive at least 1 question
    assert alloc["large_textbook"] >= 1
    assert alloc["small_notes_1"] >= 1
    assert alloc["small_notes_2"] >= 1
    # Large textbook must be capped at 50% (<= 10 questions)
    assert alloc["large_textbook"] <= 10


def test_planner_focused_mode():
    """Verify focused document receives ~70% of questions."""
    docs = [
        ScopedDocumentInfo(document_id="doc_focus", filename="Focus.pdf", chunk_count=10, is_focused=True),
        ScopedDocumentInfo(document_id="doc_other_1", filename="Other1.pdf", chunk_count=10, is_focused=False),
        ScopedDocumentInfo(document_id="doc_other_2", filename="Other2.pdf", chunk_count=10, is_focused=False),
    ]
    plan = QuizCoveragePlanner.plan_coverage(docs, 10, coverage_mode="focused")

    alloc = plan.allocations_per_document
    assert sum(alloc.values()) == 10
    assert alloc["doc_focus"] >= 7


def test_planner_question_types_and_mixed_difficulty():
    """Verify question types and mixed difficulty partitions with diff <= 1."""
    types = ["multiple_choice", "true_false", "identification"]
    alloc = QuizCoveragePlanner.allocate_question_types(types, 10)
    assert alloc == {"multiple_choice": 4, "true_false": 3, "identification": 3}

    diff_alloc = QuizCoveragePlanner.allocate_difficulties("mixed", 10)
    assert diff_alloc == {"easy": 4, "medium": 3, "hard": 3}


# ===========================================================================
# END-TO-END FOLDER & MULTI-DOCUMENT QUIZ TESTS
# ===========================================================================

def test_folder_quiz_direct_generation(client: TestClient, db_session, mock_collection):
    """Test generating a quiz from direct documents in a folder."""
    user_id = LEGACY_OWNER_ID

    # 1. Setup folder & documents
    folder = Folder(id="f_direct", user_id=user_id, name="Computer Systems")
    db_session.add(folder)

    doc_a = Document(
        id="doc_a",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="OS.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    doc_b = Document(
        id="doc_b",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="Networks.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    # Add mock chunks to collection
    mock_collection.add(
        documents=["OS kernels manage memory and processes.", "TCP/IP protocol defines networking."],
        ids=["doc_a:0", "doc_b:0"],
        metadatas=[
            {"document_id": "doc_a", "user_id": user_id, "filename": "OS.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_b", "user_id": user_id, "filename": "Networks.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    # 2. Call POST /folders/f_direct/quiz
    response = client.post(
        f"/folders/{folder.id}/quiz",
        json={
            "include_subfolders": False,
            "question_count": 2,
            "difficulty": "medium",
            "question_types": ["multiple_choice", "true_false"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["scope_type"] == "FOLDER"
    assert data["folder_id"] == folder.id
    assert data["source_document_count"] == 2
    assert len(data["questions"]) >= 1

    # 3. Verify snapshot persistence in DB
    quiz_id = data["quiz_id"]
    db_quiz = db_session.get(Quiz, quiz_id)
    assert db_quiz is not None
    assert db_quiz.scope_type == "FOLDER"
    assert db_quiz.folder_id == folder.id

    # Verify QuizSourceDocument join rows
    src_docs = db_session.query(QuizSourceDocument).filter(QuizSourceDocument.quiz_id == quiz_id).all()
    assert len(src_docs) == 2
    assert {s.document_id for s in src_docs} == {"doc_a", "doc_b"}


def test_folder_quiz_recursive_subfolders(client: TestClient, db_session, mock_collection):
    """Test generating a quiz from folder including recursive child subfolders."""
    user_id = LEGACY_OWNER_ID

    parent_folder = Folder(id="f_parent", user_id=user_id, name="Parent Course")
    child_folder = Folder(id="f_child", user_id=user_id, name="Child Module", parent_folder_id=parent_folder.id)
    db_session.add_all([parent_folder, child_folder])

    doc_parent = Document(
        id="doc_p",
        user_id=user_id,
        folder_id=parent_folder.id,
        original_filename="Syllabus.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=3,
    )
    doc_child = Document(
        id="doc_c",
        user_id=user_id,
        folder_id=child_folder.id,
        original_filename="Module1.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=4,
    )
    db_session.add_all([doc_parent, doc_child])
    db_session.commit()

    mock_collection.add(
        documents=["Course syllabus and policies.", "Module 1 detailed concepts."],
        ids=["doc_p:0", "doc_c:0"],
        metadatas=[
            {"document_id": "doc_p", "user_id": user_id, "filename": "Syllabus.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_c", "user_id": user_id, "filename": "Module1.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    # Call POST /folders/f_parent/quiz with include_subfolders=True
    response = client.post(
        f"/folders/{parent_folder.id}/quiz",
        json={
            "include_subfolders": True,
            "question_count": 2,
            "difficulty": "medium",
            "question_types": ["multiple_choice", "true_false"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["scope_type"] == "FOLDER"
    assert data["include_subfolders"] is True
    assert data["source_document_count"] == 2


def test_selected_documents_quiz_generation(client: TestClient, db_session, mock_collection):
    """Test generating a quiz from multi-selected documents."""
    user_id = LEGACY_OWNER_ID

    doc_1 = Document(
        id="doc_sel_1",
        user_id=user_id,
        original_filename="Selected1.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=4,
    )
    doc_2 = Document(
        id="doc_sel_2",
        user_id=user_id,
        original_filename="Selected2.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=4,
    )
    db_session.add_all([doc_1, doc_2])
    db_session.commit()

    mock_collection.add(
        documents=["Selected document 1 topic.", "Selected document 2 topic."],
        ids=["doc_sel_1:0", "doc_sel_2:0"],
        metadatas=[
            {"document_id": "doc_sel_1", "user_id": user_id, "filename": "Selected1.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "doc_sel_2", "user_id": user_id, "filename": "Selected2.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    response = client.post(
        "/quizzes/generate",
        json={
            "scope_type": "SELECTED_DOCUMENTS",
            "document_ids": ["doc_sel_1", "doc_sel_2"],
            "question_count": 2,
            "difficulty": "medium",
            "question_types": ["multiple_choice", "true_false"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["scope_type"] == "SELECTED_DOCUMENTS"
    assert data["source_document_count"] == 2


def test_single_document_quiz_regression(client: TestClient, db_session, mock_collection):
    """Test that existing single-document quiz generation continues working flawlessly."""
    user_id = LEGACY_OWNER_ID

    doc = Document(
        id="doc_single",
        user_id=user_id,
        original_filename="SingleFile.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=3,
    )
    db_session.add(doc)
    db_session.commit()

    mock_collection.add(
        documents=["Single file document content."],
        ids=["doc_single:0"],
        metadatas=[
            {"document_id": "doc_single", "user_id": user_id, "filename": "SingleFile.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    response = client.post(
        "/quizzes/generate",
        json={
            "document_id": "doc_single",
            "question_count": 2,
            "difficulty": "easy",
            "question_types": ["multiple_choice", "true_false"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == "doc_single"
    assert data["quiz_title"] == "Quiz on SingleFile.pdf"


def test_cross_tenant_quiz_isolation(client: TestClient, db_session):
    """Ensure user cannot generate a quiz referencing another tenant's folder or documents."""
    other_user = User(
        id="user_victim",
        email="victim@studygen.io",
        normalized_email="victim@studygen.io",
        password_hash="pw",
        display_name="Victim User",
        status="ACTIVE",
    )
    db_session.add(other_user)

    secret_folder = Folder(id="f_victim", user_id="user_victim", name="Secret Research")
    secret_doc = Document(
        id="doc_victim",
        user_id="user_victim",
        folder_id=secret_folder.id,
        original_filename="Confidential.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=5,
    )
    db_session.add_all([secret_folder, secret_doc])
    db_session.commit()

    # Default user attempts to generate quiz on victim's folder
    res_folder = client.post(f"/folders/{secret_folder.id}/quiz", json={"question_count": 5})
    assert res_folder.status_code == 404

    # Default user attempts to generate quiz referencing victim's document ID
    res_doc = client.post(
        "/quizzes/generate",
        json={"scope_type": "SELECTED_DOCUMENTS", "document_ids": ["doc_victim"], "question_count": 5},
    )
    assert res_doc.status_code in (404, 422)


def test_trashed_document_excluded_from_quiz(client: TestClient, db_session):
    """Ensure trashed documents are rejected from quiz generation."""
    from datetime import datetime, timezone
    user_id = LEGACY_OWNER_ID

    trashed_doc = Document(
        id="doc_trashed",
        user_id=user_id,
        original_filename="Trashed.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(trashed_doc)
    db_session.commit()

    res = client.post("/quizzes/generate", json={"document_id": "doc_trashed", "question_count": 5})
    assert res.status_code in (404, 422)


def test_question_validation_rejects_fake_sources_and_invalid_mcq():
    """Verify question validation filters out questions with invalid options and fake source labels."""
    from app.services.question_validation import validate_questions, SelectedChunk

    chunks = [
        SelectedChunk(
            text="Operating systems manage hardware resources.",
            document_id="doc_os",
            filename="OS.pdf",
            chunk_index=0,
            page_number=1,
        )
    ]

    raw_questions = [
        # Valid question referencing real SOURCE_1
        {
            "question_type": "multiple_choice",
            "question": "What does an OS manage?",
            "options": ["Hardware resources", "Cooking recipes", "Music streams", "Weather forecasts"],
            "correct_answer": "Hardware resources",
            "accepted_answers": [],
            "source_labels": ["SOURCE_1"],
        },
        # Invalid: correct answer not in options
        {
            "question_type": "multiple_choice",
            "question": "What is Linux?",
            "options": ["A kernel", "A distro", "A browser", "An IDE"],
            "correct_answer": "An operating system",
            "accepted_answers": [],
            "source_labels": ["SOURCE_1"],
        },
        # Invalid: references fake source SOURCE_99
        {
            "question_type": "multiple_choice",
            "question": "What is Python?",
            "options": ["A language", "A snake", "A car", "A game"],
            "correct_answer": "A language",
            "accepted_answers": [],
            "source_labels": ["SOURCE_99"],
        },
    ]

    validated = validate_questions(raw_questions, chunks, require_sources=True)
    assert len(validated) == 1
    assert validated[0].question_text == "What does an OS manage?"
    assert validated[0].sources[0].document_id == "doc_os"
    assert validated[0].sources[0].filename == "OS.pdf"


def test_list_and_get_quiz_detail_scope_metadata(client: TestClient, db_session, mock_collection):
    """Verify GET /quizzes and GET /quizzes/{id} return scope metadata."""
    user_id = LEGACY_OWNER_ID

    folder = Folder(id="f_meta", user_id=user_id, name="Algorithms")
    db_session.add(folder)

    doc = Document(
        id="doc_algo",
        user_id=user_id,
        folder_id=folder.id,
        original_filename="Sort.pdf",
        file_type="pdf",
        status="READY",
        file_size=1024,
        chunk_count=2,
    )
    db_session.add(doc)
    db_session.commit()

    mock_collection.add(
        documents=["Quicksort average time complexity is O(N log N)."],
        ids=["doc_algo:0"],
        metadatas=[
            {"document_id": "doc_algo", "user_id": user_id, "filename": "Sort.pdf", "chunk_index": 0, "page_number": 1},
        ],
    )

    # 1. Generate folder quiz
    res_gen = client.post(
        f"/folders/{folder.id}/quiz",
        json={"question_count": 2, "difficulty": "medium", "question_types": ["multiple_choice"]},
    )
    assert res_gen.status_code == 200
    quiz_id = res_gen.json()["quiz_id"]

    # 2. List quizzes
    res_list = client.get("/quizzes")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["total"] >= 1
    item = next(i for i in list_data["items"] if i["quiz_id"] == quiz_id)
    assert item["scope_type"] == "FOLDER"
    assert item["folder_id"] == folder.id

    # 3. Get quiz detail
    res_detail = client.get(f"/quizzes/{quiz_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["scope_type"] == "FOLDER"
    assert detail["folder_id"] == folder.id
    assert len(detail["questions"]) >= 1

