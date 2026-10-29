"""Real PostgreSQL constraints, types, and concurrency for Phase 11B."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.postgres_integration, pytest.mark.social_interactions]


@pytest.fixture(scope="module")
def pg11b():
    url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url: pytest.skip("SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0016_phase12a_1_session_security"
    yield engine; engine.dispose()


def _user_and_post(engine, visibility="PUBLIC"):
    user, post, now = str(uuid4()), str(uuid4()), "2026-08-14T00:00:00+00:00"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,password_changed_at,created_at,updated_at) VALUES(:u,:e,:e,'x','User','ACTIVE',:n,:n,:n)"), {"u":user,"e":f"{user}@11b.test","n":now})
        conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,published_at,created_at,updated_at) VALUES(:p,:u,'note','TEXT',:v,'PUBLISHED',true,:n,:n,:n)"), {"p":post,"u":user,"v":visibility,"n":now})
    return user, post, now


def _cleanup(engine, user, post):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM posts WHERE shared_post_id=:p"), {"p":post})
        conn.execute(text("DELETE FROM posts WHERE id=:p"), {"p":post})
        conn.execute(text("DELETE FROM users WHERE id=:u"), {"u":user})


def test_phase11b_schema_constraints_indexes_and_timestamptz(pg11b):
    inspector=inspect(pg11b)
    assert {"post_reactions","post_comments","saved_posts"} <= set(inspector.get_table_names())
    assert {i["name"] for i in inspector.get_indexes("post_comments")} >= {"ix_post_comments_post_created","ix_post_comments_parent_created","ix_post_comments_author_created"}
    assert {i["name"] for i in inspector.get_indexes("posts")} >= {"ix_posts_shared_post_id"}
    assert {c["name"] for c in inspector.get_check_constraints("posts")} >= {"ck_posts_not_self_shared","ck_posts_nonnegative_interaction_counts"}
    assert {c["name"] for c in inspector.get_unique_constraints("post_reactions")} >= {"uq_post_reaction_user"}
    assert {c["name"] for c in inspector.get_unique_constraints("saved_posts")} >= {"uq_saved_post_user"}
    columns={column["name"]:column for column in inspector.get_columns("post_comments")}
    assert columns["created_at"]["type"].timezone is True


def test_unique_interactions_and_nonnegative_counter_constraints(pg11b):
    user,post,now=_user_and_post(pg11b)
    try:
        with pg11b.begin() as conn:
            conn.execute(text("INSERT INTO post_reactions(id,post_id,user_id,reaction_type,created_at,updated_at) VALUES(:id,:p,:u,'LIKE',:n,:n)"),{"id":str(uuid4()),"p":post,"u":user,"n":now})
            conn.execute(text("INSERT INTO saved_posts(id,user_id,post_id,created_at) VALUES(:id,:u,:p,:n)"),{"id":str(uuid4()),"p":post,"u":user,"n":now})
        for table, sql in (("reaction","INSERT INTO post_reactions(id,post_id,user_id,reaction_type,created_at,updated_at) VALUES(:id,:p,:u,'HELPFUL',:n,:n)"),("save","INSERT INTO saved_posts(id,user_id,post_id,created_at) VALUES(:id,:u,:p,:n)")):
            with pytest.raises(IntegrityError):
                with pg11b.begin() as conn: conn.execute(text(sql),{"id":str(uuid4()),"p":post,"u":user,"n":now})
        with pytest.raises(IntegrityError):
            with pg11b.begin() as conn: conn.execute(text("UPDATE posts SET reaction_count=-1 WHERE id=:p"),{"p":post})
        with pytest.raises(IntegrityError):
            with pg11b.begin() as conn: conn.execute(text("UPDATE posts SET shared_post_id=id WHERE id=:p"),{"p":post})
    finally:_cleanup(pg11b,user,post)


@pytest.mark.concurrency
def test_concurrent_reaction_is_unique_and_counter_atomic(pg11b):
    user,post,now=_user_and_post(pg11b)
    def react(_):
        with pg11b.begin() as conn:
            inserted=conn.execute(text("INSERT INTO post_reactions(id,post_id,user_id,reaction_type,created_at,updated_at) VALUES(:id,:p,:u,'HELPFUL',:n,:n) ON CONFLICT(post_id,user_id) DO NOTHING RETURNING id"),{"id":str(uuid4()),"p":post,"u":user,"n":now}).scalar_one_or_none()
            if inserted: conn.execute(text("UPDATE posts SET reaction_count=reaction_count+1 WHERE id=:p"),{"p":post})
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(react,range(2)))
        with pg11b.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM post_reactions WHERE post_id=:p"),{"p":post}).scalar_one()==1
            assert conn.execute(text("SELECT reaction_count FROM posts WHERE id=:p"),{"p":post}).scalar_one()==1
    finally:_cleanup(pg11b,user,post)
