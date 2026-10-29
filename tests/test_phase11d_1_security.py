"""Phase 11D.1 RBAC, spoofing, privacy, and token-state security."""
from datetime import timedelta
from uuid import uuid4
import json
import pytest
from sqlalchemy import select
from app.database.models.auth import User
from app.database.models.social import Post
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.services.moderation_service import audit
from app.services.post_service import PostError
from app.services.token_service import create_access_token
from config import settings

pytestmark=[pytest.mark.moderation,pytest.mark.security]

def user(db,role="USER"):
    uid=str(uuid4()); now=utc_now(); row=User(id=uid,email=uid+'@security.test',normalized_email=uid+'@security.test',password_hash='x',display_name='Security',username=uid[:12],normalized_username=uid[:12],status='ACTIVE',role=role,password_changed_at=now,created_at=now,updated_at=now)
    db.add(row);db.commit();db.refresh(row);db.expunge(row);return row
def auth(row):return {'Authorization':'Bearer '+create_access_token(row,settings)}
def post(db,author):
    now=utc_now();row=Post(id=str(uuid4()),author_user_id=author.id,caption='security target',post_type='TEXT',visibility='PUBLIC',status='PUBLISHED',allow_comments=True,published_at=now,created_at=now,updated_at=now);db.add(row);db.commit();db.refresh(row);db.expunge(row);return row

def test_user_rbac_matrix_and_mass_assignment_rejected(client):
    with SessionLocal() as db: normal=user(db); target=user(db); p=post(db,target)
    h=auth(normal)
    calls=[('get','/moderation/reports',None),('post','/moderation/reports/x/assign',{}),('post',f'/moderation/posts/{p.id}/hide',{}),('post',f'/moderation/users/{target.id}/restrictions',{'restriction_type':'COMMENTING','reason':'x'}),('get','/moderation/actions',None),('post',f'/moderation/users/{target.id}/deactivate',{'reason':'x'})]
    for method,url,body in calls:
        response=getattr(client,method)(url,headers=h,**({'json':body} if body is not None else {}));assert response.status_code==403,(method,url,response.text)
    report=client.post('/reports',headers=h,json={'resource_type':'POST','resource_id':p.id,'reason':'SPAM','actor_user_id':target.id})
    assert report.status_code==422

def test_moderator_admin_boundary_self_protection_and_admin_own_content(client):
    with SessionLocal() as db: moderator=user(db,'MODERATOR'); admin=user(db,'ADMIN'); target=user(db); own=post(db,admin)
    assert client.post(f'/moderation/users/{target.id}/deactivate',headers=auth(moderator),json={'reason':'x'}).status_code==403
    assert client.post(f'/moderation/users/{target.id}/restrictions',headers=auth(moderator),json={'restriction_type':'ACCOUNT','reason':'x'}).status_code==403
    assert client.post(f'/moderation/users/{moderator.id}/restrictions',headers=auth(moderator),json={'restriction_type':'COMMENTING','reason':'x'}).status_code==409
    assert client.post(f'/moderation/users/{admin.id}/deactivate',headers=auth(admin),json={'reason':'x'}).status_code==409
    assert client.post(f'/moderation/posts/{own.id}/hide',headers=auth(admin),json={}).status_code==409

def test_stale_role_claim_uses_current_database_role(client):
    with SessionLocal() as db: actor=user(db,'MODERATOR')
    token=auth(actor)
    assert client.get('/moderation/reports',headers=token).status_code==200
    with SessionLocal.begin() as db:
        row=db.get(User,actor.id);row.role='USER'
    assert client.get('/moderation/reports',headers=token).status_code==403
    with SessionLocal.begin() as db:
        row=db.get(User,actor.id);row.role='MODERATOR'
    assert client.get('/moderation/reports',headers=token).status_code==200

def test_account_restriction_blocks_existing_token_and_noncommunity_api(client):
    with SessionLocal() as db: target=user(db); admin=user(db,'ADMIN')
    token=auth(target)
    response=client.post(f'/moderation/users/{target.id}/restrictions',headers=auth(admin),json={'restriction_type':'ACCOUNT','ends_at':(utc_now()+timedelta(days=1)).isoformat(),'reason':'security hold'})
    assert response.status_code==201
    assert client.get('/auth/me',headers=token).status_code==401
    assert client.get('/documents',headers=token).status_code==401

def test_reporter_and_internal_fields_do_not_leak(client):
    with SessionLocal() as db: author=user(db); reporter=user(db); moderator=user(db,'MODERATOR'); p=post(db,author)
    ack=client.post('/reports',headers=auth(reporter),json={'resource_type':'POST','resource_id':p.id,'reason':'SPAM','note':'private report note'})
    assert ack.status_code==201
    normal_responses=[client.get('/posts/feed',headers=auth(author)),client.get(f'/posts/{p.id}',headers=auth(author)),client.get(f'/community/users/{author.id}',headers=auth(author))]
    forbidden={'reporter_user_id','reporter_username','reporter_email','reporter','reported_by','resolution_note','assigned_moderator_user_id','priority'}
    def keys(value):
        if isinstance(value,dict):return set(value)|set().union(*(keys(v) for v in value.values()))
        if isinstance(value,list):return set().union(*(keys(v) for v in value)) if value else set()
        return set()
    assert all(not(forbidden & keys(r.json())) for r in normal_responses if r.status_code==200)
    detail=client.get('/moderation/reports/'+ack.json()['report_id'],headers=auth(moderator));assert detail.status_code==200 and detail.json()['reporter_user_id']==reporter.id

def test_audit_metadata_rejects_secrets_and_oversize():
    with SessionLocal() as db:
        actor=user(db,'ADMIN')
        with pytest.raises(PostError):audit(db,actor.id,'USER_REACTIVATED','USER',actor.id,metadata={'authorization':'Bearer secret'})
        with pytest.raises(PostError):audit(db,actor.id,'USER_REACTIVATED','USER',actor.id,metadata={'safe':'x'*5000})

def test_deactivation_revokes_refresh_and_reactivation_does_not_resurrect(client):
    email=str(uuid4())+'@tokens.test';registered=client.post('/auth/register',json={'email':email,'password':'Correct-Horse-123!','display_name':'Token User'})
    assert registered.status_code==201;data=registered.json();target_id=data['user']['id'];old_access={'Authorization':'Bearer '+data['access_token']};old_refresh=data['refresh_token']
    with SessionLocal() as db: admin=user(db,'ADMIN')
    assert client.post(f'/moderation/users/{target_id}/deactivate',headers=auth(admin),json={'reason':'security'}).status_code==200
    assert client.get('/auth/me',headers=old_access).status_code==401
    assert client.post('/auth/refresh',json={'refresh_token':old_refresh}).status_code==401
    assert client.post(f'/moderation/users/{target_id}/reactivate',headers=auth(admin),json={'reason':'cleared'}).status_code==200
    assert client.post('/auth/refresh',json={'refresh_token':old_refresh}).status_code==401
    login=client.post('/auth/login',json={'email':email,'password':'Correct-Horse-123!'});assert login.status_code==200
