"""
conftest.py — shared pytest fixtures for backend tests.
Gemini is mocked — no live API calls are made.

IMPORTANT: DATABASE_URL must be set in the environment BEFORE any SQLAlchemy
module is imported.  The env-var is set here at module level (top of file),
which is the very first code that runs when pytest collects this conftest.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# 1. Set DATABASE_URL before ANY other import that touches SQLAlchemy
# --------------------------------------------------------------------------
import os

os.environ["TESTING"] = "true"
os.environ["ENABLE_LEGACY_STUDY_MODULES"] = "false"
_TEST_DB = "sqlite:///:memory:"
os.environ["DATABASE_URL"] = _TEST_DB

# --------------------------------------------------------------------------
# 2. Normal imports (all happen after env-var is set)
# --------------------------------------------------------------------------
import io
import sys
import json
import uuid
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Make sure the backend root is importable before importing local modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID, User
from app.database.session import engine, SessionLocal
from app.services.token_service import create_access_token, utcnow
from config import settings

# Import ALL models so Base.metadata includes Phase 4 tables.
# This must happen before setup_test_db calls create_all(), and before
# the client fixture imports main.py (which triggers alembic migrations).
import app.database.models  # noqa: F401 — registers all models via __init__.py


# ---------------------------------------------------------------------------
# Minimal in-memory ChromaDB collection stub
# ---------------------------------------------------------------------------

class _InMemoryCollection:
    """
    Lightweight in-memory substitute for chromadb.Collection.
    Supports add, get, delete with basic where-filtering.
    """

    def __init__(self):
        self._store: dict[str, dict] = {}  # id → {document, metadata}

    def add(self, documents, ids, metadatas):
        for doc, id_, meta in zip(documents, ids, metadatas):
            owned_meta = dict(meta)
            owned_meta.setdefault("user_id", LEGACY_OWNER_ID)
            self._store[id_] = {"document": doc, "metadata": owned_meta}

    def _matches(self, metadata, where):
        if not where:
            return True
        if "$and" in where:
            return all(self._matches(metadata, part) for part in where["$and"])
        return all(
            self._eval_condition(metadata.get(key), condition)
            for key, condition in where.items()
        )

    def get(self, where=None, include=None):
        results = {"ids": [], "documents": [], "metadatas": []}
        for id_, item in self._store.items():
            meta = item["metadata"]
            if not self._matches(meta, where):
                continue
            results["ids"].append(id_)
            results["documents"].append(item["document"])
            results["metadatas"].append(meta)
        return results

    def delete(self, ids):
        for id_ in ids:
            self._store.pop(id_, None)

    def update(self, ids, metadatas):
        for id_, metadata in zip(ids, metadatas):
            if id_ in self._store:
                self._store[id_]["metadata"] = dict(metadata)

    def query(self, query_texts, n_results=3, where=None, include=None):
        # Simple stub — returns first n items (not semantic)
        items = [
            item for item in self._store.values()
            if self._matches(item["metadata"], where)
        ][:n_results]
        return {
            "documents": [[i["document"] for i in items]],
            "metadatas": [[i["metadata"] for i in items]],
            "distances": [[0.1] * len(items)],
        }

    def _eval_condition(self, value, condition):
        if isinstance(condition, dict):
            if "$eq" in condition:
                return value == condition["$eq"]
        return value == condition


# ---------------------------------------------------------------------------
# Mock Gemini response builder
# ---------------------------------------------------------------------------

def make_gemini_response(questions: list) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps({
        "quiz_title": "Test Quiz",
        "questions": questions,
    })
    return resp


SAMPLE_QUESTIONS = [
    {
        "question_type": "multiple_choice",
        "question": "What is the capital of France?",
        "options": ["Paris", "London", "Berlin", "Rome"],
        "correct_answer": "Paris",
        "explanation": "Paris is the capital of France.",
        "source_labels": ["SOURCE_1"],
    },
    {
        "question_type": "multiple_choice",
        "question": "What is 2 + 2?",
        "options": ["3", "4", "5", "6"],
        "correct_answer": "4",
        "explanation": "2 + 2 equals 4.",
        "source_labels": ["SOURCE_1"],
    },
    {
        "question_type": "multiple_choice",
        "question": "What color is the sky?",
        "options": ["Blue", "Green", "Red", "Yellow"],
        "correct_answer": "Blue",
        "explanation": "The sky appears blue due to Rayleigh scattering.",
        "source_labels": ["SOURCE_1"],
    },
]


# ---------------------------------------------------------------------------
# Database setup/teardown (session-scoped so tables are created once)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Create all tables in the test database once per session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_db(setup_test_db):
    """Ensure each test runs against an empty database by deleting all rows after each test."""
    session = SessionLocal()
    now = utcnow()
    session.add(User(
        id=LEGACY_OWNER_ID,
        email="test@example.com",
        normalized_email="test@example.com",
        password_hash="test-only-unused",
        display_name="Test User",
        status="ACTIVE",
        password_changed_at=now,
        created_at=now,
        updated_at=now,
    ))
    session.commit()
    session.close()
    yield
    session = SessionLocal()
    try:
        # Break Phase 11B self-references before deterministic table cleanup.
        from app.database.models.social import Post, PostComment
        session.query(PostComment).update({PostComment.parent_comment_id: None})
        session.query(Post).update({Post.shared_post_id: None})
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


@pytest.fixture
def db_session():
    """Yield a clean database session and rollback after each test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# App fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_collection():
    return _InMemoryCollection()


@pytest.fixture
def mock_gemini():
    model = MagicMock()
    model.generate_content.return_value = make_gemini_response(SAMPLE_QUESTIONS)
    model.model_name = "gemini-test-mock"
    return model


@pytest.fixture
def client(mock_collection, mock_gemini):
    """
    TestClient with mocked ChromaDB, Gemini, and in-memory SQLite.

    We patch chromadb and google.generativeai at module level so that main.py
    startup code does not reach out to real services.  The DATABASE_URL env var
    is already set above so the SQLAlchemy engine targets the shared in-memory
    DB — no reload is needed.
    """
    with patch("chromadb.PersistentClient") as mock_chroma_client, \
         patch("chromadb.utils.embedding_functions.DefaultEmbeddingFunction"), \
         patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel", return_value=mock_gemini):

        mock_chroma_client.return_value.get_or_create_collection.return_value = mock_collection

        # Import the app fresh (no reload — env var is already in place)
        from main import app as fastapi_app

        # Directly override state so routes use mock objects
        fastapi_app.state.collection = mock_collection
        fastapi_app.state.gemini_model = mock_gemini
        fastapi_app.state.gemini_available = True
        fastapi_app.state.gemini_unavailable_reason = ""

        session = SessionLocal()
        test_user = session.get(User, LEGACY_OWNER_ID)
        access_token = create_access_token(test_user, settings)
        session.close()

        with TestClient(fastapi_app, raise_server_exceptions=True) as c:
            c.headers.update({"Authorization": f"Bearer {access_token}"})
            yield c


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def make_pdf_bytes() -> bytes:
    """Create a minimal valid single-page PDF in memory."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is a test document about biology and cells.")
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_txt_bytes(content: str = "Hello. This is a test document.") -> bytes:
    return content.encode("utf-8")


def make_docx_bytes(content: str = "Hello. This is a DOCX test document.") -> bytes:
    from docx import Document
    doc = Document()
    doc.add_paragraph(content)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# Re-export GenerateQuizRequest so test files can import from conftest
from app.schemas.quizzes import GenerateQuizRequest  # noqa: E402, F401
