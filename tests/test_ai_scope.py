"""Comprehensive tests for Course and Folder Intelligence AI Scope Resolver (Wave 1).

Covers:
- Single Document Scope
- Selected Documents Scope (atomicity, deduplication, foreign reject)
- Direct Folder Scope
- Recursive Folder Scope (nested traversal)
- Multi-Tenant Isolation (User A cannot see User B folders/documents)
- Trash and Readiness Guards
- Max Scope Limit Guards
- Preview Endpoint (side-effect free)
- RAG Leakage Protection
- Snapshot Provenance
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.document import Document
from app.database.models.folder import Folder
from app.database.models.chat import ChatSession, ChatSessionDocument
from app.schemas.ai_scope import AIScopeRequest, AIScopeType
from app.services import ai_scope_service, chat_service
from app.services.chat_retrieval import retrieve_chat_chunks
from main import app


def _create_user(db: Session, email: str, role: str = "USER") -> User:
    user = User(
        id=str(uuid4()),
        email=email,
        normalized_email=email.lower().strip(),
        password_hash="hash",
        display_name="Test User",
        role=role,
        status="ACTIVE",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_folder(
    db: Session,
    user_id: str,
    name: str,
    parent_folder_id: str | None = None,
) -> Folder:
    folder = Folder(
        id=str(uuid4()),
        user_id=user_id,
        name=name,
        parent_folder_id=parent_folder_id,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


def _create_document(
    db: Session,
    user_id: str,
    filename: str,
    folder_id: str | None = None,
    status: str = "READY",
    deleted: bool = False,
) -> Document:
    doc = Document(
        id=str(uuid4()),
        user_id=user_id,
        folder_id=folder_id,
        original_filename=filename,
        file_type="pdf",
        file_size=1024,
        status=status,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# Scope Resolver Unit & Security Tests
# ---------------------------------------------------------------------------

def test_single_document_scope_success(db_session: Session):
    user = _create_user(db_session, "single_doc@example.com")
    doc = _create_document(db_session, user.id, "Lecture1.pdf")

    req = AIScopeRequest(scope_type=AIScopeType.DOCUMENT, document_id=doc.id)
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.scope_type == "document"
    assert resolved.document_count == 1
    assert resolved.document_ids == [doc.id]
    assert resolved.documents[0].filename == "Lecture1.pdf"


def test_single_document_foreign_rejected(db_session: Session):
    user_a = _create_user(db_session, "user_a@example.com")
    user_b = _create_user(db_session, "user_b@example.com")
    doc_b = _create_document(db_session, user_b.id, "UserB_Doc.pdf")

    req = AIScopeRequest(scope_type=AIScopeType.DOCUMENT, document_id=doc_b.id)
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user_a.id, req)
    assert exc_info.value.status_code == 404


def test_single_document_trashed_rejected(db_session: Session):
    user = _create_user(db_session, "trashed_doc@example.com")
    doc = _create_document(db_session, user.id, "Trashed.pdf", deleted=True)

    req = AIScopeRequest(scope_type=AIScopeType.DOCUMENT, document_id=doc.id)
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user.id, req)
    assert exc_info.value.status_code == 422


def test_single_document_unready_rejected(db_session: Session):
    user = _create_user(db_session, "unready_doc@example.com")
    doc = _create_document(db_session, user.id, "Processing.pdf", status="PROCESSING")

    req = AIScopeRequest(scope_type=AIScopeType.DOCUMENT, document_id=doc.id)
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user.id, req)
    assert exc_info.value.status_code == 422


def test_selected_documents_scope_success(db_session: Session):
    user = _create_user(db_session, "selected_docs@example.com")
    d1 = _create_document(db_session, user.id, "Doc1.pdf")
    d2 = _create_document(db_session, user.id, "Doc2.pdf")
    d3 = _create_document(db_session, user.id, "Doc3.pdf")

    req = AIScopeRequest(
        scope_type=AIScopeType.SELECTED_DOCUMENTS,
        document_ids=[d1.id, d2.id, d3.id],
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.scope_type == "selected_documents"
    assert resolved.document_count == 3
    assert resolved.document_ids == [d1.id, d2.id, d3.id]


def test_selected_documents_deduplication(db_session: Session):
    user = _create_user(db_session, "dedup_docs@example.com")
    d1 = _create_document(db_session, user.id, "Doc1.pdf")
    d2 = _create_document(db_session, user.id, "Doc2.pdf")

    # Pass duplicates: [d1, d2, d1, d2]
    req = AIScopeRequest(
        scope_type=AIScopeType.SELECTED_DOCUMENTS,
        document_ids=[d1.id, d2.id, d1.id, d2.id],
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.document_count == 2
    assert resolved.document_ids == [d1.id, d2.id]


def test_selected_documents_atomic_foreign_reject(db_session: Session):
    user_a = _create_user(db_session, "user_a_select@example.com")
    user_b = _create_user(db_session, "user_b_select@example.com")
    d_own = _create_document(db_session, user_a.id, "OwnDoc.pdf")
    d_foreign = _create_document(db_session, user_b.id, "ForeignDoc.pdf")

    req = AIScopeRequest(
        scope_type=AIScopeType.SELECTED_DOCUMENTS,
        document_ids=[d_own.id, d_foreign.id],
    )
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user_a.id, req)
    assert exc_info.value.status_code == 404


def test_selected_documents_atomic_unready_reject(db_session: Session):
    user = _create_user(db_session, "unready_select@example.com")
    d_ready = _create_document(db_session, user.id, "Ready.pdf", status="READY")
    d_unready = _create_document(db_session, user.id, "Unready.pdf", status="PROCESSING")

    req = AIScopeRequest(
        scope_type=AIScopeType.SELECTED_DOCUMENTS,
        document_ids=[d_ready.id, d_unready.id],
    )
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user.id, req)
    assert exc_info.value.status_code == 422


def test_selected_documents_max_limit_enforced(db_session: Session):
    user = _create_user(db_session, "max_limit@example.com")
    # Generate 55 dummy doc IDs
    fake_ids = [str(uuid4()) for _ in range(55)]

    req = AIScopeRequest(
        scope_type=AIScopeType.SELECTED_DOCUMENTS,
        document_ids=fake_ids,
    )
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user.id, req)
    assert exc_info.value.status_code == 422
    assert "exceeding the maximum allowed limit" in exc_info.value.detail


def test_folder_direct_scope_excludes_subfolders(db_session: Session):
    user = _create_user(db_session, "folder_direct@example.com")
    f_parent = _create_folder(db_session, user.id, "ParentFolder")
    f_child = _create_folder(db_session, user.id, "ChildFolder", parent_folder_id=f_parent.id)

    doc_parent = _create_document(db_session, user.id, "ParentDoc.pdf", folder_id=f_parent.id)
    doc_child = _create_document(db_session, user.id, "ChildDoc.pdf", folder_id=f_child.id)

    req = AIScopeRequest(
        scope_type=AIScopeType.FOLDER,
        folder_id=f_parent.id,
        include_subfolders=False,
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.scope_type == "folder"
    assert resolved.include_subfolders is False
    assert resolved.document_count == 1
    assert resolved.folder_count == 1
    assert doc_parent.id in resolved.document_ids
    assert doc_child.id not in resolved.document_ids


def test_folder_recursive_scope_includes_descendants(db_session: Session):
    user = _create_user(db_session, "folder_recursive@example.com")
    root = _create_folder(db_session, user.id, "Database Systems")
    sql_sub = _create_folder(db_session, user.id, "SQL", parent_folder_id=root.id)
    adv_sub = _create_folder(db_session, user.id, "Advanced SQL", parent_folder_id=sql_sub.id)

    d_root = _create_document(db_session, user.id, "Intro.pdf", folder_id=root.id)
    d_sql = _create_document(db_session, user.id, "Queries.pdf", folder_id=sql_sub.id)
    d_adv = _create_document(db_session, user.id, "Indexing.pdf", folder_id=adv_sub.id)

    req = AIScopeRequest(
        scope_type=AIScopeType.FOLDER,
        folder_id=root.id,
        include_subfolders=True,
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.scope_type == "folder"
    assert resolved.include_subfolders is True
    assert resolved.document_count == 3
    assert resolved.folder_count == 3
    assert set(resolved.document_ids) == {d_root.id, d_sql.id, d_adv.id}


def test_recursive_scope_tenant_isolation(db_session: Session):
    """User A's recursive folder must never include User B's folder or documents."""
    user_a = _create_user(db_session, "user_a_iso@example.com")
    user_b = _create_user(db_session, "user_b_iso@example.com")

    f_a = _create_folder(db_session, user_a.id, "Folder A")
    d_a = _create_document(db_session, user_a.id, "DocA.pdf", folder_id=f_a.id)

    f_b = _create_folder(db_session, user_b.id, "Folder B")
    d_b = _create_document(db_session, user_b.id, "DocB.pdf", folder_id=f_b.id)

    req = AIScopeRequest(
        scope_type=AIScopeType.FOLDER,
        folder_id=f_a.id,
        include_subfolders=True,
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user_a.id, req)

    assert resolved.document_count == 1
    assert resolved.document_ids == [d_a.id]
    assert d_b.id not in resolved.document_ids


def test_folder_scope_trashed_document_exclusion(db_session: Session):
    user = _create_user(db_session, "trash_excl@example.com")
    folder = _create_folder(db_session, user.id, "Algorithms")
    d_active = _create_document(db_session, user.id, "Sorting.pdf", folder_id=folder.id)
    d_trashed = _create_document(db_session, user.id, "Graphs.pdf", folder_id=folder.id, deleted=True)

    req = AIScopeRequest(
        scope_type=AIScopeType.FOLDER,
        folder_id=folder.id,
        include_subfolders=False,
    )
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    assert resolved.document_count == 1
    assert resolved.document_ids == [d_active.id]
    assert d_trashed.id not in resolved.document_ids


def test_folder_empty_scope_rejected_cleanly(db_session: Session):
    user = _create_user(db_session, "empty_f@example.com")
    folder = _create_folder(db_session, user.id, "EmptyFolder")

    req = AIScopeRequest(
        scope_type=AIScopeType.FOLDER,
        folder_id=folder.id,
        include_subfolders=False,
    )
    with pytest.raises(ai_scope_service.AIScopeError) as exc_info:
        ai_scope_service.resolve_ai_scope(db_session, user.id, req)
    assert exc_info.value.status_code == 400
    assert "contains no ready documents" in exc_info.value.detail


def test_scope_preview_endpoint_has_no_side_effects(client: TestClient, db_session: Session):
    user = _create_user(db_session, "preview_user@example.com")
    folder = _create_folder(db_session, user.id, "Physics")
    _create_document(db_session, user.id, "Mechanics.pdf", folder_id=folder.id)
    _create_document(db_session, user.id, "Thermodynamics.pdf", folder_id=folder.id)

    from app.services.token_service import create_access_token
    from config import settings
    token = create_access_token(user, settings)

    response = client.post(
        "/ai/scope/preview",
        json={
            "scope_type": "folder",
            "folder_id": folder.id,
            "include_subfolders": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["scope_type"] == "folder"
    assert data["folder_id"] == folder.id
    assert data["folder_name"] == "Physics"
    assert data["document_count"] == 2
    assert len(data["documents"]) == 2

    # Verify no chat sessions were created in the database
    sessions = db_session.query(ChatSession).filter(ChatSession.user_id == user.id).all()
    assert len(sessions) == 0


def test_rag_retrieval_strict_scope_confinement():
    """Mock Chroma collection to verify retrieve_chat_chunks strictly confines results to resolved document IDs."""
    class MockCollection:
        def query(self, query_texts, n_results, where, include=None):
            # Extract queried document_id from where clause
            # where is {"$and": [{"user_id": ...}, {"document_id": ...}]}
            doc_cond = where["$and"][1]["document_id"]["$eq"]
            user_cond = where["$and"][0]["user_id"]["$eq"]

            if doc_cond == "doc_alpha":
                return {
                    "documents": [["Content with ALPHA_SCOPE_ONLY"]],
                    "metadatas": [[{"document_id": "doc_alpha", "user_id": user_cond, "filename": "Alpha.pdf", "chunk_index": 0}]],
                    "distances": [[0.1]],
                }
            elif doc_cond == "doc_beta":
                return {
                    "documents": [["Content with BETA_SCOPE_ONLY"]],
                    "metadatas": [[{"document_id": "doc_beta", "user_id": user_cond, "filename": "Beta.pdf", "chunk_index": 0}]],
                    "distances": [[0.2]],
                }
            else:
                return {
                    "documents": [["Content with GAMMA_OUTSIDE_SCOPE"]],
                    "metadatas": [[{"document_id": "doc_gamma", "user_id": user_cond, "filename": "Gamma.pdf", "chunk_index": 0}]],
                    "distances": [[0.05]],
                }

    mock_col = MockCollection()

    # If AI scope is resolved only to [doc_alpha, doc_beta]
    chunks = retrieve_chat_chunks(
        mock_col,
        query="GAMMA_OUTSIDE_SCOPE",
        document_ids=["doc_alpha", "doc_beta"],
        top_k=5,
        relevance_threshold=0.0,
        max_chunks_per_document=2,
        max_context_characters=2000,
        user_id="test_user",
    )

    # Gamma is outside scope, so it must never be retrieved even if query matches Gamma's secret token
    retrieved_docs = {chunk.document_id for chunk in chunks}
    assert "doc_gamma" not in retrieved_docs
    assert "doc_alpha" in retrieved_docs or "doc_beta" in retrieved_docs


def test_chat_session_snapshot_provenance(db_session: Session):
    """Moving a document to another folder after starting a chat preserves the original session snapshot."""
    user = _create_user(db_session, "provenance_user@example.com")
    f1 = _create_folder(db_session, user.id, "Folder 1")
    f2 = _create_folder(db_session, user.id, "Folder 2")

    doc = _create_document(db_session, user.id, "StableDoc.pdf", folder_id=f1.id)

    # Start folder chat session
    req = AIScopeRequest(scope_type=AIScopeType.FOLDER, folder_id=f1.id, include_subfolders=False)
    resolved = ai_scope_service.resolve_ai_scope(db_session, user.id, req)

    session_detail = chat_service.create_session(
        db_session,
        document_ids=resolved.document_ids,
        title="Folder 1 Chat",
        system_mode="document_qa",
        max_documents=50,
        scope_type="folder",
        folder_id=f1.id,
        include_subfolders=False,
    )

    assert session_detail.scope_type == "folder"
    assert session_detail.folder_id == f1.id
    assert len(session_detail.documents) == 1

    # Now move the document to Folder 2
    doc.folder_id = f2.id
    db_session.commit()

    # Reopen/load existing chat session
    session = chat_service.get_session(db_session, session_detail.session_id)
    serialized = chat_service.serialize_session(db_session, session, detail=True)

    # Historical snapshot remains intact with StableDoc.pdf linked
    assert serialized.session_id == session_detail.session_id
    assert len(serialized.documents) == 1
    assert serialized.documents[0].document_id == doc.id
