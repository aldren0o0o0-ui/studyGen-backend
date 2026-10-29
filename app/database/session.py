"""
database/session.py — Engine and session factory (Phase 10: PostgreSQL-ready).

PostgreSQL:  postgresql+psycopg://user:password@host:5432/dbname
SQLite:      sqlite:///path/to/db.sqlite  (development / test only)

Use get_db() as a FastAPI dependency.
Use session_scope() for one-off script sessions.
Use worker_session_scope() in ARQ worker tasks.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import with_loader_criteria
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

# Import settings here so DATABASE_URL is resolved through config validation.
# config.py reads DATABASE_URL env var and falls back to SQLite for development.
from config import settings  # noqa: E402

_url = settings.database_url

# ────────────────────────────────────────────────────────────────────────────
# Engine creation
# ────────────────────────────────────────────────────────────────────────────

_engine_kwargs: dict = {
    "echo": settings.database_echo,
}

if settings.is_sqlite:
    _connect_args: dict = {"check_same_thread": False, "timeout": 15}
    if ":memory:" in _url:
        _engine_kwargs["poolclass"] = StaticPool
    _engine_kwargs["connect_args"] = _connect_args

elif settings.is_postgresql:
    _engine_kwargs["pool_size"] = settings.database_pool_size
    _engine_kwargs["max_overflow"] = settings.database_max_overflow
    _engine_kwargs["pool_timeout"] = settings.database_pool_timeout
    _engine_kwargs["pool_recycle"] = settings.database_pool_recycle
    _engine_kwargs["pool_pre_ping"] = settings.database_pool_pre_ping

logger.info("Initialising database engine: %s", settings.database_url_safe)

engine = create_engine(_url, **_engine_kwargs)

# ────────────────────────────────────────────────────────────────────────────
# SQLite-only pragmas (WAL + FK enforcement)
# ────────────────────────────────────────────────────────────────────────────

if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# ────────────────────────────────────────────────────────────────────────────
# PostgreSQL-only: statement / lock timeouts
# ────────────────────────────────────────────────────────────────────────────

if settings.is_postgresql:
    @event.listens_for(engine, "connect")
    def _set_pg_timeouts(dbapi_connection, connection_record):
        stmt_ms = settings.database_statement_timeout_ms
        lock_ms = settings.database_lock_timeout_ms
        if stmt_ms > 0 or lock_ms > 0:
            cursor = dbapi_connection.cursor()
            if stmt_ms > 0:
                cursor.execute(f"SET statement_timeout = {stmt_ms}")
            if lock_ms > 0:
                cursor.execute(f"SET lock_timeout = {lock_ms}")
            cursor.close()

# ────────────────────────────────────────────────────────────────────────────
# Session factory
# ────────────────────────────────────────────────────────────────────────────

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ────────────────────────────────────────────────────────────────────────────
# Ownership enforcement events (unchanged from Phase 7)
# ────────────────────────────────────────────────────────────────────────────

@event.listens_for(Session, "do_orm_execute")
def _scope_owned_selects(execute_state):
    if not execute_state.is_select:
        return
    from app.ownership_context import current_user_id
    user_id = current_user_id()
    if not user_id:
        return
    from app.database.models.attempt import QuizAttempt
    from app.database.models.chat import ChatSession
    from app.database.models.document import Document
    from app.database.models.flashcard import StudySet, FlashcardReviewSession
    from app.database.models.folder import Folder
    from app.database.models.quiz import Quiz
    from app.database.models.study import (
        ReviewItem, ReviewSession, StudyActivity, StudyGoal, TopicPerformance,
    )
    models = (
        Document, Folder, Quiz, QuizAttempt, TopicPerformance, ReviewItem, ReviewSession,
        StudyGoal, StudyActivity, StudySet, FlashcardReviewSession, ChatSession,
    )
    execute_state.statement = execute_state.statement.options(*[
        with_loader_criteria(
            model, model.user_id == user_id, include_aliases=True
        )
        for model in models
    ])


@event.listens_for(Session, "before_flush")
def _stamp_owned_records(session, flush_context, instances):
    from app.ownership_context import current_user_id
    user_id = current_user_id()
    if not user_id:
        return
    for value in session.new:
        # Moderation restrictions target another user by design; ``user_id`` is
        # the enforcement subject, not request-owned data.
        from app.database.models.moderation import UserRestriction
        if isinstance(value, UserRestriction):
            continue
        if hasattr(value, "user_id"):
            existing = getattr(value, "user_id", None)
            if existing and existing != user_id:
                raise ValueError("Cross-user ownership assignment is not allowed.")
            setattr(value, "user_id", user_id)


# ────────────────────────────────────────────────────────────────────────────
# Session scope helpers
# ────────────────────────────────────────────────────────────────────────────

@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    General-purpose transactional session scope.

    Usage::

        with session_scope() as db:
            db.add(some_object)
            # commit happens automatically on clean exit
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def worker_session_scope() -> Generator[Session, None, None]:
    """
    Session scope designed for ARQ background worker tasks.
    Opens a fresh session, commits on success, rolls back on failure,
    and always closes — regardless of outcome.

    Usage::

        async def my_arq_task(ctx: dict, job_id: str) -> str:
            with worker_session_scope() as db:
                ...  # service calls
            return "SUCCEEDED"
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
