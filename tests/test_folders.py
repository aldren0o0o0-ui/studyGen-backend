"""
Tests for Folder Foundation, Document Move, Safe Deletion, Favorites, Bulk Actions, and Nested Folders.
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.database.models.auth import User
from app.database.models.document import Document
from app.database.models.folder import Folder
from app.services.auth_service import hash_password
from app.services.token_service import create_access_token
from config import settings


@pytest.fixture
def user_a(db_session):
    u = User(
        id=str(uuid.uuid4()),
        email="student_a@example.com",
        normalized_email="student_a@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Student A",
        status="ACTIVE",
        role="USER",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def user_b(db_session):
    u = User(
        id=str(uuid.uuid4()),
        email="student_b@example.com",
        normalized_email="student_b@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Student B",
        status="ACTIVE",
        role="USER",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def auth_a(user_a):
    token = create_access_token(user_a, settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_b(user_b):
    token = create_access_token(user_b, settings)
    return {"Authorization": f"Bearer {token}"}


def _create_doc(db_session, user_id: str, filename: str, folder_id: str = None) -> Document:
    doc = Document(
        id=str(uuid.uuid4()),
        user_id=user_id,
        folder_id=folder_id,
        original_filename=filename,
        file_type="pdf",
        file_size=1024,
        chunk_count=5,
        status="READY",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# Phase 1: Folder Foundation Tests
# ---------------------------------------------------------------------------

def test_create_and_list_folders(client: TestClient, auth_a, auth_b, user_a, user_b):
    # 1. User A creates a folder
    res_a = client.post("/folders", json={"name": "Database Systems"}, headers=auth_a)
    assert res_a.status_code == 201
    folder_a_id = res_a.json()["id"]
    assert res_a.json()["name"] == "Database Systems"
    assert res_a.json()["parent_folder_id"] is None

    # 2. User B creates a folder
    res_b = client.post("/folders", json={"name": "Networking"}, headers=auth_b)
    assert res_b.status_code == 201
    folder_b_id = res_b.json()["id"]

    # 3. Isolation: User A only sees their folder
    list_a = client.get("/folders", headers=auth_a)
    assert list_a.status_code == 200
    items_a = list_a.json()["items"]
    assert len(items_a) == 1
    assert items_a[0]["id"] == folder_a_id

    # 4. User B only sees their folder
    list_b = client.get("/folders", headers=auth_b)
    assert list_b.status_code == 200
    items_b = list_b.json()["items"]
    assert len(items_b) == 1
    assert items_b[0]["id"] == folder_b_id


def test_create_folder_invalid_name(client: TestClient, auth_a):
    # Blank name
    res = client.post("/folders", json={"name": "   "}, headers=auth_a)
    assert res.status_code in (400, 422)

    # Control characters
    res = client.post("/folders", json={"name": "Folder\x00Name"}, headers=auth_a)
    assert res.status_code in (400, 422)


def test_get_folder_detail_and_isolation(client: TestClient, auth_a, auth_b):
    res_create = client.post("/folders", json={"name": "Operating Systems"}, headers=auth_a)
    folder_id = res_create.json()["id"]

    # User A gets folder detail
    res = client.get(f"/folders/{folder_id}", headers=auth_a)
    assert res.status_code == 200
    assert res.json()["id"] == folder_id
    assert res.json()["name"] == "Operating Systems"
    assert len(res.json()["breadcrumbs"]) == 1
    assert res.json()["breadcrumbs"][0]["name"] == "Operating Systems"

    # User B cannot access User A's folder (404)
    res_b = client.get(f"/folders/{folder_id}", headers=auth_b)
    assert res_b.status_code == 404


def test_rename_folder(client: TestClient, auth_a, auth_b):
    res_create = client.post("/folders", json={"name": "Old Name"}, headers=auth_a)
    folder_id = res_create.json()["id"]

    # User A renames folder
    res_update = client.patch(f"/folders/{folder_id}", json={"name": "New Name"}, headers=auth_a)
    assert res_update.status_code == 200
    assert res_update.json()["name"] == "New Name"

    # User B cannot rename User A's folder
    res_b = client.patch(f"/folders/{folder_id}", json={"name": "Hacked"}, headers=auth_b)
    assert res_b.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Phase 1: Document Move & Ownership Protection Tests
# ---------------------------------------------------------------------------

def test_move_document_to_folder_and_root(client: TestClient, auth_a, user_a, db_session):
    folder_res = client.post("/folders", json={"name": "Lectures"}, headers=auth_a)
    folder_id = folder_res.json()["id"]

    doc = _create_doc(db_session, user_a.id, "Lecture1.pdf")
    assert doc.folder_id is None

    # Move doc into folder
    move_res = client.patch(f"/documents/{doc.id}/move", json={"folder_id": folder_id}, headers=auth_a)
    assert move_res.status_code == 200
    assert move_res.json()["folder_id"] == folder_id

    # Filter documents by folder_id
    list_res = client.get(f"/documents?folder_id={folder_id}", headers=auth_a)
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1
    assert list_res.json()["items"][0]["document_id"] == doc.id

    # Root-only filter excludes folder documents
    root_res = client.get("/documents?root_only=true", headers=auth_a)
    assert root_res.status_code == 200
    assert root_res.json()["total"] == 0

    # Move back to root
    move_root = client.patch(f"/documents/{doc.id}/move", json={"folder_id": None}, headers=auth_a)
    assert move_root.status_code == 200
    assert move_root.json()["folder_id"] is None

    # Root-only filter now includes it
    root_res2 = client.get("/documents?root_only=true", headers=auth_a)
    assert root_res2.status_code == 200
    assert root_res2.json()["total"] == 1


def test_cross_tenant_move_attack_protection(client: TestClient, auth_a, auth_b, user_a, user_b, db_session):
    # User A creates Folder A
    f_res = client.post("/folders", json={"name": "Folder A"}, headers=auth_a)
    folder_a_id = f_res.json()["id"]

    # User B creates Document B
    doc_b = _create_doc(db_session, user_b.id, "Secret_B.pdf")

    # Attack 1: User B tries to move Document B into User A's Folder A
    attack_1 = client.patch(f"/documents/{doc_b.id}/move", json={"folder_id": folder_a_id}, headers=auth_b)
    assert attack_1.status_code in (400, 404)

    # Verify Document B is still at root
    db_session.refresh(doc_b)
    assert doc_b.folder_id is None

    # Attack 2: User A tries to move User B's Document B into Folder A
    attack_2 = client.patch(f"/documents/{doc_b.id}/move", json={"folder_id": folder_a_id}, headers=auth_a)
    assert attack_2.status_code in (400, 404)

    db_session.refresh(doc_b)
    assert doc_b.folder_id is None


def test_safe_folder_deletion(client: TestClient, auth_a, user_a, db_session):
    # Create folder and put 2 files in it
    f_res = client.post("/folders", json={"name": "Temp Project"}, headers=auth_a)
    folder_id = f_res.json()["id"]

    doc1 = _create_doc(db_session, user_a.id, "Plan.pdf", folder_id=folder_id)
    doc2 = _create_doc(db_session, user_a.id, "Specs.pdf", folder_id=folder_id)

    # Delete folder
    del_res = client.delete(f"/folders/{folder_id}", headers=auth_a)
    assert del_res.status_code == 200
    assert del_res.json()["documents_relocated"] == 2

    # Files must NOT be deleted — they should now be at root
    db_session.refresh(doc1)
    db_session.refresh(doc2)
    assert doc1.folder_id is None
    assert doc2.folder_id is None
    assert doc1.deleted_at is None
    assert doc2.deleted_at is None


# ---------------------------------------------------------------------------
# Phase 3: Favorites, Recents, and Bulk Operations Tests
# ---------------------------------------------------------------------------

def test_favorites_and_recents(client: TestClient, auth_a, user_a, db_session):
    doc = _create_doc(db_session, user_a.id, "Starred.pdf")
    assert not doc.is_favorite

    # Toggle favorite
    fav_res = client.post(f"/documents/{doc.id}/favorite", headers=auth_a)
    assert fav_res.status_code == 200
    assert fav_res.json()["is_favorite"] is True

    # List favorites filter
    fav_list = client.get("/documents?favorite=true", headers=auth_a)
    assert fav_list.status_code == 200
    assert fav_list.json()["total"] == 1

    # Access document updates last_accessed_at
    get_res = client.get(f"/documents/{doc.id}", headers=auth_a)
    assert get_res.status_code == 200
    assert get_res.json()["last_accessed_at"] is not None


def test_bulk_move(client: TestClient, auth_a, user_a, db_session):
    f_res = client.post("/folders", json={"name": "Archive"}, headers=auth_a)
    folder_id = f_res.json()["id"]

    doc1 = _create_doc(db_session, user_a.id, "File1.pdf")
    doc2 = _create_doc(db_session, user_a.id, "File2.pdf")

    # Bulk move both files into Archive
    bulk_res = client.post(
        "/documents/bulk/move",
        json={"document_ids": [doc1.id, doc2.id], "folder_id": folder_id},
        headers=auth_a,
    )
    assert bulk_res.status_code == 200
    assert bulk_res.json()["affected_count"] == 2

    db_session.refresh(doc1)
    db_session.refresh(doc2)
    assert doc1.folder_id == folder_id
    assert doc2.folder_id == folder_id


def test_bulk_move_atomic_rollback_on_cross_tenant(client: TestClient, auth_a, user_a, user_b, db_session):
    f_res = client.post("/folders", json={"name": "My Target"}, headers=auth_a)
    folder_id = f_res.json()["id"]

    doc_a = _create_doc(db_session, user_a.id, "DocA.pdf")
    doc_b = _create_doc(db_session, user_b.id, "DocB.pdf")

    # Bulk move containing DocA (owned) and DocB (foreign) -> must fail atomically
    bulk_res = client.post(
        "/documents/bulk/move",
        json={"document_ids": [doc_a.id, doc_b.id], "folder_id": folder_id},
        headers=auth_a,
    )
    assert bulk_res.status_code == 400

    # Verify DocA was rolled back and NOT moved
    db_session.refresh(doc_a)
    assert doc_a.folder_id is None


# ---------------------------------------------------------------------------
# Phase 4: Trash Lifecycle Tests
# ---------------------------------------------------------------------------

def test_trash_restore_lifecycle(client: TestClient, auth_a, user_a, db_session):
    doc = _create_doc(db_session, user_a.id, "TrashMe.pdf")

    # Soft delete
    del_res = client.delete(f"/documents/{doc.id}", headers=auth_a)
    assert del_res.status_code == 200

    # Active listing excludes trashed file
    active_list = client.get("/documents", headers=auth_a)
    assert active_list.status_code == 200
    assert not any(d["document_id"] == doc.id for d in active_list.json()["items"])

    # Trash listing includes it
    trash_list = client.get("/documents?trash=true", headers=auth_a)
    assert trash_list.status_code == 200
    assert any(d["document_id"] == doc.id for d in trash_list.json()["items"])

    # Restore from trash
    res_restore = client.post(f"/documents/{doc.id}/restore", headers=auth_a)
    assert res_restore.status_code == 200

    # Active listing now includes it again
    active_list2 = client.get("/documents", headers=auth_a)
    assert active_list2.status_code == 200
    assert any(d["document_id"] == doc.id for d in active_list2.json()["items"])


# ---------------------------------------------------------------------------
# Phase 5: Nested Folders & Cycle Prevention Tests
# ---------------------------------------------------------------------------

def test_nested_folders_and_cycle_prevention(client: TestClient, auth_a):
    # Create Parent Folder A
    res_a = client.post("/folders", json={"name": "Folder A"}, headers=auth_a)
    folder_a_id = res_a.json()["id"]

    # Create Child Folder B inside Folder A
    res_b = client.post("/folders", json={"name": "Folder B", "parent_folder_id": folder_a_id}, headers=auth_a)
    assert res_b.status_code == 201
    folder_b_id = res_b.json()["id"]
    assert res_b.json()["parent_folder_id"] == folder_a_id

    # Create Grandchild Folder C inside Folder B
    res_c = client.post("/folders", json={"name": "Folder C", "parent_folder_id": folder_b_id}, headers=auth_a)
    assert res_c.status_code == 201
    folder_c_id = res_c.json()["id"]

    # Verify breadcrumbs of Grandchild Folder C
    detail_c = client.get(f"/folders/{folder_c_id}", headers=auth_a)
    assert detail_c.status_code == 200
    crumbs = detail_c.json()["breadcrumbs"]
    assert len(crumbs) == 3
    assert crumbs[0]["id"] == folder_a_id
    assert crumbs[1]["id"] == folder_b_id
    assert crumbs[2]["id"] == folder_c_id

    # Cycle Attack 1: Try to move Folder A into itself
    cycle_1 = client.patch(f"/folders/{folder_a_id}", json={"parent_folder_id": folder_a_id}, headers=auth_a)
    assert cycle_1.status_code == 400

    # Cycle Attack 2: Try to move Parent Folder A into Grandchild Folder C
    cycle_2 = client.patch(f"/folders/{folder_a_id}", json={"parent_folder_id": folder_c_id}, headers=auth_a)
    assert cycle_2.status_code == 400


# ---------------------------------------------------------------------------
# Phase 6: Student Intelligence / Folder AI Tests
# ---------------------------------------------------------------------------

def test_folder_ai_chat_session_creation(client: TestClient, auth_a, user_a, db_session):
    f_res = client.post("/folders", json={"name": "AI Study Pack"}, headers=auth_a)
    folder_id = f_res.json()["id"]

    # 1. Empty folder should reject AI chat
    res_empty = client.post(f"/folders/{folder_id}/chat", headers=auth_a)
    assert res_empty.status_code == 400

    # 2. Add 2 ready documents to the folder
    _create_doc(db_session, user_a.id, "Topic1.pdf", folder_id=folder_id)
    _create_doc(db_session, user_a.id, "Topic2.pdf", folder_id=folder_id)

    # 3. Create folder AI chat session
    res_chat = client.post(f"/folders/{folder_id}/chat", headers=auth_a)
    assert res_chat.status_code == 200
    data = res_chat.json()
    assert data["folder_id"] == folder_id
    assert data["document_count"] == 2
    assert "session_id" in data


# ---------------------------------------------------------------------------
# Cross-Tenant Security & Edge-Case Regression Tests
# ---------------------------------------------------------------------------

def test_cross_tenant_folder_deletion_attack(client: TestClient, auth_a, auth_b):
    # User B creates Folder B
    f_res = client.post("/folders", json={"name": "User B Folder"}, headers=auth_b)
    folder_b_id = f_res.json()["id"]

    # User A attempts to delete User B's folder
    del_res = client.delete(f"/folders/{folder_b_id}", headers=auth_a)
    assert del_res.status_code in (400, 404)

    # Verify folder B still exists for User B
    get_res = client.get(f"/folders/{folder_b_id}", headers=auth_b)
    assert get_res.status_code == 200


def test_cross_tenant_trash_restore_and_permanent_delete(client: TestClient, auth_a, auth_b, user_b, db_session):
    # User B has a trashed document
    doc_b = _create_doc(db_session, user_b.id, "UserB_Doc.pdf")
    client.delete(f"/documents/{doc_b.id}", headers=auth_b)

    # User A tries to restore User B's trashed document -> 404
    restore_attack = client.post(f"/documents/{doc_b.id}/restore", headers=auth_a)
    assert restore_attack.status_code in (400, 404)

    # User A tries to permanently delete User B's document -> 404
    perm_attack = client.delete(f"/documents/{doc_b.id}/permanent", headers=auth_a)
    assert perm_attack.status_code in (400, 404)

    # Verify document still exists in User B's trash
    trash_list = client.get("/documents?trash=true", headers=auth_b)
    assert trash_list.status_code == 200
    assert any(d["document_id"] == doc_b.id for d in trash_list.json()["items"])


def test_cross_tenant_folder_ai_attack(client: TestClient, auth_a, auth_b, user_b, db_session):
    f_res = client.post("/folders", json={"name": "User B Study Pack"}, headers=auth_b)
    folder_b_id = f_res.json()["id"]
    _create_doc(db_session, user_b.id, "UserB_Secret.pdf", folder_id=folder_b_id)

    # User A tries to start folder chat on User B's folder -> 404
    attack = client.post(f"/folders/{folder_b_id}/chat", headers=auth_a)
    assert attack.status_code in (400, 404)


def test_folder_delete_nested_relocation(client: TestClient, auth_a, user_a, db_session):
    """
    Structure:
    BSIT (Parent)
    └── Database (Child to be deleted)
        ├── Notes.pdf (doc in Database)
        └── Finals (Grandchild)
            └── Reviewer.pdf (doc in Finals)

    When Database is deleted:
    - Notes.pdf moves to root (folder_id = None)
    - Finals moves up to BSIT (parent_folder_id = BSIT.id)
    - Reviewer.pdf remains in Finals
    """
    # 1. Create BSIT
    r_bsit = client.post("/folders", json={"name": "BSIT"}, headers=auth_a)
    bsit_id = r_bsit.json()["id"]

    # 2. Create Database under BSIT
    r_db = client.post("/folders", json={"name": "Database", "parent_folder_id": bsit_id}, headers=auth_a)
    db_id = r_db.json()["id"]

    # 3. Create Finals under Database
    r_finals = client.post("/folders", json={"name": "Finals", "parent_folder_id": db_id}, headers=auth_a)
    finals_id = r_finals.json()["id"]

    # 4. Create documents
    notes_doc = _create_doc(db_session, user_a.id, "Notes.pdf", folder_id=db_id)
    rev_doc = _create_doc(db_session, user_a.id, "Reviewer.pdf", folder_id=finals_id)

    # 5. Delete Database folder
    del_res = client.delete(f"/folders/{db_id}", headers=auth_a)
    assert del_res.status_code == 200
    assert del_res.json()["documents_relocated"] == 1

    # 6. Verify Notes.pdf relocated to root
    db_session.refresh(notes_doc)
    assert notes_doc.folder_id is None
    assert notes_doc.deleted_at is None

    # 7. Verify Finals reparented to BSIT
    finals_detail = client.get(f"/folders/{finals_id}", headers=auth_a)
    assert finals_detail.status_code == 200
    assert finals_detail.json()["parent_folder_id"] == bsit_id

    # 8. Verify Reviewer.pdf still safely inside Finals
    db_session.refresh(rev_doc)
    assert rev_doc.folder_id == finals_id
    assert rev_doc.deleted_at is None


def test_restore_when_original_folder_deleted(client: TestClient, auth_a, user_a, db_session):
    # 1. Create folder and document inside it
    f_res = client.post("/folders", json={"name": "Temporary Folder"}, headers=auth_a)
    folder_id = f_res.json()["id"]
    doc = _create_doc(db_session, user_a.id, "Important.pdf", folder_id=folder_id)

    # 2. Trash document
    client.delete(f"/documents/{doc.id}", headers=auth_a)

    # 3. Delete original folder
    client.delete(f"/folders/{folder_id}", headers=auth_a)

    # 4. Restore document
    res_restore = client.post(f"/documents/{doc.id}/restore", headers=auth_a)
    assert res_restore.status_code == 200
    assert res_restore.json()["folder_id"] is None  # Safely fell back to root!

    db_session.refresh(doc)
    assert doc.folder_id is None
    assert doc.deleted_at is None


def test_folder_naming_edge_cases(client: TestClient, auth_a):
    valid_names = [
        "IT 401 - Finals",
        "Reviewer’s Notes",
        "2026–2027",
        "A",
        "Math & Science: Physics 101 [Section A]",
        "CS_101_Algorithms_&_Data_Structures_Comprehensive_Review",
    ]
    for name in valid_names:
        res = client.post("/folders", json={"name": name}, headers=auth_a)
        assert res.status_code == 201, f"Failed for valid name: {name}"
        assert res.json()["name"] == name


