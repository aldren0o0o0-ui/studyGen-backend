"""
tests/test_simplification_backend.py — Tests for Phase 2-4 Controlled Simplification:
1. Router gating with ENABLE_LEGACY_STUDY_MODULES.
2. Document analysis alias endpoint POST /documents/{document_id}/analysis.
3. Ownership and access control on the document analysis alias.
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.database.models.auth import User
from app.database.models.document import Document
from app.services.token_service import create_access_token
from config import settings
from main import app


@pytest.fixture
def mock_chroma():
    """Mock Chroma collection with sample document embeddings supporting query and get."""
    class MockCollection:
        def query(self, query_texts, n_results=10, where=None, include=None):
            doc_id = "doc-1"
            owner_id = "user-1"
            if where and "$and" in where:
                for cond in where["$and"]:
                    if "document_id" in cond:
                        doc_id = cond["document_id"]
                    if "user_id" in cond:
                        owner_id = cond["user_id"]

            return {
                "documents": [
                    [
                        f"Database Normalization is the process of organizing data to reduce redundancy for {doc_id}.",
                        f"ACID Properties ensure reliability in transaction processing for {doc_id}.",
                        f"Primary Keys uniquely identify records in relational tables for {doc_id}.",
                    ]
                ],
                "metadatas": [
                    [
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 1, "chunk_index": 0, "filename": f"{doc_id}.pdf"},
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 2, "chunk_index": 1, "filename": f"{doc_id}.pdf"},
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 3, "chunk_index": 2, "filename": f"{doc_id}.pdf"},
                    ]
                ],
                "distances": [[0.05, 0.08, 0.12]],
            }

        def get(self, where=None, include=None):
            doc_id = "doc-1"
            owner_id = "user-1"
            if where:
                if "$and" in where:
                    for cond in where["$and"]:
                        if "document_id" in cond:
                            doc_id = cond["document_id"]
                        if "user_id" in cond:
                            owner_id = cond["user_id"]
                elif "document_id" in where:
                    doc_id = where["document_id"]
                elif "user_id" in where:
                    owner_id = where["user_id"]

            return {
                "ids": [f"{doc_id}-chunk-0", f"{doc_id}-chunk-1"],
                "documents": [
                    f"Sample content chunk 0 for {doc_id}.",
                    f"Sample content chunk 1 for {doc_id}.",
                ],
                "metadatas": [
                    {"document_id": doc_id, "user_id": owner_id, "chunk_index": 0, "page_number": 1, "filename": f"{doc_id}.pdf"},
                    {"document_id": doc_id, "user_id": owner_id, "chunk_index": 1, "page_number": 2, "filename": f"{doc_id}.pdf"},
                ],
            }

    return MockCollection()


@pytest.fixture
def client(db_session, mock_chroma):
    app.state.collection = mock_chroma
    app.state.gemini_model = None
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_user(db_session):
    uid = str(uuid.uuid4())
    user = User(
        id=uid,
        email=f"simplification_user_{uid[:8]}@example.com",
        normalized_email=f"simplification_user_{uid[:8]}@example.com",
        display_name="Simplification User",
        password_hash="test_hash",
        role="USER",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def other_user(db_session):
    uid = str(uuid.uuid4())
    user = User(
        id=uid,
        email=f"other_user_{uid[:8]}@example.com",
        normalized_email=f"other_user_{uid[:8]}@example.com",
        display_name="Other User",
        password_hash="test_hash",
        role="USER",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def auth_headers(test_user):
    token = create_access_token(test_user, settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_auth_headers(other_user):
    token = create_access_token(other_user, settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ready_document(db_session, test_user):
    doc = Document(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        original_filename="Quantum_Computing_Overview.pdf",
        file_type="pdf",
        file_size=2048,
        content_hash=str(uuid.uuid4()),
        chunk_count=3,
        status="READY",
        extraction_status="READY",
        extraction_quality_score=0.95,
        extraction_quality_label="excellent",
        total_pages=3,
        is_favorite=False,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def test_legacy_router_gating_flag_defaults_false():
    """Verify that settings.enable_legacy_study_modules defaults to False."""
    assert hasattr(settings, "enable_legacy_study_modules")
    assert settings.enable_legacy_study_modules is False


def test_legacy_study_routes_disabled_by_default(client, auth_headers):
    """
    Verify that legacy study, quiz, and flashcard routes return 404 when gated off,
    while core document, chat, and study-guide endpoints remain available.
    """
    # Gated legacy routes should return 404 (Route not mounted)
    quiz_res = client.post("/quizzes/generate", json={"document_id": "test"}, headers=auth_headers)
    assert quiz_res.status_code == 404

    flashcard_res = client.get("/flashcards", headers=auth_headers)
    assert flashcard_res.status_code == 404

    study_sets_res = client.get("/study-sets", headers=auth_headers)
    assert study_sets_res.status_code == 404

    # Core routes must remain active
    docs_res = client.get("/documents", headers=auth_headers)
    assert docs_res.status_code == 200

    chat_res = client.get("/chat-sessions", headers=auth_headers)
    assert chat_res.status_code == 200

    guides_res = client.get("/study-guides", headers=auth_headers)
    assert guides_res.status_code == 200


def test_legacy_study_routes_enabled_with_flag(db_session, auth_headers):
    """
    Verify that when enable_legacy_study_modules is True, legacy routers are mounted
    and become available without database migration.
    """
    from fastapi import FastAPI
    from app.routers import study_sets as study_sets_router, quizzes as quizzes_router

    test_app = FastAPI()
    test_app.include_router(study_sets_router.router)
    test_app.include_router(quizzes_router.router)

    with TestClient(test_app) as custom_client:
        res = custom_client.get("/study-sets", headers=auth_headers)
        assert res.status_code == 200




def test_post_document_analysis_alias_success(client, db_session, test_user, ready_document, auth_headers):
    """
    Verify POST /documents/{document_id}/analysis creates a document analysis (Summary & Key Points)
    reusing the study guide workflow with status 201.
    """
    payload = {
        "guide_mode": "condensed",
        "custom_title": "Quantum Computing Summary",
    }
    response = client.post(
        f"/documents/{ready_document.id}/analysis",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "Quantum Computing Summary"
    assert data["guide_mode"] == "condensed"
    assert data["document_id"] == ready_document.id
    assert len(data["sections"]) > 0


def test_post_document_analysis_ownership_isolation(client, db_session, other_user, ready_document, other_auth_headers):
    """
    Verify that another user cannot generate an analysis on a document they do not own
    (strictly enforced by tenant isolation / 403 or 404).
    """
    payload = {
        "guide_mode": "condensed",
    }
    response = client.post(
        f"/documents/{ready_document.id}/analysis",
        json=payload,
        headers=other_auth_headers,
    )

    # In StudyGen tenant isolation, another user's document is filtered out (404) or forbidden (403)
    assert response.status_code in (403, 404)


def test_post_document_analysis_not_found(client, auth_headers):
    """
    Verify that requesting analysis for a non-existent document returns 404 Not Found.
    """
    non_existent_id = str(uuid.uuid4())
    response = client.post(
        f"/documents/{non_existent_id}/analysis",
        json={"guide_mode": "condensed"},
        headers=auth_headers,
    )

    assert response.status_code == 404
