from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import func,select
from app.database.models.auth import User
from app.database.models.moderation import ModerationAction,UserRestriction
from app.database.types import utc_now
from app.services import admin_service,moderation_service

pytestmark=[pytest.mark.admin,pytest.mark.security]
def add(db,role='USER',status='ACTIVE'):
    now=utc_now();uid=str(uuid4());row=User(id=uid,email=uid+'@atomic.test',normalized_email=uid+'@atomic.test',password_hash='x',display_name='Atomic',username=uid[:12],normalized_username=uid[:12],role=role,status=status,password_changed_at=now,created_at=now,updated_at=now);db.add(row);db.commit();return row
def fail(*args,**kwargs):raise RuntimeError('injected audit failure')

@pytest.mark.parametrize('promote,initial',[(True,'USER'),(False,'MODERATOR')])
def test_role_audit_failure_rolls_back(db_session,monkeypatch,promote,initial):
    actor=add(db_session,'ADMIN');target=add(db_session,initial);before=target.session_version;monkeypatch.setattr(admin_service,'audit',fail)
    with pytest.raises(RuntimeError):admin_service.change_moderator_role(db_session,target.id,actor,promote=promote,reason='fault')
    db_session.rollback();row=db_session.get(User,target.id)
    assert row.role==initial and row.session_version==before
    assert db_session.scalar(select(func.count(ModerationAction.id)).where(ModerationAction.target_user_id==target.id))==0

@pytest.mark.parametrize('active,initial',[(False,'ACTIVE'),(True,'DEACTIVATED')])
def test_status_audit_failure_rolls_back_version_and_state(db_session,monkeypatch,active,initial):
    actor=add(db_session,'ADMIN');target=add(db_session,status=initial);before=target.session_version;monkeypatch.setattr(moderation_service,'audit',fail)
    with pytest.raises(RuntimeError):moderation_service.set_user_active(db_session,target.id,actor,active,'fault')
    db_session.rollback();row=db_session.get(User,target.id)
    assert row.status==initial and row.session_version==before
    assert db_session.scalar(select(func.count(ModerationAction.id)).where(ModerationAction.target_user_id==target.id))==0

def test_restriction_create_audit_failure_rolls_back_normalization(db_session,monkeypatch):
    actor=add(db_session,'ADMIN');target=add(db_session);now=utc_now();old=UserRestriction(id=str(uuid4()),user_id=target.id,restriction_type='COMMENTING',status='ACTIVE',reason='old',starts_at=now-timedelta(days=2),ends_at=now-timedelta(days=1),created_by_user_id=actor.id,created_at=now,updated_at=now);db_session.add(old);db_session.commit();monkeypatch.setattr(moderation_service,'audit',fail)
    payload=SimpleNamespace(restriction_type='COMMENTING',starts_at=None,ends_at=None,reason='new',source_report_id=None)
    with pytest.raises(RuntimeError):moderation_service.create_restriction(db_session,target.id,actor,payload)
    db_session.rollback();assert db_session.get(UserRestriction,old.id).status=='ACTIVE'
    assert db_session.scalar(select(func.count(UserRestriction.id)).where(UserRestriction.user_id==target.id))==1

def test_restriction_revoke_audit_failure_rolls_back(db_session,monkeypatch):
    actor=add(db_session,'ADMIN');target=add(db_session);now=utc_now();row=UserRestriction(id=str(uuid4()),user_id=target.id,restriction_type='COMMENTING',status='ACTIVE',reason='active',starts_at=now,ends_at=None,created_by_user_id=actor.id,created_at=now,updated_at=now);db_session.add(row);db_session.commit();monkeypatch.setattr(moderation_service,'audit',fail)
    with pytest.raises(RuntimeError):moderation_service.revoke_restriction(db_session,row.id,actor,'fault')
    db_session.rollback();restored=db_session.get(UserRestriction,row.id)
    assert restored.status=='ACTIVE' and restored.revoked_at is None and restored.revoked_by_user_id is None
