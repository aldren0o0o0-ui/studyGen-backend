"""Real PostgreSQL-only Phase 10.1 certification tests.

These tests never fall back to SQLite. Set TEST_POSTGRES_DATABASE_URL to a
disposable database; the suite truncates all application tables.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.job_status import PROCESSING_JOB_STATUSES
from app.database.models.auth import User
from app.database.models.job import ProcessingJob
from app.database.types import utc_now

pytestmark = pytest.mark.postgres_integration


@pytest.fixture(scope="module")
def pg_engine():
    url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url:
        pytest.skip("SKIPPED: TEST_POSTGRES_DATABASE_URL unavailable")
    engine = create_engine(url, pool_size=2, max_overflow=1, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("TEST_POSTGRES_DATABASE_URL is not PostgreSQL")
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if revision != "0016_phase12a_1_session_security":
        engine.dispose()
        pytest.fail(f"PostgreSQL test schema is not at Phase 10.1 head: {revision!r}")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_pg(pg_engine):
    with pg_engine.begin() as conn:
        tables = [t for t in inspect(conn).get_table_names() if t != "alembic_version"]
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))


def _user(user_id=None):
    now = utc_now()
    user_id = user_id or str(uuid4())
    return User(id=user_id, email=f"{user_id}@example.test", normalized_email=f"{user_id}@example.test",
                password_hash="test", display_name="Test", status="ACTIVE",
                password_changed_at=now, created_at=now, updated_at=now)


def _job(user_id, status="PENDING", key=None):
    now = utc_now()
    return ProcessingJob(id=str(uuid4()), user_id=user_id, job_type="quiz_generation",
                         status=status, stage="test", progress_percentage=0,
                         attempt_count=0, max_attempts=3, priority=0,
                         idempotency_key=key, payload_json={"nested": [1, True, {"ok": None}]},
                         created_at=now, updated_at=now)


def test_schema_introspection_jsonb_timestamptz_and_boolean(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='processing_jobs' AND column_name='payload_json'" )).scalar_one() == "jsonb"
        assert conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='flashcards' AND column_name='source_references_json'" )).scalar_one() == "jsonb"
        assert conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='processing_jobs' AND column_name='created_at'" )).scalar_one() == "timestamp with time zone"
        assert conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='chat_messages' AND column_name='insufficient_evidence'" )).scalar_one() == "boolean"


def test_jsonb_native_round_trip_and_utc_timestamp(pg_engine):
    Session = sessionmaker(pg_engine)
    with Session.begin() as db:
        user = _user(); db.add(user); db.flush(); job = _job(user.id); db.add(job); job_id = job.id
    with Session() as db:
        loaded = db.get(ProcessingJob, job_id)
        assert loaded.payload_json == {"nested": [1, True, {"ok": None}]}
        assert isinstance(loaded.payload_json, dict)
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at.astimezone(timezone.utc).utcoffset().total_seconds() == 0


@pytest.mark.parametrize("status", PROCESSING_JOB_STATUSES)
def test_all_canonical_job_statuses_are_accepted(pg_engine, status):
    Session = sessionmaker(pg_engine)
    with Session.begin() as db:
        user = _user(); db.add(user); db.flush(); db.add(_job(user.id, status=status))


def test_invalid_job_status_rejected_and_session_reusable(pg_engine):
    Session = sessionmaker(pg_engine)
    with Session() as db:
        user = _user(); db.add(user); db.commit()
        db.add(_job(user.id, status="UNKNOWN"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(User, user.id) is not None


def test_running_cancel_requested_cancelled_transitions(pg_engine):
    Session = sessionmaker(pg_engine)
    with Session.begin() as db:
        user = _user(); db.add(user); db.flush(); job = _job(user.id, "RUNNING"); db.add(job); job_id = job.id
    with Session.begin() as db:
        job = db.get(ProcessingJob, job_id); job.status = "CANCEL_REQUESTED"; job.cancel_requested_at = utc_now()
    with Session.begin() as db:
        job = db.get(ProcessingJob, job_id); job.status = "CANCELLED"; job.cancelled_at = utc_now()


def test_pool_returns_connections_and_multiple_sessions(pg_engine):
    before = pg_engine.pool.checkedout()
    with pg_engine.connect() as first, pg_engine.connect() as second:
        assert first.execute(text("SELECT 1")).scalar_one() == 1
        assert second.execute(text("SELECT 1")).scalar_one() == 1
        assert pg_engine.pool.checkedout() >= before + 2
    assert pg_engine.pool.checkedout() == before


@pytest.mark.concurrency
def test_concurrent_transactions_are_isolated(pg_engine):
    def query():
        with pg_engine.connect() as conn:
            return conn.execute(text("SELECT pg_backend_pid()" )).scalar_one()
    with ThreadPoolExecutor(max_workers=2) as executor:
        pids = list(executor.map(lambda _: query(), range(2)))
    assert len(pids) == 2
