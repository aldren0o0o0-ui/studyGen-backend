import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,func,select,text
from sqlalchemy.orm import sessionmaker
from app.commands.create_admin import BootstrapError,create_admin
from app.database.models.auth import RefreshToken,User
from app.database.models.moderation import ModerationAction
from app.database.types import utc_now
from app.services import admin_service,auth_service,moderation_service
from app.services.password_service import hash_password
from app.services.post_service import PostError
from app.services.token_service import TokenError,decode_access_token,rotate_refresh_token
from config import settings

pytestmark=[pytest.mark.admin,pytest.mark.security,pytest.mark.postgres_integration,pytest.mark.concurrency]
PASSWORD='Postgres-security-password-123!'

@pytest.fixture
def pg():
    url=os.getenv('TEST_POSTGRES_DATABASE_URL')
    if not url or 'test' not in url.lower():pytest.skip('Disposable TEST_POSTGRES_DATABASE_URL required')
    engine=create_engine(url,pool_size=10,pool_pre_ping=True);Session=sessionmaker(engine,expire_on_commit=False)
    with engine.begin() as c:
        assert c.execute(text('select version_num from alembic_version')).scalar_one()=='0016_phase12a_1_session_security'
        names=[r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public' and tablename<>'alembic_version'"))]
        if names:c.exec_driver_sql('TRUNCATE '+','.join('"'+n+'"' for n in names)+' CASCADE')
    yield Session;engine.dispose()

def add(Session,role='USER',status='ACTIVE'):
    with Session() as db:
        uid=str(uuid4());now=utc_now();row=User(id=uid,email=uid+'@p0.test',normalized_email=uid+'@p0.test',password_hash=hash_password(PASSWORD),display_name='P0',username=uid[:12],normalized_username=uid[:12],status=status,role=role,password_changed_at=now,created_at=now,updated_at=now);db.add(row);db.commit();return row

def login_payload(row):return SimpleNamespace(email=row.email,password=PASSWORD,device_name=None,device_identifier=None)

def test_login_first_then_deactivate_revokes_raced_session_permanently(pg,monkeypatch):
    admin=add(pg,'ADMIN');target=add(pg);entered=Event();release=Event();original=auth_service._tokens;result={}
    def paused(*args,**kwargs):entered.set();assert release.wait(10);return original(*args,**kwargs)
    monkeypatch.setattr(auth_service,'_tokens',paused)
    def login():
        with pg() as db:
            _,result['access'],result['refresh']=auth_service.login(db,login_payload(target),settings)
    def deactivate():
        with pg() as db:moderation_service.set_user_active(db,target.id,db.get(User,admin.id),False,'race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        lf=pool.submit(login);assert entered.wait(10);df=pool.submit(deactivate);release.set();lf.result();df.result()
    with pg() as db:
        row=db.get(User,target.id);assert row.status=='DEACTIVATED';assert decode_access_token(result['access'],settings)['sv']<row.session_version
        assert db.scalar(select(func.count(RefreshToken.id)).where(RefreshToken.user_id==target.id,RefreshToken.revoked_at.is_(None)))==0
        moderation_service.set_user_active(db,target.id,db.get(User,admin.id),True,'restore')
        with pytest.raises(TokenError):rotate_refresh_token(db,result['refresh'],settings)

def test_deactivation_lock_first_makes_waiting_login_fail_without_token(pg):
    target=add(pg);locked=Event();release=Event();result={}
    def deactivate_holding_lock():
        with pg() as db:
            row=db.scalar(select(User).where(User.id==target.id).with_for_update(of=User));row.status='DEACTIVATED';row.session_version+=1;locked.set();assert release.wait(10);db.commit()
    def login():
        try:
            with pg() as db:auth_service.login(db,login_payload(target),settings);result['value']='issued'
        except auth_service.AuthError:result['value']='rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        df=pool.submit(deactivate_holding_lock);assert locked.wait(10);lf=pool.submit(login);release.set();df.result();lf.result()
    assert result['value']=='rejected'
    with pg() as db:assert db.scalar(select(func.count(RefreshToken.id)).where(RefreshToken.user_id==target.id))==0

def test_concurrent_bootstrap_and_demotion_are_single_logical_transitions(pg):
    barrier=Barrier(2)
    def bootstrap():
        with pg() as db:
            barrier.wait()
            try:create_admin(db,email='only-admin@bootstrap.test',username='only_admin',display_name='Only',password=PASSWORD);return 'ok'
            except BootstrapError:return 'duplicate'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:bootstrap(),range(2)))==['duplicate','ok']
    first=add(pg,'ADMIN');second=add(pg,'ADMIN');target=add(pg,'MODERATOR');barrier=Barrier(2)
    def demote(actor):
        with pg() as db:
            barrier.wait()
            try:admin_service.change_moderator_role(db,target.id,db.get(User,actor.id),promote=False,reason='race');return 'ok'
            except PostError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(demote,[first,second]))==['INVALID_ROLE_TRANSITION','ok']
    with pg() as db:assert db.scalar(select(func.count(ModerationAction.id)).where(ModerationAction.target_user_id==target.id,ModerationAction.action_type=='USER_DEMOTED_FROM_MODERATOR'))==1
