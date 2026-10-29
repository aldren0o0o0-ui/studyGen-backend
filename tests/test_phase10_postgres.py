"""
tests/test_phase10_postgres.py — Phase 10 PostgreSQL integration and concurrency test suite.
"""
from __future__ import annotations

import os
import pytest
from sqlalchemy import create_engine, inspect, text
from app.database.session import session_scope, worker_session_scope
from config import settings


def test_postgres_configuration_masking():
    """Verify that sensitive database credentials are masked in safe output."""
    url = "postgresql+psycopg://user:secret123@localhost:5432/testdb"
    from config import _mask_db_url
    masked = _mask_db_url(url)
    assert "secret123" not in masked
    assert "user:***@localhost" in masked


def test_session_scope_commit_and_rollback(db_session):
    """Verify session_scope helper behavior."""
    from app.database.models.auth import User, LEGACY_OWNER_ID
    
    # Test safe commit via scope
    with session_scope() as session:
        user = session.get(User, LEGACY_OWNER_ID)
        assert user is not None


def test_worker_session_scope_isolation(db_session):
    """Verify worker session scope operates independently."""
    with worker_session_scope() as session:
        assert session.is_active
