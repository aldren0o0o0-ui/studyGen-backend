"""Phase 7 authentication, token lifecycle, and cross-user isolation tests."""
from __future__ import annotations

from datetime import timedelta
import pytest
from config import settings

from app.database.models.auth import RefreshToken, User
from app.database.session import SessionLocal
from app.services.token_service import utcnow


def register(client, email: str):
    response = client.post("/auth/register", json={
        "email": email,
        "display_name": email.split("@")[0].title(),
        "password": "correct-horse-battery",
    })
    assert response.status_code == 201, response.text
    return response.json()


def bearer(result):
    return {"Authorization": f"Bearer {result['access_token']}"}


def test_protected_routes_reject_missing_and_invalid_tokens(client):
    assert client.get("/documents", headers={"Authorization": ""}).status_code == 401
    assert client.get(
        "/documents", headers={"Authorization": "Bearer invalid"}
    ).status_code == 401


def test_register_login_profile_and_logout(client):
    auth = register(client, "Alice@Example.com")
    if settings.enable_community_features:
        created_post = client.post("/posts", headers=bearer(auth), json={
            "caption": "First post", "visibility": "PUBLIC",
            "recipient_user_ids": [], "allow_comments": True,
        })
        assert created_post.status_code == 201, created_post.text
    headers = bearer(auth)
    assert client.get("/auth/me", headers=headers).json()["email"] == "Alice@Example.com"
    updated = client.patch(
        "/auth/me", headers=headers, json={"display_name": "Alice Updated"}
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Alice Updated"
    login = client.post("/auth/login", json={
        "email": "alice@example.com", "password": "correct-horse-battery",
    })
    assert login.status_code == 200
    logout = client.post(
        "/auth/logout", headers=bearer(login.json()),
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert logout.status_code == 200
    # Logging out revokes only that refresh token; it must never alter the
    # password hash or prevent a fresh credential-based login.
    second_login = client.post("/auth/login", json={
        "email": " ALICE@example.com ", "password": "correct-horse-battery",
    })
    assert second_login.status_code == 200, second_login.text


@pytest.mark.skipif(not settings.enable_community_features, reason="Community features disabled")
def test_legacy_account_without_community_username_can_create_post(client):
    created = client.post("/posts", json={
        "caption": "Legacy account first post", "visibility": "PUBLIC",
        "recipient_user_ids": [], "allow_comments": True,
    })
    assert created.status_code == 201, created.text
    profile = client.get("/community/me")
    assert profile.status_code == 200
    assert profile.json()["username"].startswith("user_")


def test_refresh_rotation_and_reuse_revokes_family(client):
    auth = register(client, "rotate@example.com")
    first = client.post("/auth/refresh", json={"refresh_token": auth["refresh_token"]})
    assert first.status_code == 200
    reused = client.post("/auth/refresh", json={"refresh_token": auth["refresh_token"]})
    assert reused.status_code == 401
    assert client.post(
        "/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
    ).status_code == 401


def test_expired_access_token_is_rejected(client):
    auth = register(client, "expired@example.com")
    db = SessionLocal()
    try:
        user = db.get(User, auth["user"]["id"])
        user.password_changed_at = utcnow() + timedelta(seconds=2)
        db.commit()
    finally:
        db.close()
    assert client.get("/auth/me", headers=bearer(auth)).status_code == 401


def test_document_and_chat_isolation(client):
    alice = register(client, "alice-isolation@example.com")
    bob = register(client, "bob-isolation@example.com")
    upload = client.post(
        "/documents/upload",
        headers=bearer(alice),
        files={"file": ("private.txt", b"Alice private evidence.", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]

    listing = client.get("/documents", headers=bearer(bob))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    assert client.get(f"/documents/{document_id}", headers=bearer(bob)).status_code == 404
    assert client.delete(f"/documents/{document_id}", headers=bearer(bob)).status_code == 404
    cross_chat = client.post(
        "/chat-sessions", headers=bearer(bob),
        json={"document_ids": [document_id], "system_mode": "document_qa"},
    )
    assert cross_chat.status_code == 404


def test_change_password_revokes_all_refresh_tokens(client):
    auth = register(client, "password@example.com")
    second = client.post("/auth/login", json={
        "email": "password@example.com", "password": "correct-horse-battery",
    }).json()
    response = client.post(
        "/auth/change-password", headers=bearer(auth),
        json={
            "current_password": "correct-horse-battery",
            "new_password": "new-correct-horse-battery",
        },
    )
    assert response.status_code == 200
    assert client.post(
        "/auth/refresh", json={"refresh_token": second["refresh_token"]}
    ).status_code == 401
    assert client.post("/auth/login", json={
        "email": "password@example.com", "password": "correct-horse-battery",
    }).status_code == 401
    assert client.post("/auth/login", json={
        "email": "password@example.com", "password": "new-correct-horse-battery",
    }).status_code == 200


def test_refresh_tokens_are_hashed_at_rest(client):
    auth = register(client, "hash@example.com")
    db = SessionLocal()
    try:
        row = db.query(RefreshToken).filter_by(user_id=auth["user"]["id"]).one()
        assert row.token_hash != auth["refresh_token"]
        assert len(row.token_hash) == 64
    finally:
        db.close()
