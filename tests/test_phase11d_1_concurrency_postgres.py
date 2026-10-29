"""Real PostgreSQL Phase 11D.1 moderation race certification."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from app.database.models.auth import User
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction
from app.database.models.social import Post
from app.database.types import utc_now
from app.services import moderation_service, post_comment_service
from app.services.post_service import PostError

pytestmark=[pytest.mark.moderation,pytest.mark.concurrency,pytest.mark.postgres_integration,pytest.mark.security]

@pytest.fixture(scope='module')
def pg():
    url=os.getenv('TEST_POSTGRES_DATABASE_URL')
    if not url:pytest.skip('SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable')
    engine=create_engine(url,pool_size=12,max_overflow=4,pool_pre_ping=True)
    with engine.connect() as c:assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0016_phase12a_1_session_security'
    yield engine;engine.dispose()
@pytest.fixture(autouse=True)
def clean(pg):
    with pg.begin() as c:c.execute(text('TRUNCATE moderation_actions,user_restrictions,content_reports,saved_posts,post_comments,post_reactions,post_allowed_users,post_media,posts,refresh_tokens,users CASCADE'))

def seed(pg):
    S=sessionmaker(pg,expire_on_commit=False);db=S();now=utc_now()
    def u(role='USER'):
        uid=str(uuid4());row=User(id=uid,email=uid+'@race.test',normalized_email=uid+'@race.test',password_hash='x',display_name='Race',username=uid[:12],normalized_username=uid[:12],status='ACTIVE',role=role,password_changed_at=now,created_at=now,updated_at=now);db.add(row);return row
    author,reporter,m1,m2=u(),u(),u('MODERATOR'),u('MODERATOR');p=Post(id=str(uuid4()),author_user_id=author.id,caption='race',post_type='TEXT',visibility='PUBLIC',status='PUBLISHED',allow_comments=True,published_at=now,created_at=now,updated_at=now);db.add(p);db.commit();db.close();return S,author,reporter,m1,m2,p

def test_duplicate_report_race_one_row_and_one_creation_audit(pg):
    S,author,reporter,m1,m2,p=seed(pg);payload=SimpleNamespace(resource_type='POST',resource_id=p.id,reason='SPAM',note=None)
    def run():
        with S() as db:return moderation_service.create_report(db,db.get(User,reporter.id),payload).id
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:run(),range(2)))
    with S() as db:
        assert len(set(ids))==1
        assert db.scalar(select(text('count(*)')).select_from(ContentReport))==1
        assert len(list(db.scalars(select(ModerationAction).where(ModerationAction.action_type=='REPORT_CREATED')).all()))==1

def test_assignment_race_first_lock_wins(pg):
    S,author,reporter,m1,m2,p=seed(pg)
    with S() as db:r=moderation_service.create_report(db,db.get(User,reporter.id),SimpleNamespace(resource_type='POST',resource_id=p.id,reason='SPAM',note=None));rid=r.id
    def run(mid):
        try:
            with S() as db:moderation_service.assign_report(db,rid,db.get(User,mid));return 'ok'
        except PostError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[m1.id,m2.id]))
    assert sorted(results)==['REPORT_ALREADY_ASSIGNED','ok']

def test_hide_remove_race_removed_is_strongest_state(pg):
    S,author,reporter,m1,m2,p=seed(pg)
    def run(args):
        action,mid=args
        try:
            with S() as db:moderation_service.moderate_post(db,p.id,db.get(User,mid),action);return 'ok'
        except PostError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,[('hide',m1.id),('remove',m2.id)]))
    with S() as db:
        assert db.get(Post,p.id).status=='REMOVED'
        actions=list(db.scalars(select(ModerationAction.action_type).where(ModerationAction.target_id==p.id)).all())
        assert actions[-1]=='POST_REMOVED'

def test_duplicate_and_distinct_restriction_scope_concurrency(pg):
    S,target,reporter,m1,m2,p=seed(pg)
    def apply(scope,mid):
        payload=SimpleNamespace(restriction_type=scope,starts_at=None,ends_at=None,reason='race',source_report_id=None)
        try:
            with S() as db:moderation_service.create_restriction(db,target.id,db.get(User,mid),payload);return 'ok'
        except (PostError,Exception) as exc:return getattr(exc,'code',type(exc).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda mid:apply('COMMENTING',mid),[m1.id,m2.id]))
    assert results.count('ok')==1
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda pair:apply(*pair),[('REACTING',m1.id),('SHARING',m2.id)]))
    assert results==['ok','ok']
    with S() as db:assert len(list(db.scalars(select(UserRestriction).where(UserRestriction.user_id==target.id,UserRestriction.status=='ACTIVE')).all()))==3
