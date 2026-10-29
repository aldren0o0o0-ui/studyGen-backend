"""
test_database.py
"""
import pytest
from sqlalchemy import text
from app.database.session import engine


def test_database_connection():
    """Verify we can connect to the test database and it has the right tables."""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table';")
        ).fetchall()
        tables = {row[0] for row in result}

        assert "documents" in tables
        assert "quizzes" in tables
        assert "questions" in tables
        assert "quiz_attempts" in tables
        assert "attempt_answers" in tables
