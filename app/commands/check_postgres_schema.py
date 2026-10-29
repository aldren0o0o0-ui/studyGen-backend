"""
app/commands/check_postgres_schema.py — Validate PostgreSQL Schema Integrity & Alembic Revision
"""
from __future__ import annotations

import logging
import sys
from sqlalchemy import create_engine, inspect, text
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_TABLES = [
    "users",
    "refresh_tokens",
    "documents",
    "document_processing_runs",
    "document_pages",
    "quizzes",
    "questions",
    "quiz_attempts",
    "attempt_answers",
    "topic_performance",
    "review_items",
    "review_sessions",
    "review_session_items",
    "study_goals",
    "study_activities",
    "study_sets",
    "flashcards",
    "flashcard_review_states",
    "flashcard_review_sessions",
    "flashcard_review_session_items",
    "chat_sessions",
    "chat_session_documents",
    "chat_messages",
    "chat_citations",
    "chat_generation_records",
    "processing_jobs",
    "worker_heartbeats",
    "alembic_version",
]


def check_schema():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    
    existing_tables = set(inspector.get_table_names())
    missing_tables = [table for table in EXPECTED_TABLES if table not in existing_tables]
    
    if missing_tables:
        logger.error("Schema check FAILED: Missing tables %s", missing_tables)
        sys.exit(1)
        
    with engine.connect() as conn:
        res = conn.execute(text("SELECT version_num FROM alembic_version"))
        version = res.scalar()
        logger.info("Current Alembic Revision: %s", version)
        
    logger.info("Schema check PASSED: All 28 expected tables present in database.")


if __name__ == "__main__":
    check_schema()
