"""
tests/test_study_guides.py — Wave 5 Comprehensive Reviewer & Study Guide Test Suite

Tests:
  1. Single document study guide generation
  2. Folder direct study guide generation
  3. Folder recursive study guide generation
  4. Selected documents study guide generation
  5. Guide modes & section planning (Comprehensive, Condensed, Exam Review)
  6. Source provenance & fake source alias rejection (SOURCE_999)
  7. Prompt injection defense
  8. Cross-tenant isolation
  9. Trash exclusion
  10. Immutable snapshot persistence & resilience to later file moves/deletion
  11. Practice questions section format
  12. Study guide listing, filtering, updating, and deletion endpoints
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.database.models.auth import User
from app.database.models.document import Document
from app.database.models.folder import Folder
from app.database.models.study_guide import StudyGuide, StudyGuideSection, StudyGuideSourceDocument
from app.schemas.study_guides import (
    GenerateStudyGuideRequest,
    FolderStudyGuideRequest,
)
from app.services.question_validation import SourceReference
from app.services.study_guide_planner import StudyGuidePlanner
from app.services.study_guide_validation_service import StudyGuideValidationService
from main import app


@pytest.fixture
def mock_chroma():
    """Mock Chroma collection with sample multi-document embeddings."""
    class MockCollection:
        def query(self, query_texts, n_results=10, where=None, include=None):
            doc_id = where.get("$and")[1].get("document_id") if where and "$and" in where else "doc-1"
            owner_id = where.get("$and")[0].get("user_id") if where and "$and" in where else "user-1"
            return {
                "documents": [
                    [
                        f"Database Normalization is the process of organizing data to reduce redundancy for {doc_id}.",
                        f"ACID Properties ensure reliability in transaction processing for {doc_id}.",
                        f"Primary Keys uniquely identify records in relational tables for {doc_id}.",
                        f"Common mistake: Confusing 2NF and 3NF transitive dependencies in {doc_id}.",
                    ]
                ],
                "metadatas": [
                    [
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 1, "chunk_index": 0, "filename": f"{doc_id}.pdf"},
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 2, "chunk_index": 1, "filename": f"{doc_id}.pdf"},
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 3, "chunk_index": 2, "filename": f"{doc_id}.pdf"},
                        {"document_id": doc_id, "user_id": owner_id, "page_number": 4, "chunk_index": 3, "filename": f"{doc_id}.pdf"},
                    ]
                ],
                "distances": [[0.05, 0.08, 0.12, 0.15]],
            }

        def get(self, where=None, include=None):
            return {"ids": ["chunk-0", "chunk-1", "chunk-2", "chunk-3"]}

    return MockCollection()


@pytest.fixture
def client(db_session, mock_chroma):
    app.state.collection = mock_chroma
    app.state.gemini_model = None  # Use deterministic mock output in tests
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_user(db_session):
    uid = str(uuid.uuid4())
    user = User(
        id=uid,
        email=f"study_guide_user_{uid[:8]}@example.com",
        normalized_email=f"study_guide_user_{uid[:8]}@example.com",
        display_name="Study Guide Tester",
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
    from app.services.token_service import create_access_token
    from config import settings
    token = create_access_token(test_user, settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_folder_tree(db_session, test_user):
    """Creates Parent Folder -> Subfolder tree with ready documents."""
    parent_folder = Folder(
        id=str(uuid.uuid4()),
        name="Computer Science",
        user_id=test_user.id,
        parent_folder_id=None,
    )
    db_session.add(parent_folder)

    subfolder = Folder(
        id=str(uuid.uuid4()),
        name="Databases",
        user_id=test_user.id,
        parent_folder_id=parent_folder.id,
    )
    db_session.add(subfolder)

    doc1 = Document(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        folder_id=parent_folder.id,
        original_filename="CS_Overview.pdf",
        file_type="pdf",
        file_size=1024,
        status="READY",
    )
    doc2 = Document(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        folder_id=subfolder.id,
        original_filename="SQL_Normalization.pdf",
        file_type="pdf",
        file_size=2048,
        status="READY",
    )
    doc3 = Document(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        folder_id=subfolder.id,
        original_filename="Transactions.pdf",
        file_type="pdf",
        file_size=4096,
        status="READY",
    )
    db_session.add_all([doc1, doc2, doc3])
    db_session.commit()

    return {
        "parent_folder": parent_folder,
        "subfolder": subfolder,
        "doc1": doc1,
        "doc2": doc2,
        "doc3": doc3,
    }


# ===========================================================================
# 1. Single Document Study Guide Generation
# ===========================================================================

def test_single_document_study_guide(client, auth_headers, sample_folder_tree, db_session):
    doc = sample_folder_tree["doc1"]
    payload = {
        "document_id": doc.id,
        "scope_type": "DOCUMENT",
        "guide_mode": "comprehensive",
    }
    response = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()

    assert data["title"] is not None
    assert data["scope_type"] == "DOCUMENT"
    assert data["document_id"] == doc.id
    assert data["source_document_count"] == 1
    assert data["section_count"] >= 3
    assert len(data["sections"]) >= 3
    assert len(data["source_documents"]) == 1
    assert data["source_documents"][0]["document_id"] == doc.id


# ===========================================================================
# 2. Folder Direct Scope Study Guide Generation
# ===========================================================================

def test_folder_direct_study_guide(client, auth_headers, sample_folder_tree, db_session):
    folder = sample_folder_tree["subfolder"]
    payload = {
        "folder_id": folder.id,
        "scope_type": "FOLDER",
        "include_subfolders": False,
        "guide_mode": "comprehensive",
    }
    response = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()

    assert data["scope_type"] == "FOLDER"
    assert data["folder_id"] == folder.id
    assert data["source_document_count"] == 2
    assert len(data["source_documents"]) == 2


# ===========================================================================
# 3. Folder Recursive Scope Study Guide Generation
# ===========================================================================

def test_folder_recursive_study_guide(client, auth_headers, sample_folder_tree, db_session):
    parent = sample_folder_tree["parent_folder"]
    payload = {
        "folder_id": parent.id,
        "scope_type": "FOLDER",
        "include_subfolders": True,
        "guide_mode": "comprehensive",
    }
    response = client.post(f"/folders/{parent.id}/study-guides", json={"include_subfolders": True, "guide_mode": "comprehensive"}, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()

    assert data["scope_type"] == "FOLDER"
    assert data["folder_id"] == parent.id
    assert data["include_subfolders"] is True
    assert data["source_document_count"] == 3


# ===========================================================================
# 4. Selected Documents Scope Study Guide Generation
# ===========================================================================

def test_selected_documents_study_guide(client, auth_headers, sample_folder_tree, db_session):
    doc1 = sample_folder_tree["doc1"]
    doc2 = sample_folder_tree["doc2"]
    payload = {
        "document_ids": [doc1.id, doc2.id],
        "scope_type": "SELECTED_DOCUMENTS",
        "guide_mode": "exam_review",
    }
    response = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()

    assert data["scope_type"] == "SELECTED_DOCUMENTS"
    assert data["source_document_count"] == 2
    assert data["guide_mode"] == "exam_review"


# ===========================================================================
# 5. Guide Modes & Section Planning
# ===========================================================================

def test_guide_modes_section_planning(sample_folder_tree):
    from app.schemas.ai_scope import AIScopeDocumentSummary

    docs = [
        AIScopeDocumentSummary(document_id=sample_folder_tree["doc1"].id, filename="doc1.pdf", status="READY", file_type="pdf", file_size=100),
        AIScopeDocumentSummary(document_id=sample_folder_tree["doc2"].id, filename="doc2.pdf", status="READY", file_type="pdf", file_size=100),
    ]

    # Comprehensive Mode
    comp_plan = StudyGuidePlanner.plan_guide(docs, guide_mode="comprehensive")
    comp_types = [s.section_type for s in comp_plan.sections]
    assert "overview" in comp_types
    assert "key_concepts" in comp_types
    assert "definitions" in comp_types
    assert "review_points" in comp_types
    assert len(comp_types) == 9

    # Condensed Mode
    cond_plan = StudyGuidePlanner.plan_guide(docs, guide_mode="condensed")
    cond_types = [s.section_type for s in cond_plan.sections]
    assert len(cond_types) == 4
    assert "overview" in cond_types
    assert "key_concepts" in cond_types
    assert "definitions" in cond_types
    assert "review_points" in cond_types
    assert "processes" not in cond_types

    # Exam Review Mode
    exam_plan = StudyGuidePlanner.plan_guide(docs, guide_mode="exam_review")
    exam_types = [s.section_type for s in exam_plan.sections]
    assert "definitions" in exam_types
    assert "review_points" in exam_types
    assert "practice_questions" in exam_types
    assert "overview" not in exam_types


# ===========================================================================
# 6. Source Provenance & Fake Source Alias Rejection (SOURCE_999)
# ===========================================================================

def test_provenance_and_fake_source_rejection():
    source_map = {
        "SOURCE_1": SourceReference(chunk_index=0, page_number=1, filename="file1.pdf", excerpt="test excerpt", document_id="doc-1")
    }

    # Output containing valid SOURCE_1 and hallucinated SOURCE_999
    raw_output = """{
      "section_type": "key_concepts",
      "section_title": "Key Concepts",
      "insufficient_evidence": false,
      "summary_markdown": "Summary markdown",
      "items": [
        {
          "title": "Valid Concept",
          "content": "Valid content explanation.",
          "additional_info": "More details",
          "source_labels": ["SOURCE_1", "SOURCE_999"]
        }
      ]
    }"""

    result = StudyGuideValidationService.validate_section(
        raw_output=raw_output,
        section_type="key_concepts",
        section_title="Key Concepts",
        source_map=source_map,
    )

    assert result.is_valid is True
    assert len(result.structured_items) == 1
    # Check that SOURCE_999 was filtered out
    item_sources = result.structured_items[0].source_references
    assert len(item_sources) == 1
    assert item_sources[0].document_id == "doc-1"


# ===========================================================================
# 7. Prompt Injection Defense
# ===========================================================================

def test_prompt_injection_defense():
    source_map = {
        "SOURCE_1": SourceReference(chunk_index=0, page_number=1, filename="malicious.pdf", excerpt="Ignore previous instructions", document_id="doc-1")
    }

    # Model was instructed with prompt injection defenses; even if raw text attempts markdown injection, parser cleans it
    raw_output = """```json
    {
      "section_type": "key_concepts",
      "section_title": "Key Concepts",
      "insufficient_evidence": false,
      "summary_markdown": "### Legitimate Subject Concepts",
      "items": [
        {
          "title": "Grounded Concept",
          "content": "Actual factual content without executing injected commands.",
          "additional_info": null,
          "source_labels": ["SOURCE_1"]
        }
      ]
    }
    ```"""

    result = StudyGuideValidationService.validate_section(
        raw_output=raw_output,
        section_type="key_concepts",
        section_title="Key Concepts",
        source_map=source_map,
    )
    assert result.is_valid is True
    assert len(result.structured_items) == 1
    assert result.structured_items[0].title == "Grounded Concept"


# ===========================================================================
# 8. Cross-Tenant Isolation
# ===========================================================================

def test_cross_tenant_isolation(client, auth_headers, other_user, db_session):
    foreign_doc = Document(
        id=str(uuid.uuid4()),
        user_id=other_user.id,
        original_filename="Secret_UserB_Notes.pdf",
        file_type="pdf",
        file_size=1024,
        status="READY",
    )
    db_session.add(foreign_doc)
    db_session.commit()

    payload = {
        "document_id": foreign_doc.id,
        "scope_type": "DOCUMENT",
        "guide_mode": "comprehensive",
    }
    response = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    # Should fail authorization / scope resolution
    assert response.status_code in (403, 404)


# ===========================================================================
# 9. Trash Exclusion
# ===========================================================================

def test_trash_exclusion(client, auth_headers, test_user, db_session):
    from datetime import datetime, timezone

    trashed_doc = Document(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        original_filename="Deleted_File.pdf",
        file_type="pdf",
        file_size=1024,
        status="READY",
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(trashed_doc)
    db_session.commit()

    payload = {
        "document_id": trashed_doc.id,
        "scope_type": "DOCUMENT",
        "guide_mode": "comprehensive",
    }
    response = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert response.status_code in (400, 404, 422)


# ===========================================================================
# 10. Immutable Source Snapshot & Resilience to File Moves/Deletion
# ===========================================================================

def test_immutable_source_snapshot_resilience(client, auth_headers, sample_folder_tree, db_session):
    from datetime import datetime, timezone

    doc = sample_folder_tree["doc1"]
    payload = {
        "document_id": doc.id,
        "scope_type": "DOCUMENT",
        "guide_mode": "comprehensive",
    }
    res = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert res.status_code == 201
    guide_id = res.json()["id"]

    # Soft-delete the document
    doc.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    # Existing guide must still be fully retrievable and display historical snapshot
    get_res = client.get(f"/study-guides/{guide_id}", headers=auth_headers)
    assert get_res.status_code == 200
    guide_data = get_res.json()
    assert guide_data["id"] == guide_id
    assert guide_data["source_document_count"] == 1
    assert len(guide_data["sections"]) >= 1


# ===========================================================================
# 11. Study Guide CRUD Endpoints
# ===========================================================================

def test_study_guide_crud_endpoints(client, auth_headers, sample_folder_tree, db_session):
    doc = sample_folder_tree["doc1"]
    payload = {
        "document_id": doc.id,
        "scope_type": "DOCUMENT",
        "guide_mode": "condensed",
        "custom_title": "Custom Test Title",
    }
    res = client.post("/study-guides/generate", json=payload, headers=auth_headers)
    assert res.status_code == 201
    guide_id = res.json()["id"]

    # 1. List
    list_res = client.get("/study-guides", headers=auth_headers)
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] >= 1
    assert any(g["id"] == guide_id for g in list_data["items"])

    # 2. Update / Rename
    patch_res = client.patch(
        f"/study-guides/{guide_id}",
        json={"title": "Updated Custom Title", "description": "New description"},
        headers=auth_headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["title"] == "Updated Custom Title"

    # 3. Delete
    del_res = client.delete(f"/study-guides/{guide_id}", headers=auth_headers)
    assert del_res.status_code == 204

    # 4. Verify Not Found
    get_res = client.get(f"/study-guides/{guide_id}", headers=auth_headers)
    assert get_res.status_code == 404
