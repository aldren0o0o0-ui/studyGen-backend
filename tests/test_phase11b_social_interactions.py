"""Phase 11B interaction behavior, authorization, idempotency, and counters."""
import pytest
from app.services.social_counter_service import rebuild
from tests.test_phase11a_social import _configure, _draft, _publish, _register

pytestmark = pytest.mark.social_interactions


def test_reactions_are_idempotent_changeable_and_private_safe(client):
    _configure(client, "reaction_author")
    _, other, _ = _register(client, "reaction_user")
    public = _publish(client, _draft(client)["post_id"])
    private = _publish(client, _draft(client, "PRIVATE")["post_id"])
    post_id = public["post_id"]
    first = client.put(f"/posts/{post_id}/reaction", headers=other, json={"reaction_type": "LIKE"})
    assert first.status_code == 200 and first.json()["reaction_count"] == 1
    same = client.put(f"/posts/{post_id}/reaction", headers=other, json={"reaction_type": "LIKE"}).json()
    assert same["reaction_count"] == 1
    changed = client.put(f"/posts/{post_id}/reaction", headers=other, json={"reaction_type": "HELPFUL"}).json()
    assert changed == {"post_id": post_id, "current_user_reaction": "HELPFUL", "reaction_count": 1}
    assert client.get(f"/posts/{post_id}", headers=other).json()["current_user_reaction"] == "HELPFUL"
    removed = client.delete(f"/posts/{post_id}/reaction", headers=other).json()
    assert removed["reaction_count"] == 0 and removed["current_user_reaction"] is None
    assert client.put(f"/posts/{private['post_id']}/reaction", headers=other, json={"reaction_type": "LIKE"}).status_code == 404
    assert client.put(f"/posts/{post_id}/reaction", headers=other, json={"reaction_type": "LOVE"}).status_code == 422


def test_comments_replies_edit_delete_and_disabled_policy(client):
    _configure(client, "comment_author")
    _, other, _ = _register(client, "comment_user")
    post = _publish(client, _draft(client)["post_id"])
    post_id = post["post_id"]
    top = client.post(f"/posts/{post_id}/comments", headers=other, json={"content": "Useful explanation"})
    assert top.status_code == 201
    top_id = top.json()["comment_id"]
    reply = client.post(f"/comments/{top_id}/replies", json={"content": "Thank you"}).json()
    nested = client.post(f"/comments/{reply['comment_id']}/replies", headers=other, json={"content": "One level only"}).json()
    assert nested["parent_comment_id"] == top_id
    assert client.patch(f"/comments/{top_id}", json={"content": "hijack"}).status_code == 409
    edited = client.patch(f"/comments/{top_id}", headers=other, json={"content": "Edited explanation"})
    assert edited.status_code == 200 and edited.json()["edited_at"]
    assert client.post(f"/posts/{post_id}/comments", json={"content": "   "}).status_code == 422
    assert client.delete(f"/comments/{top_id}", headers=other).status_code == 204
    page = client.get(f"/posts/{post_id}/comments").json()
    assert page["items"][0]["status"] == "DELETED" and page["items"][0]["content"] is None
    assert page["items"][0]["reply_count"] == 2
    assert client.get(f"/posts/{post_id}").json()["comment_count"] == 2
    assert client.patch(f"/posts/{post_id}", json={"allow_comments": False}).status_code == 200
    assert client.post(f"/posts/{post_id}/comments", headers=other, json={"content": "blocked"}).status_code == 409
    assert client.get(f"/posts/{post_id}/comments", headers=other).status_code == 200


def test_saves_are_private_idempotent_and_revalidate_access(client):
    _configure(client, "save_author")
    _, recipient_headers, recipient = _register(client, "save_recipient")
    post = _publish(client, _draft(client, "SPECIFIC_USERS", [recipient["user_id"]])["post_id"])
    post_id = post["post_id"]
    assert client.put(f"/posts/{post_id}/save", headers=recipient_headers).json()["save_count"] == 1
    assert client.put(f"/posts/{post_id}/save", headers=recipient_headers).json()["save_count"] == 1
    saved = client.get("/community/saved", headers=recipient_headers).json()["items"]
    assert [item["post_id"] for item in saved] == [post_id]
    client.patch(f"/posts/{post_id}", json={"visibility": "PRIVATE"})
    assert client.get("/community/saved", headers=recipient_headers).json()["items"] == []
    assert client.delete(f"/posts/{post_id}/save", headers=recipient_headers).status_code == 404
    client.patch(f"/posts/{post_id}", json={"visibility": "PUBLIC"})
    assert client.delete(f"/posts/{post_id}/save", headers=recipient_headers).json()["save_count"] == 0
    assert client.delete(f"/posts/{post_id}/save", headers=recipient_headers).json()["save_count"] == 0


