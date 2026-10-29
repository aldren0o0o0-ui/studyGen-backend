from uuid import uuid4
import pytest
from sqlalchemy import select
from app.commands.create_admin import BootstrapError,create_admin
from app.commands.check_admin_consistency import check as check_admin_consistency
from app.database.models.auth import User
from app.database.models.moderation import ModerationAction
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.services.password_service import verify_password
from app.services.token_service import create_access_token
from config import settings

pytestmark=[pytest.mark.admin,pytest.mark.security]
def make_user(db,role="USER",status="ACTIVE",name="Admin target"):
    uid=str(uuid4());now=utc_now();row=User(id=uid,email=uid+'@admin.test',normalized_email=uid+'@admin.test',password_hash='x',display_name=name,username=uid[:12],normalized_username=uid[:12],status=status,role=role,password_changed_at=now,created_at=now,updated_at=now);db.add(row);db.commit();db.refresh(row);db.expunge(row);return row
def auth(row):return {'Authorization':'Bearer '+create_access_token(row,settings)}

def test_admin_rbac_and_sensitive_field_boundary(client):
    with SessionLocal() as db:normal=make_user(db);moderator=make_user(db,'MODERATOR');admin=make_user(db,'ADMIN')
    for actor in (normal,moderator):
        for path in ('/admin/dashboard','/admin/users',f'/admin/users/{admin.id}','/admin/restrictions','/admin/audit'):
            assert client.get(path,headers=auth(actor)).status_code==403
    response=client.get('/admin/users',headers=auth(admin));assert response.status_code==200
    encoded=response.text.lower()
    for secret in ('password_hash','refresh_token','token_hash','jwt_secret'):assert secret not in encoded

def test_dashboard_users_search_filters_pagination_and_detail(client):
    with SessionLocal() as db:admin=make_user(db,'ADMIN');target=make_user(db,name='Searchable Person');moderator=make_user(db,'MODERATOR')
    headers=auth(admin);dashboard=client.get('/admin/dashboard',headers=headers);assert dashboard.status_code==200 and dashboard.json()['users']['moderators']==1
    assert client.get('/admin/users?search=Searchable',headers=headers).json()['items'][0]['user_id']==target.id
    assert all(row['role']=='MODERATOR' for row in client.get('/admin/users?role=MODERATOR',headers=headers).json()['items'])
    first=client.get('/admin/users?limit=1',headers=headers).json();assert first['next_cursor'];second=client.get('/admin/users?limit=1&cursor='+first['next_cursor'],headers=headers).json();assert second['items'][0]['user_id']!=first['items'][0]['user_id']
    detail=client.get(f'/admin/users/{target.id}',headers=headers);assert detail.status_code==200 and detail.json()['user']['email']==target.email
    if settings.enable_community_features:
        assert client.get('/community/users/'+target.id,headers=headers).status_code==200 and 'email' not in client.get('/community/users/'+target.id,headers=headers).json()

def test_role_transitions_are_atomic_audited_and_self_protected(client):
    with SessionLocal() as db:admin=make_user(db,'ADMIN');target=make_user(db)
    headers=auth(admin);promoted=client.post(f'/admin/users/{target.id}/promote-moderator',headers=headers,json={'reason':'trusted reviewer'});assert promoted.status_code==200 and promoted.json()['role']=='MODERATOR'
    assert client.post(f'/admin/users/{target.id}/promote-moderator',headers=headers,json={'reason':'duplicate'}).status_code==409
    demoted=client.post(f'/admin/users/{target.id}/demote-moderator',headers=headers,json={'reason':'rotation complete'});assert demoted.status_code==200 and demoted.json()['role']=='USER'
    assert client.post(f'/admin/users/{admin.id}/demote-moderator',headers=headers,json={'reason':'self'}).status_code==409
    with SessionLocal() as db:actions=list(db.scalars(select(ModerationAction).where(ModerationAction.target_user_id==target.id)).all());assert [a.action_type for a in actions]==['USER_PROMOTED_TO_MODERATOR','USER_DEMOTED_FROM_MODERATOR']

def test_stale_moderator_token_loses_authority_after_admin_demotion(client):
    with SessionLocal() as db:admin=make_user(db,'ADMIN');moderator=make_user(db,'MODERATOR')
    stale=auth(moderator)
    if settings.enable_community_features:
        assert client.get('/moderation/reports',headers=stale).status_code==200
        assert client.post(f'/admin/users/{moderator.id}/demote-moderator',headers=auth(admin),json={'reason':'access removed'}).status_code==200
        assert client.get('/moderation/reports',headers=stale).status_code==403
    else:
        assert client.post(f'/admin/users/{moderator.id}/demote-moderator',headers=auth(admin),json={'reason':'access removed'}).status_code==200

def test_registration_cannot_inject_privileged_role(client):
    email=str(uuid4())+'@inject.test';response=client.post('/auth/register',json={'email':email,'display_name':'Injected','password':'correct-horse-battery','role':'ADMIN','status':'ACTIVE'});assert response.status_code==201
    assert response.json()['user']['role']=='USER'

def test_bootstrap_hashes_password_and_refuses_duplicates(db_session):
    password='Bootstrap-password-123!';row=create_admin(db_session,email='root@example.test',username='root_admin',display_name='Root Admin',password=password);assert row.role=='ADMIN' and row.status=='ACTIVE' and row.password_hash!=password and verify_password(password,row.password_hash)
    with pytest.raises(BootstrapError):create_admin(db_session,email='root@example.test',username='different_admin',display_name='Duplicate',password=password)
    existing=make_user(db_session);existing.email='occupied@example.test';existing.normalized_email='occupied@example.test';db_session.add(existing);db_session.commit()
    with pytest.raises(BootstrapError):create_admin(db_session,email='occupied@example.test',username='occupied_admin',display_name='No Promotion',password=password)

def test_consistency_checker_accepts_database_terminal_statuses(db_session):
    make_user(db_session,'ADMIN');make_user(db_session,status='DISABLED');make_user(db_session,status='DELETED')
    active_admins,issues=check_admin_consistency(db_session)
    assert active_admins==1
    assert not [issue for issue in issues if issue.startswith('invalid user state:')]
