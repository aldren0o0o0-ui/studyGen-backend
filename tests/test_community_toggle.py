"""Tests for the enable_community_features configuration toggle."""
import pytest
from fastapi.testclient import TestClient

from app.database.models.auth import User
from app.services.auth_service import create_access_token, hash_password
from config import settings
from main import app


@pytest.fixture
def test_user(db_session):
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="test_toggle@example.com",
        normalized_email="test_toggle@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Toggle Tester",
        status="ACTIVE",
        role="ADMIN",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user):
    token = create_access_token(test_user, settings)
    return {"Authorization": f"Bearer {token}"}


def test_community_endpoints_disabled_by_default(client: TestClient, auth_headers):
    """When enable_community_features is False, community & moderation endpoints must return 404."""
    # Community profile endpoint
    resp = client.get("/community/me", headers=auth_headers)
    assert resp.status_code == 404

    # Posts endpoint
    resp = client.get("/posts/feed", headers=auth_headers)
    assert resp.status_code == 404

    # Moderation endpoint
    resp = client.get("/moderation/reports", headers=auth_headers)
    assert resp.status_code == 404


def test_core_endpoints_remain_functional_when_community_disabled(client: TestClient, auth_headers):
    """Core learning endpoints (documents, quizzes, study sets, chat, auth) remain functional."""
    # Health check
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"

    # Documents listing
    resp = client.get("/documents", headers=auth_headers)
    assert resp.status_code == 200

    # Quizzes listing
    resp = client.get("/quizzes", headers=auth_headers)
    assert resp.status_code == 200

    # Study sets
    resp = client.get("/study-sets", headers=auth_headers)
    assert resp.status_code == 200

    # Chat sessions
    resp = client.get("/chat-sessions", headers=auth_headers)
    assert resp.status_code == 200


def test_admin_dashboard_with_community_disabled(client: TestClient, auth_headers):
    """Admin dashboard queries and metrics succeed with safe defaults when community is disabled."""
    resp = client.get("/admin/dashboard", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert "system" in data
    assert data["community"]["published_posts"] == 0
    assert data["community"]["open_reports"] == 0
