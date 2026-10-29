from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import jwt,pytest
from sqlalchemy import select

from app.database.models.auth import User
from app.database.models.moderation import UserRestriction
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.services import admin_service,auth_service,moderation_service
from app.services.password_service import hash_password
from app.services.post_service import PostError
from app.services.token_service import TokenError,create_access_token,decode_access_token
from config import settings

pytestmark=[pytest.mark.admin,pytest.mark.security]
def user(db,role="USER",status="ACTIVE",password="Security-password-123!"):
    now=utc_now();uid=str(uuid4());row=User(id=uid,email=uid+'@security.test',normalized_email=uid+'@security.test',password_hash=hash_password(password),display_name='Security',username=uid[:12],normalized_username=uid[:12],role=role,status=status,password_changed_at=now,created_at=now,updated_at=now);db.add(row);db.commit();db.refresh(row);return row
def header(token):return {"Authorization":"Bearer "+token}

def test_session_version_claim_is_strict_and_persisted(db_session):
    row=user(db_session);token=create_access_token(row,settings);claims=decode_access_token(token,settings)
    assert row.session_version==1 and claims['sv']==1
    base={k:v for k,v in claims.items() if k!='sv'}
    legacy=jwt.encode(base,settings.jwt_secret_key,algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):decode_access_token(legacy,settings)
    for bad in (None,"1",-1,True):
        malformed=jwt.encode({**base,'sv':bad},settings.jwt_secret_key,algorithm=settings.jwt_algorithm)
        with pytest.raises(TokenError):decode_access_token(malformed,settings)

def test_access_tokens_never_resurrect_across_lifecycle_cycles(client):
    with SessionLocal() as db:
        admin=user(db,'ADMIN');target=user(db);admin_id,target_id=admin.id,target.id;old=create_access_token(target,settings);original=target.session_version
        moderation_service.set_user_active(db,target.id,admin,False,'security cycle')
    assert client.get('/auth/me',headers=header(old)).status_code==401
    with SessionLocal() as db:
        moderation_service.set_user_active(db,target_id,db.get(User,admin_id),True,'security cycle');assert db.get(User,target_id).session_version==original+2
    assert client.get('/auth/me',headers=header(old)).status_code==401
    with SessionLocal() as db:
        current=db.get(User,target_id);new=create_access_token(current,settings);actor=db.get(User,admin_id)
        moderation_service.set_user_active(db,target_id,actor,False,'cycle two');moderation_service.set_user_active(db,target_id,actor,True,'cycle two')
    assert client.get('/auth/me',headers=header(old)).status_code==401
    assert client.get('/auth/me',headers=header(new)).status_code==401
    with SessionLocal() as db:fresh=create_access_token(db.get(User,target_id),settings)
    assert client.get('/auth/me',headers=header(fresh)).status_code==200

def test_password_change_increments_session_version_and_revokes_old_access(client):
    password='Security-password-123!'
    with SessionLocal() as db:target=user(db,password=password);old=create_access_token(target,settings);before=target.session_version;auth_service.change_password(db,target,password,'New-security-password-123!',settings)
    assert client.get('/auth/me',headers=header(old)).status_code==401
    with SessionLocal() as db:assert db.get(User,target.id).session_version==before+1

def test_effective_restriction_counts_and_expired_reapplication(db_session,monkeypatch):
    admin=user(db_session,'ADMIN');target=user(db_session);now=utc_now()
    expired=UserRestriction(id=str(uuid4()),user_id=target.id,restriction_type='COMMENTING',status='ACTIVE',reason='old',starts_at=now-timedelta(days=2),ends_at=now-timedelta(days=1),created_by_user_id=admin.id,created_at=now-timedelta(days=2),updated_at=now-timedelta(days=2))
    active=UserRestriction(id=str(uuid4()),user_id=target.id,restriction_type='REACTING',status='ACTIVE',reason='current',starts_at=now-timedelta(hours=1),ends_at=now+timedelta(days=1),created_by_user_id=admin.id,created_at=now,updated_at=now)
    revoked=UserRestriction(id=str(uuid4()),user_id=target.id,restriction_type='SHARING',status='REVOKED',reason='revoked',starts_at=now-timedelta(days=2),ends_at=None,created_by_user_id=admin.id,created_at=now,updated_at=now)
    db_session.add_all([expired,active,revoked]);db_session.commit();monkeypatch.setattr(admin_service,'_redis_health',lambda:'UNKNOWN')
    rows,_=admin_service.list_users(db_session,search=target.email);assert rows[0][1]==1
    assert admin_service.dashboard(db_session)['moderation']['active_restrictions']==1
    payload=SimpleNamespace(restriction_type='COMMENTING',starts_at=None,ends_at=now+timedelta(days=3),reason='renewed',source_report_id=None)
    created=moderation_service.create_restriction(db_session,target.id,admin,payload)
    assert created.status=='ACTIVE' and db_session.get(UserRestriction,expired.id).status=='EXPIRED'

def test_governance_target_hierarchy(db_session):
    moderator=user(db_session,'MODERATOR');admin=user(db_session,'ADMIN');other_admin=user(db_session,'ADMIN');peer=user(db_session,'MODERATOR');ordinary=user(db_session)
    payload=SimpleNamespace(restriction_type='COMMUNITY_ACCESS',starts_at=None,ends_at=None,reason='policy',source_report_id=None)
    assert moderation_service.create_restriction(db_session,ordinary.id,moderator,payload).status=='ACTIVE'
    for target in (peer,admin):
        with pytest.raises(PostError) as caught:moderation_service.create_restriction(db_session,target.id,moderator,payload)
        assert caught.value.code=='TARGET_ROLE_PROTECTED';db_session.rollback()
    with pytest.raises(PostError) as caught:moderation_service.set_user_active(db_session,other_admin.id,admin,False,'peer')
    assert caught.value.code=='PEER_ADMIN_PROTECTED';db_session.rollback()

def test_strict_admin_filters_and_recursive_sensitive_absence(client):
    with SessionLocal() as db:admin=user(db,'ADMIN');target=user(db);headers=header(create_access_token(admin,settings));target_id=target.id
    assert client.get('/admin/restrictions?status=BOGUS',headers=headers).status_code==422
    assert client.get('/admin/audit?action_type=BOGUS',headers=headers).status_code==422
    payload=client.get(f'/admin/users/{target_id}',headers=headers).json();encoded=str(payload).lower()
    for field in ('password_hash','refresh_token','access_token','authorization','database_url','private_key'):assert field not in encoded
