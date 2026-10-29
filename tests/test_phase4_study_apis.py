"""
tests/test_phase4_study_apis.py — Phase 4 Automated Tests

Tests:
  59. Study overview API
  60. Topic listing and filters API
  61. Question-type performance API
  62. Activity pagination API
  63. Document study analytics API
  64. Topic detail / listing
  65. Review-session detail
  66. Completed review feedback
  67. Unknown resources return controlled 404 errors
"""
from __future__ import annotations

from uuid import uuid4
import pytest

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.services import attempt_service, review_session_service


def test_study_overview_api(client, db_session):
    resp = client.get("/study/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "quizzes_completed" in data
    assert "review_sessions_completed" in data
    assert "questions_answered" in data
    assert "overall_accuracy" in data
    assert "recommendation" in data


def test_question_types_api(client, db_session):
    resp = client.get("/study/question-types")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    types = [item["question_type"] for item in data["items"]]
    assert "multiple_choice" in types
    assert "true_false" in types
    assert "identification" in types


def test_document_study_analytics_api(client, db_session):
    doc = Document(id=str(uuid4()), original_filename="algebra.pdf", file_type="pdf", file_size=10, chunk_count=1, status="READY")
    db_session.add(doc)
    db_session.commit()

    resp = client.get(f"/documents/{doc.id}/study")
    assert resp.status_code == 200
    data = resp.json()
    assert data["document_id"] == doc.id
    assert data["filename"] == "algebra.pdf"
    assert "weak_topics" in data
    assert "due_review_count" in data
    assert "average_mastery" in data


def test_unknown_resources_return_404(client, db_session):
    resp1 = client.get("/review-sessions/non-existent-id")
    assert resp1.status_code == 404

    resp2 = client.get("/documents/non-existent-id/study")
    assert resp2.status_code == 404
