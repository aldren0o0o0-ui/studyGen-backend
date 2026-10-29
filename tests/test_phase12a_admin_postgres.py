import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,func,select,text
from sqlalchemy.orm import sessionmaker
from app.database.models.auth import User
from app.database.models.moderation import ModerationAction
from app.database.types import utc_now
from app.services import admin_service,moderation_service
from app.services.post_service import PostError
pytestmark=[pytest.mark.admin,pytest.mark.postgres_integration,pytest.mark.concurrency,pytest.mark.security]
@pytest.fixture
def pg():
    url=os.getenv('TEST_POSTGRES_DATABASE_URL')
    if not url:pytest.skip('SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable')
    engine=create_engine(url,pool_pre_ping=True);Session=sessionmaker(engine,expire_on_commit=False)
    with engine.begin() as c:
        names=[r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'"))]
        if names:c.exec_driver_sql('TRUNCATE '+','.join('"'+n+'"' for n in names)+' CASCADE')
    yield Session;engine.dispose()
def add(Session,role='USER'):
    with Session() as db:
        uid=str(uuid4());now=utc_now();row=User(id=uid,email=uid+'@adminrace.test',normalized_email=uid+'@adminrace.test',password_hash='x',display_name='Race',username=uid[:12],normalized_username=uid[:12],status='ACTIVE',role=role,password_changed_at=now,created_at=now,updated_at=now);db.add(row);db.commit();return row
def test_concurrent_promotion_has_one_transition_and_one_audit(pg):
    first=add(pg,'ADMIN');second=add(pg,'ADMIN');target=add(pg);barrier=Barrier(2)
    def run(actor):
        with pg() as db:
            barrier.wait()
            try:admin_service.change_moderator_role(db,target.id,actor,promote=True,reason='race');return 'ok'
            except PostError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[first,second]))
    assert sorted(results)==['INVALID_ROLE_TRANSITION','ok']
    with pg() as db:
        assert db.get(User,target.id).role=='MODERATOR'
        assert db.scalar(select(func.count(ModerationAction.id)).where(ModerationAction.target_user_id==target.id,ModerationAction.action_type=='USER_PROMOTED_TO_MODERATOR'))==1
def test_promotion_and_deactivation_serialize_without_authority_leak(pg):
    promoter=add(pg,'ADMIN');deactivator=add(pg,'ADMIN');target=add(pg);barrier=Barrier(2)
    def promote():
        with pg() as db:
            barrier.wait()
            try:admin_service.change_moderator_role(db,target.id,promoter,promote=True,reason='race');return 'promoted'
            except PostError as exc:return exc.code
    def deactivate():
        with pg() as db:barrier.wait();moderation_service.set_user_active(db,target.id,deactivator,False,'race');return 'deactivated'
    with ThreadPoolExecutor(max_workers=2) as pool:a=pool.submit(promote);b=pool.submit(deactivate);results={a.result(),b.result()}
    assert 'deactivated' in results
    with pg() as db:
        row=db.get(User,target.id);assert row.status=='DEACTIVATED' and row.role in {'USER','MODERATOR'}
