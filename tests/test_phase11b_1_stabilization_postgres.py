"""Phase 11B.1 real PostgreSQL races, rollback safety, and invariants."""
import os, threading, time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.database.models.auth import User
from app.database.models.social import Post, PostComment, PostReaction, SavedPost
from app.services import post_comment_service, post_reaction_service, post_share_service, saved_post_service

pytestmark=[pytest.mark.postgres_integration,pytest.mark.social_interactions,pytest.mark.concurrency]


@pytest.fixture(scope="module")
def stabilized_pg():
    url=os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url:pytest.skip("SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable")
    engine=create_engine(url,pool_size=20,max_overflow=10,pool_pre_ping=True)
    with engine.connect() as conn:assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="0016_phase12a_1_session_security"
    yield engine;engine.dispose()


def seed(engine,user_count=1,visibility="PUBLIC"):
    ids=[str(uuid4()) for _ in range(user_count)];post=str(uuid4());now="2026-08-14T00:00:00+00:00"
    with engine.begin() as conn:
        for uid in ids:conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,password_changed_at,created_at,updated_at) VALUES(:u,:e,:e,'x','User','ACTIVE',:n,:n,:n)"),{"u":uid,"e":f"{uid}@stabilize.test","n":now})
        conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,published_at,created_at,updated_at) VALUES(:p,:u,'note','TEXT',:v,'PUBLISHED',true,:n,:n,:n)"),{"p":post,"u":ids[0],"v":visibility,"n":now})
    return ids,post


def cleanup(engine,users,post):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM posts WHERE shared_post_id=:p"),{"p":post});conn.execute(text("DELETE FROM posts WHERE id=:p"),{"p":post});conn.execute(text("DELETE FROM users WHERE id=ANY(:users)"),{"users":users})


def assert_invariants(session,post_id):
    post=session.get(Post,post_id)
    assert post.reaction_count==session.scalar(select(func.count()).select_from(PostReaction).where(PostReaction.post_id==post_id))
    assert post.save_count==session.scalar(select(func.count()).select_from(SavedPost).where(SavedPost.post_id==post_id))
    assert post.comment_count==session.scalar(select(func.count()).select_from(PostComment).where(PostComment.post_id==post_id,PostComment.status=="VISIBLE"))
    assert post.share_count==session.scalar(select(func.count()).select_from(Post).where(Post.shared_post_id==post_id,Post.deleted_at.is_(None)))
    assert min(post.reaction_count,post.save_count,post.comment_count,post.share_count)>=0


def test_concurrent_duplicate_different_user_and_save_unsave(stabilized_pg):
    users,post=seed(stabilized_pg,10);Session=sessionmaker(stabilized_pg,expire_on_commit=False)
    def save(uid):
        with Session() as db:saved_post_service.save(db,post,uid)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(save,users))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:save(users[0]),range(2)))
        def unsave():
            with Session() as db:saved_post_service.unsave(db,post,users[0])
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda fn:fn(),[lambda:save(users[0]),unsave]))
        with Session() as db:assert_invariants(db,post);assert db.scalar(select(func.count()).select_from(SavedPost).where(SavedPost.post_id==post)) in {9,10}
    finally:cleanup(stabilized_pg,users,post)


def test_concurrent_comments_replies_and_create_delete(stabilized_pg):
    users,post=seed(stabilized_pg,8);Session=sessionmaker(stabilized_pg,expire_on_commit=False)
    def comment(uid):
        with Session() as db:return post_comment_service.create(db,post,uid,f"comment {uid}",2000).id
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:tops=list(pool.map(comment,users))
        def reply(args):
            uid,parent=args
            with Session() as db:return post_comment_service.create(db,post,uid,"reply",2000,parent).id
        with ThreadPoolExecutor(max_workers=7) as pool:replies=list(pool.map(reply,zip(users[1:],tops[0:1]*7)))
        def nested(uid):
            with Session() as db:return post_comment_service.create(db,post,uid,"normalized",2000,replies[0]).id
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(nested,users[2:5]))
        def remove():
            with Session() as db:post_comment_service.delete(db,tops[1],users[1])
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda fn:fn(),[lambda:comment(users[1]),remove]))
        with Session() as db:
            assert_invariants(db,post)
            roots=set(db.scalars(select(PostComment.parent_comment_id).where(PostComment.id.in_(replies))).all());assert roots=={tops[0]}
            normalized=list(db.scalars(select(PostComment).where(PostComment.content=="normalized")));assert all(row.parent_comment_id==tops[0] for row in normalized)
    finally:cleanup(stabilized_pg,users,post)


