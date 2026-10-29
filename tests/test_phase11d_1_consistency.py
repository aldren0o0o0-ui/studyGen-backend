from uuid import uuid4
import pytest
from app.commands.check_moderation_consistency import check
from app.database.models.auth import User
from app.database.models.moderation import ContentReport, UserRestriction
from app.database.models.social import Post
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.services.user_restriction_service import assert_can_comment
from app.commands.expire_user_restrictions import main as expire_restrictions
from datetime import timedelta

pytestmark=[pytest.mark.moderation,pytest.mark.security]

def test_deliberate_corruption_is_detected():
    now=utc_now();a,b,p,r=map(lambda _:str(uuid4()),range(4))
    with SessionLocal() as db:
        for uid in (a,b):db.add(User(id=uid,email=uid+'@corrupt.test',normalized_email=uid+'@corrupt.test',password_hash='x',display_name='x',status='ACTIVE',role='USER',password_changed_at=now,created_at=now,updated_at=now))
        db.add(Post(id=p,author_user_id=b,caption='x',post_type='TEXT',visibility='PUBLIC',status='HIDDEN',allow_comments=True,published_at=now,created_at=now,updated_at=now))
        db.add(ContentReport(id=r,reporter_user_id=a,resource_type='POST',resource_id=p,reported_user_id=b,reason='SPAM',status='RESOLVED',priority='NORMAL',resource_status_at_report='PUBLISHED',resource_created_at=now,created_at=now,updated_at=now))
        db.commit();issues=check(db)
    assert f'resolved_report_incomplete:{r}' in issues
    assert f'moderated_post_without_audit:{p}' in issues

def test_expiration_is_scheduler_independent_then_maintenance_marks_expired():
    now=utc_now();target,actor=str(uuid4()),str(uuid4())
    with SessionLocal() as db:
        for uid,role in ((target,'USER'),(actor,'MODERATOR')):db.add(User(id=uid,email=uid+'@expiry.test',normalized_email=uid+'@expiry.test',password_hash='x',display_name='x',status='ACTIVE',role=role,password_changed_at=now,created_at=now,updated_at=now))
        row=UserRestriction(id=str(uuid4()),user_id=target,restriction_type='COMMENTING',status='ACTIVE',reason='expired',starts_at=now-timedelta(days=2),ends_at=now-timedelta(days=1),created_by_user_id=actor,created_at=now-timedelta(days=2),updated_at=now-timedelta(days=2));db.add(row);db.commit();rid=row.id
        assert_can_comment(db,target)
    expire_restrictions()
    with SessionLocal() as db:assert db.get(UserRestriction,rid).status=='EXPIRED'
