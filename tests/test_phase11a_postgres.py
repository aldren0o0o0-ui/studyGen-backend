"""Real PostgreSQL Phase 11A schema and concurrency checks."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.postgres_integration, pytest.mark.social_foundation]


@pytest.fixture(scope="module")
def pg():
    url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url: pytest.skip("SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable")
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql": pytest.fail("Phase 11A PostgreSQL tests require PostgreSQL")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0016_phase12a_1_session_security"
    yield engine; engine.dispose()


def test_phase11a_schema_types_constraints_and_indexes(pg):
    inspector = inspect(pg)
    assert {"posts", "post_allowed_users", "post_media"} <= set(inspector.get_table_names())
    assert {i["name"] for i in inspector.get_indexes("posts")} >= {"ix_posts_feed", "ix_posts_author_published", "ix_posts_visibility_status_published"}
    columns = {c["name"]: c for c in inspector.get_columns("posts")}
    assert columns["published_at"]["type"].timezone is True
    assert {c["name"] for c in inspector.get_check_constraints("posts")} >= {"ck_posts_visibility", "ck_posts_status", "ck_posts_type"}


def test_unique_username_and_recipient_constraints(pg):
    first, second, post = str(uuid4()), str(uuid4()), str(uuid4())
    normalized_username = f"user_{uuid4().hex[:16]}"
    now = "2026-08-14T00:00:00+00:00"
    try:
        with pg.begin() as conn:
            for uid, email in ((first, f"{first}@social.test"), (second, f"{second}@social.test")):
                conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,password_changed_at,created_at,updated_at) VALUES(:id,:e,:e,'x','User','ACTIVE',:n,:n,:n)"), {"id": uid, "e": email, "n": now})
            conn.execute(text("UPDATE users SET username=:u, normalized_username=:u WHERE id=:id"), {"id": first, "u": normalized_username})
            conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,created_at,updated_at) VALUES(:p,:u,'note','TEXT','SPECIFIC_USERS','DRAFT',true,:n,:n)"), {"p": post, "u": first, "n": now})
            conn.execute(text("INSERT INTO post_allowed_users(id,post_id,allowed_user_id,created_at) VALUES(:id,:p,:u,:n)"), {"id": str(uuid4()), "p": post, "u": second, "n": now})

        with pytest.raises(IntegrityError):
            with pg.begin() as conn:
                conn.execute(text("UPDATE users SET username=:u, normalized_username=:u WHERE id=:id"), {"id": second, "u": normalized_username})

        with pytest.raises(IntegrityError):
            with pg.begin() as conn:
                conn.execute(text("INSERT INTO post_allowed_users(id,post_id,allowed_user_id,created_at) VALUES(:id,:p,:u,:n)"), {"id": str(uuid4()), "p": post, "u": second, "n": now})
    finally:
        with pg.begin() as conn:
            conn.execute(text("DELETE FROM posts WHERE id=:post"), {"post": post})
            conn.execute(text("DELETE FROM users WHERE id IN (:first, :second)"), {"first": first, "second": second})


@pytest.mark.concurrency
def test_concurrent_post_creation(pg):
    user_id = str(uuid4()); now = "2026-08-14T00:00:00+00:00"
    with pg.begin() as conn:
        conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,password_changed_at,created_at,updated_at) VALUES(:id,:e,:e,'x','User','ACTIVE',:n,:n,:n)"),{"id":user_id,"e":f"{user_id}@social.test","n":now})
    def create(_):
        with pg.begin() as conn:
            pid=str(uuid4()); conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,created_at,updated_at) VALUES(:p,:u,'note','TEXT','PUBLIC','DRAFT',true,:n,:n)"),{"p":pid,"u":user_id,"n":now}); return pid
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(create, range(2)))
        assert len(set(ids)) == 2
    finally:
        with pg.begin() as conn:
            conn.execute(text("DELETE FROM posts WHERE author_user_id=:user_id"), {"user_id": user_id})
            conn.execute(text("DELETE FROM users WHERE id=:user_id"), {"user_id": user_id})
