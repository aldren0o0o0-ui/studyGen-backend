from app.commands.assign_legacy_data_owner import assign
from app.commands.backfill_vector_ownership import backfill
from app.commands.check_ownership_consistency import audit
from app.database.models.auth import LEGACY_OWNER_ID, User
from app.database.models.document import Document
from app.services.password_service import hash_password
from app.services.token_service import utcnow


def _user(db, user_id="target-user"):
    now = utcnow()
    user = User(
        id=user_id,
        email="owner@example.com",
        normalized_email="owner@example.com",
        password_hash=hash_password("owner-password-123"),
        display_name="Owner",
        status="ACTIVE",
        password_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    return user


def test_legacy_assignment_is_dry_run_idempotent_and_updates_vectors(
    db_session, mock_collection
):
    target = _user(db_session)
    document = Document(
        id="legacy-doc", user_id=LEGACY_OWNER_ID, original_filename="legacy.txt",
        file_type="txt", file_size=10, chunk_count=1, status="READY",
    )
    db_session.add(document)
    db_session.commit()
    mock_collection.add(
        ids=["legacy-doc:0"], documents=["legacy"],
        metadatas=[{"document_id": "legacy-doc", "chunk_index": 0}],
    )

    dry = assign(target.email, apply=False, collection=mock_collection)
    db_session.expire_all()
    assert dry["documents"] == 1
    assert db_session.get(Document, "legacy-doc").user_id == LEGACY_OWNER_ID

    applied = assign(target.email, apply=True, collection=mock_collection)
    db_session.expire_all()
    assert applied["documents"] == 1
    assert db_session.get(Document, "legacy-doc").user_id == target.id
    assert mock_collection.get()["metadatas"][0]["user_id"] == target.id
    assert assign(target.email, apply=True, collection=mock_collection)["documents"] == 0


def test_vector_backfill_reports_orphans_without_guessing(db_session, mock_collection):
    target = _user(db_session)
    db_session.add(Document(
        id="known", user_id=target.id, original_filename="known.txt",
        file_type="txt", file_size=10, chunk_count=1, status="READY",
    ))
    db_session.commit()
    mock_collection.add(
        ids=["known:0", "missing:0"], documents=["known", "orphan"],
        metadatas=[
            {"document_id": "known", "chunk_index": 0},
            {"document_id": "missing", "chunk_index": 0},
        ],
    )
    report = backfill(apply=True, collection=mock_collection)
    assert report["chunks_updated"] == 1
    assert report["orphan_vectors"] == 1
    metas = mock_collection.get()["metadatas"]
    assert metas[0]["user_id"] == target.id
    assert metas[1]["user_id"] == LEGACY_OWNER_ID


def test_ownership_audit_clean_database(db_session, mock_collection):
    assert not any(audit(collection=mock_collection).values())
