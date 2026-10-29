"""Phase 11A profiles, privacy, feed, post lifecycle, and media security."""
import uuid

import pytest

pytestmark = pytest.mark.social_foundation


def _configure(client, username="learner_one", headers=None):
    response = client.patch("/community/me", headers=headers or {}, json={"username": username, "bio": "Learning together"})
    assert response.status_code == 200, response.text
    return response.json()


def _register(client, username):
    email = f"{username}@example.com"
    response = client.post("/auth/register", json={"email": email, "display_name": username.title(), "password": "correct-horse-battery"})
    assert response.status_code == 201, response.text
    result = response.json(); headers = {"Authorization": f"Bearer {result['access_token']}"}
    profile = _configure(client, username, headers)
    return result, headers, profile


def _draft(client, visibility="PUBLIC", recipients=None, caption="Study note", headers=None):
    response = client.post("/posts", headers=headers or {}, json={"caption": caption, "visibility": visibility, "recipient_user_ids": recipients or []})
    assert response.status_code == 201, response.text
    return response.json()


def _publish(client, post_id, headers=None):
    response = client.post(f"/posts/{post_id}/publish", headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_validation_privacy_and_search(client):
    mine = _configure(client)
    assert "email" not in mine and mine["username"] == "learner_one"
    assert client.patch("/community/me", json={"username": "admin"}).status_code == 422
    _, other_headers, other = _register(client, "learner_two")
    assert client.patch("/community/me", headers=other_headers, json={"username": "LEARNER_ONE"}).status_code == 409
    found = client.get("/community/users/search?q=learner").json()
    assert all("email" not in user for user in found)
    public = client.get(f"/community/users/{other['user_id']}").json()
    assert set(public) == {"user_id", "username", "display_name", "profile_image_url", "bio", "joined_at"}


def test_public_private_specific_visibility_and_direct_id_security(client):
    _configure(client, "author_user")
    _, allowed_headers, allowed = _register(client, "allowed_user")
    _, stranger_headers, _ = _register(client, "stranger_user")

    public = _publish(client, _draft(client)["post_id"])
    private = _publish(client, _draft(client, "PRIVATE")["post_id"])
    specific = _publish(client, _draft(client, "SPECIFIC_USERS", [allowed["user_id"]])["post_id"])

    assert client.get(f"/posts/{public['post_id']}", headers=stranger_headers).status_code == 200
    assert client.get(f"/posts/{private['post_id']}", headers=stranger_headers).status_code == 404
    assert client.get(f"/posts/{specific['post_id']}", headers=allowed_headers).status_code == 200
    assert client.get(f"/posts/{specific['post_id']}", headers=stranger_headers).status_code == 404
    feed_ids = {p["post_id"] for p in client.get("/posts/feed", headers=allowed_headers).json()["items"]}
    assert public["post_id"] in feed_ids and specific["post_id"] in feed_ids and private["post_id"] not in feed_ids
    mine_ids = {p["post_id"] for p in client.get("/posts/mine").json()["items"]}
    assert {public["post_id"], private["post_id"], specific["post_id"]} <= mine_ids


def test_edit_delete_and_privacy_revocation(client):
    _configure(client, "privacy_author")
    _, recipient_headers, recipient = _register(client, "privacy_recipient")
    post = _publish(client, _draft(client, "SPECIFIC_USERS", [recipient["user_id"]])["post_id"])
    assert client.get(f"/posts/{post['post_id']}", headers=recipient_headers).status_code == 200
    changed = client.patch(f"/posts/{post['post_id']}", json={"caption": "Edited", "visibility": "PRIVATE"})
    assert changed.status_code == 200 and changed.json()["edited_at"]
    assert client.get(f"/posts/{post['post_id']}", headers=recipient_headers).status_code == 404
    assert client.delete(f"/posts/{post['post_id']}", headers=recipient_headers).status_code == 404
    assert client.delete(f"/posts/{post['post_id']}").status_code == 200
    assert client.get(f"/posts/{post['post_id']}").status_code == 404


def test_validation_recipient_and_empty_post(client):
    _configure(client, "validation_user")
    assert client.post("/posts", json={"visibility": "SPECIFIC_USERS", "recipient_user_ids": []}).status_code == 422
    draft = _draft(client, caption=None)
    assert client.post(f"/posts/{draft['post_id']}/publish").status_code == 422
    assert client.post("/posts", json={"caption": "<script>alert(1)</script>"}).status_code == 422


def test_document_media_authorization_and_isolation(client):
    _configure(client, "media_author")
    _, stranger_headers, _ = _register(client, "media_stranger")
    draft = _draft(client, "PRIVATE", caption=None)
    upload = client.post(f"/posts/{draft['post_id']}/media", files={"file": ("notes.txt", b"private study notes", "text/plain")})
    assert upload.status_code == 201, upload.text
    media = upload.json(); assert "storage" not in media and media["processing_status"] == "READY"
    _publish(client, draft["post_id"])
    assert client.get(media["url"]).status_code == 200
    assert client.get(media["url"], headers=stranger_headers).status_code == 404
    assert client.get(f"/posts/{uuid.uuid4()}/media/{media['media_id']}").status_code == 404


def test_media_validation_and_pagination(client):
    _configure(client, "feed_author")
    draft = _draft(client)
    assert client.post(f"/posts/{draft['post_id']}/media", files={"file": ("../bad.txt", b"x", "text/plain")}).status_code == 422
    assert client.post(f"/posts/{draft['post_id']}/media", files={"file": ("fake.png", b"not png", "image/png")}).status_code == 422
    for index in range(3): _publish(client, _draft(client, caption=f"Post {index}")["post_id"])
    first = client.get("/posts/feed?limit=2").json(); second = client.get(f"/posts/feed?limit=2&cursor={first['next_cursor']}").json()
    assert first["next_cursor"] and not ({p["post_id"] for p in first["items"]} & {p["post_id"] for p in second["items"]})
