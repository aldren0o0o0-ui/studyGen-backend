from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.database.models.auth import User
from app.database.models.moderation import ModerationAction
from app.database.models.social import Post
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.services.token_service import create_access_token
from config import settings

pytestmark = pytest.mark.moderation


def _user(db, role="USER"):
    uid=str(uuid4()); now=utc_now(); row=User(id=uid,email=f"{uid}@moderation.test",normalized_email=f"{uid}@moderation.test",password_hash="x",display_name="Test",username=uid[:12],normalized_username=uid[:12],status="ACTIVE",role=role,password_changed_at=now,created_at=now,updated_at=now)
    db.add(row); db.commit(); db.refresh(row); db.expunge(row); return row


def _auth(user): return {"Authorization":f"Bearer {create_access_token(user,settings)}"}


def _post(db, author):
    now=utc_now(); row=Post(id=str(uuid4()),author_user_id=author.id,caption="report me",post_type="TEXT",visibility="PUBLIC",status="PUBLISHED",allow_comments=True,published_at=now,created_at=now,updated_at=now)
    db.add(row); db.commit(); db.refresh(row); db.expunge(row); return row


def test_report_privacy_idempotence_and_rbac(client):
    with SessionLocal() as db: author,reporter,moderator=_user(db),_user(db),_user(db,"MODERATOR"); post=_post(db,author)
    body={"resource_type":"POST","resource_id":post.id,"reason":"SPAM","note":"Advertising"}
    first=client.post("/reports",json=body,headers=_auth(reporter)); second=client.post("/reports",json=body,headers=_auth(reporter))
    assert first.status_code==second.status_code==201 and first.json()["report_id"]==second.json()["report_id"]
    assert "reporter_user_id" not in first.json() and client.get("/moderation/reports",headers=_auth(reporter)).status_code==403
    queue=client.get("/moderation/reports",headers=_auth(moderator)); assert queue.status_code==200 and "reporter_user_id" not in queue.json()["items"][0]


def test_hide_restore_and_audit(client):
    with SessionLocal() as db: author,viewer,moderator=_user(db),_user(db),_user(db,"MODERATOR"); post=_post(db,author)
    assert client.post(f"/moderation/posts/{post.id}/hide",json={},headers=_auth(moderator)).status_code==200
    assert client.get(f"/posts/{post.id}",headers=_auth(viewer)).status_code==404
    assert client.post(f"/moderation/posts/{post.id}/restore",json={},headers=_auth(moderator)).status_code==200
    with SessionLocal() as db: assert db.scalar(select(ModerationAction).where(ModerationAction.target_id==post.id,ModerationAction.action_type=="POST_HIDDEN"))


def test_restriction_revoke_and_deactivation(client):
    with SessionLocal() as db: target,moderator,admin=_user(db),_user(db,"MODERATOR"),_user(db,"ADMIN"); post=_post(db,moderator)
    response=client.post(f"/moderation/users/{target.id}/restrictions",json={"restriction_type":"COMMENTING","ends_at":(utc_now()+timedelta(days=1)).isoformat(),"reason":"cooldown"},headers=_auth(moderator)); assert response.status_code==201
    blocked=client.post(f"/posts/{post.id}/comments",json={"content":"blocked"},headers=_auth(target)); assert blocked.status_code==422 and blocked.json()["detail"]["code"]=="COMMENTING_RESTRICTED"
    assert client.post(f"/moderation/restrictions/{response.json()['id']}/revoke",json={"revocation_reason":"reviewed"},headers=_auth(moderator)).status_code==200
    assert client.post(f"/posts/{post.id}/comments",json={"content":"allowed"},headers=_auth(target)).status_code==201
    token=_auth(target); assert client.post(f"/moderation/users/{target.id}/deactivate",json={"reason":"policy"},headers=_auth(moderator)).status_code==403
    assert client.post(f"/moderation/users/{target.id}/deactivate",json={"reason":"policy"},headers=_auth(admin)).status_code==200
    assert client.get("/posts/feed",headers=token).status_code==401