def test_share_privacy_canonical_original_and_unavailable_state(client):
    _configure(client, "share_author")
    _, allowed_headers, allowed = _register(client, "share_allowed")
    _, outsider_headers, outsider = _register(client, "share_outsider")
    public = _publish(client, _draft(client)["post_id"])
    specific = _publish(client, _draft(client, "SPECIFIC_USERS", [allowed["user_id"]])["post_id"])
    private = _publish(client, _draft(client, "PRIVATE")["post_id"])
    shared = client.post(f"/posts/{public['post_id']}/share", headers=allowed_headers, json={"caption": "Worth reading", "visibility": "PUBLIC"})
    assert shared.status_code == 201 and shared.json()["shared_post"]["post_id"] == public["post_id"]
    reshared = client.post(f"/posts/{shared.json()['post_id']}/share", headers=outsider_headers, json={"visibility": "PRIVATE"})
    assert reshared.status_code == 201 and reshared.json()["shared_post"]["post_id"] == public["post_id"]
    assert client.post(f"/posts/{specific['post_id']}/share", headers=allowed_headers, json={"visibility": "PUBLIC"}).status_code == 409
    assert client.post(f"/posts/{specific['post_id']}/share", headers=allowed_headers, json={"visibility": "SPECIFIC_USERS", "recipient_user_ids": [outsider["user_id"]]}).status_code == 409
    assert client.post(f"/posts/{private['post_id']}/share", json={"visibility": "PRIVATE"}).status_code == 409
    client.delete(f"/posts/{public['post_id']}")
    unavailable = client.get(f"/posts/{shared.json()['post_id']}", headers=allowed_headers).json()["shared_post"]
    assert unavailable["available"] is False and unavailable["caption"] is None and unavailable["media"] == []


def test_counter_rebuild_dry_run_and_apply(client, db_session):
    _configure(client, "counter_author")
    post = _publish(client, _draft(client)["post_id"])
    from app.database.models.social import Post
    row = db_session.get(Post, post["post_id"]); row.reaction_count = 9; db_session.commit()
    differences = rebuild(db_session, apply=False)
    assert any(item[:3] == (post["post_id"], "reaction_count", 9) for item in differences)
    assert db_session.get(Post, post["post_id"]).reaction_count == 9
    rebuild(db_session, apply=True)
    assert db_session.get(Post, post["post_id"]).reaction_count == 0
    assert rebuild(db_session, apply=False) == []


def test_comment_reply_pagination_and_share_eligible_filtering(client):
    _configure(client, "stability_author")
    _, allowed_headers, allowed = _register(client, "stability_allowed")
    _, outsider_headers, outsider = _register(client, "stability_outsider")
    post = _publish(client, _draft(client, "SPECIFIC_USERS", [allowed["user_id"]])["post_id"])
    for value in ("first", "second", "third"):
        assert client.post(f"/posts/{post['post_id']}/comments", headers=allowed_headers, json={"content": value}).status_code == 201
    first = client.get(f"/posts/{post['post_id']}/comments?limit=1", headers=allowed_headers).json()
    second = client.get(f"/posts/{post['post_id']}/comments?limit=1&cursor={first['next_cursor']}", headers=allowed_headers).json()
    assert first["next_cursor"] and first["items"][0]["comment_id"] != second["items"][0]["comment_id"]
    parent = first["items"][0]["comment_id"]
    for value in ("reply one", "reply two"):
        assert client.post(f"/comments/{parent}/replies", headers=allowed_headers, json={"content": value}).status_code == 201
    replies = client.get(f"/comments/{parent}/replies?limit=1", headers=allowed_headers).json()
    more = client.get(f"/comments/{parent}/replies?limit=1&cursor={replies['next_cursor']}", headers=allowed_headers).json()
    assert replies["next_cursor"] and replies["items"][0]["comment_id"] != more["items"][0]["comment_id"]
    eligible = client.get(f"/posts/{post['post_id']}/share-eligible-users?q=stability", headers=allowed_headers).json()
    assert allowed["user_id"] not in {item["user_id"] for item in eligible}
    assert outsider["user_id"] not in {item["user_id"] for item in eligible}
    assert client.get(f"/posts/{post['post_id']}/share-eligible-users?q=stability", headers=outsider_headers).status_code == 404


def test_three_account_privacy_revalidation_sequence(client):
    _configure(client, "privacy_sequence_a")
    _, b_headers, user_b = _register(client, "privacy_sequence_b")
    _, c_headers, user_c = _register(client, "privacy_sequence_c")
    original = _publish(client, _draft(client)["post_id"]); post_id = original["post_id"]
    assert client.put(f"/posts/{post_id}/reaction", headers=b_headers, json={"reaction_type": "HELPFUL"}).status_code == 200
    assert client.post(f"/posts/{post_id}/comments", headers=b_headers, json={"content": "historical"}).status_code == 201
    assert client.put(f"/posts/{post_id}/save", headers=b_headers).status_code == 200
    wrapper = client.post(f"/posts/{post_id}/share", headers=b_headers, json={"visibility": "PUBLIC"}).json()
    assert client.patch(f"/posts/{post_id}", json={"visibility": "PRIVATE"}).status_code == 200
    assert client.get(f"/posts/{post_id}", headers=b_headers).status_code == 404
    assert client.get("/community/saved", headers=b_headers).json()["items"] == []
    assert client.get(f"/posts/{wrapper['post_id']}", headers=b_headers).json()["shared_post"]["available"] is False
    for method,path,body in (("put",f"/posts/{post_id}/reaction",{"reaction_type":"LIKE"}),("post",f"/posts/{post_id}/comments",{"content":"blocked"}),("put",f"/posts/{post_id}/save",None)):
        assert getattr(client,method)(path,headers=b_headers,**({"json":body} if body else {})).status_code == 404
    assert client.patch(f"/posts/{post_id}", json={"visibility": "SPECIFIC_USERS", "recipient_user_ids": [user_b["user_id"]]}).status_code == 200
    assert client.get(f"/posts/{post_id}", headers=b_headers).status_code == 200
    assert client.get(f"/posts/{post_id}", headers=c_headers).status_code == 404
    assert client.patch(f"/posts/{post_id}", json={"visibility": "SPECIFIC_USERS", "recipient_user_ids": [user_c["user_id"]]}).status_code == 200
    assert client.get(f"/posts/{post_id}", headers=b_headers).status_code == 404
