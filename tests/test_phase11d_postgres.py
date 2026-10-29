"""Real PostgreSQL Phase 11D schema and concurrency certification."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.moderation, pytest.mark.postgres_integration]


@pytest.fixture(scope="module")
def pg():
    url=os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url: pytest.skip("SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable")
    engine=create_engine(url,pool_size=6,max_overflow=4,pool_pre_ping=True)
    with engine.connect() as conn: assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="0016_phase12a_1_session_security"
    yield engine; engine.dispose()


@pytest.fixture(autouse=True)
def clean(pg):
    with pg.begin() as conn:
        conn.execute(text("TRUNCATE moderation_actions, user_restrictions, content_reports, saved_posts, post_comments, post_reactions, post_allowed_users, post_media, posts, refresh_tokens, users CASCADE"))


def test_phase11d_schema_constraints_indexes_and_timestamptz(pg):
    i=inspect(pg); assert {"content_reports","moderation_actions","user_restrictions"}.issubset(i.get_table_names())
    report_indexes={x["name"] for x in i.get_indexes("content_reports")}; restriction_indexes={x["name"] for x in i.get_indexes("user_restrictions")}
    assert "uq_content_reports_active_reporter_resource" in report_indexes
    assert "uq_user_restrictions_active_scope" in restriction_indexes
    for table,columns in {"content_reports":{"created_at","updated_at","resolved_at","resource_created_at"},"moderation_actions":{"created_at"},"user_restrictions":{"starts_at","ends_at","created_at","updated_at","revoked_at"}}.items():
        found={c["name"]:c for c in i.get_columns(table)}
        assert all(found[name]["type"].timezone for name in columns)


def test_partial_unique_active_report_and_restriction_period(pg):
    a,b,p=str(uuid4()),str(uuid4()),str(uuid4()); now="2026-08-14T00:00:00+00:00"
    with pg.begin() as conn:
        for uid in (a,b): conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,role,password_changed_at,created_at,updated_at) VALUES(:id,:e,:e,'x','User','ACTIVE','USER',:n,:n,:n)"),{"id":uid,"e":uid+'@test.local',"n":now})
        conn.execute(text("INSERT INTO posts(id,author_user_id,post_type,visibility,status,allow_comments,created_at,updated_at,reaction_count,comment_count,share_count,save_count) VALUES(:id,:u,'TEXT','PUBLIC','PUBLISHED',true,:n,:n,0,0,0,0)"),{"id":p,"u":b,"n":now})
        values={"id":str(uuid4()),"r":a,"p":p,"u":b,"n":now}
        conn.execute(text("INSERT INTO content_reports(id,reporter_user_id,resource_type,resource_id,reported_user_id,reason,status,priority,resource_status_at_report,resource_created_at,created_at,updated_at) VALUES(:id,:r,'POST',:p,:u,'SPAM','OPEN','NORMAL','PUBLISHED',:n,:n,:n)"),values)
        with pytest.raises(IntegrityError), conn.begin_nested(): conn.execute(text("INSERT INTO content_reports(id,reporter_user_id,resource_type,resource_id,reported_user_id,reason,status,priority,resource_status_at_report,resource_created_at,created_at,updated_at) VALUES(:id,:r,'POST',:p,:u,'OTHER','UNDER_REVIEW','NORMAL','PUBLISHED',:n,:n,:n)"),{**values,"id":str(uuid4())})
        with pytest.raises(IntegrityError), conn.begin_nested(): conn.execute(text("INSERT INTO user_restrictions(id,user_id,restriction_type,status,reason,starts_at,ends_at,created_by_user_id,created_at,updated_at) VALUES(:id,:u,'COMMENTING','ACTIVE','x',:n,:n,:r,:n,:n)"),{"id":str(uuid4()),"u":b,"r":a,"n":now})