def test_reaction_change_delete_race_and_concurrent_shares(stabilized_pg):
    users,post=seed(stabilized_pg,6);Session=sessionmaker(stabilized_pg,expire_on_commit=False)
    try:
        with Session() as db:post_reaction_service.set_reaction(db,post,users[1],"LIKE")
        def change():
            with Session() as db:post_reaction_service.set_reaction(db,post,users[1],"HELPFUL")
        def remove():
            with Session() as db:post_reaction_service.remove_reaction(db,post,users[1])
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda fn:fn(),[change,remove]))
        payload=SimpleNamespace(caption="share",visibility="PRIVATE",recipient_user_ids=[],allow_comments=True)
        def share(uid):
            with Session() as db:post_share_service.share(db,post,db.get(User,uid),payload,SimpleNamespace(post_caption_max_characters=5000))
        with ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(share,users[1:]))
        with Session() as db:assert_invariants(db,post);assert db.get(Post,post).share_count==5
    finally:cleanup(stabilized_pg,users,post)


def test_privacy_revocation_serializes_before_share(stabilized_pg):
    users,post=seed(stabilized_pg,2);Session=sessionmaker(stabilized_pg,expire_on_commit=False);locked=threading.Event();errors=[]
    def revoke():
        with stabilized_pg.begin() as conn:
            conn.execute(text("SELECT id FROM posts WHERE id=:p FOR UPDATE"),{"p":post})
            conn.execute(text("UPDATE posts SET visibility='PRIVATE' WHERE id=:p"),{"p":post})
            locked.set();time.sleep(.2)
    def share():
        locked.wait()
        try:
            with Session() as db:post_share_service.share(db,post,db.get(User,users[1]),SimpleNamespace(caption=None,visibility="PRIVATE",recipient_user_ids=[],allow_comments=True),SimpleNamespace(post_caption_max_characters=5000))
        except Exception as exc:errors.append(getattr(exc,"code",type(exc).__name__))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda fn:fn(),[revoke,share]))
        assert errors==["POST_NOT_FOUND"]
        with Session() as db:assert_invariants(db,post);assert db.get(Post,post).share_count==0
    finally:cleanup(stabilized_pg,users,post)


def test_fault_injection_rolls_back_rows_counters_and_session_recovers(stabilized_pg):
    users,post=seed(stabilized_pg,2);Session=sessionmaker(stabilized_pg,expire_on_commit=False)
    operations=[lambda db:post_reaction_service.set_reaction(db,post,users[1],"LIKE"),lambda db:saved_post_service.save(db,post,users[1]),lambda db:post_comment_service.create(db,post,users[1],"rollback",2000),lambda db:post_share_service.share(db,post,db.get(User,users[1]),SimpleNamespace(caption=None,visibility="PRIVATE",recipient_user_ids=[],allow_comments=True),SimpleNamespace(post_caption_max_characters=5000))]
    try:
        for operation in operations:
            with Session() as db:
                real_commit=db.commit
                def fail_commit():db.flush();raise RuntimeError("injected")
                db.commit=fail_commit
                with pytest.raises(RuntimeError):operation(db)
                db.rollback();db.commit=real_commit
                assert db.scalar(select(func.count()).select_from(User))>=2
            with Session() as verify:assert_invariants(verify,post)
        with Session() as db:
            created=post_comment_service.create(db,post,users[1],"visible",2000)
        with Session() as db:
            real_commit=db.commit
            def fail_delete():db.flush();raise RuntimeError("injected")
            db.commit=fail_delete
            with pytest.raises(RuntimeError):post_comment_service.delete(db,created.id,users[1])
            db.rollback();db.commit=real_commit
        with Session() as db:assert db.get(PostComment,created.id).status=="VISIBLE";assert_invariants(db,post)
    finally:cleanup(stabilized_pg,users,post)
