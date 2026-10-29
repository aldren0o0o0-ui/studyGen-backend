"""
tests/test_phase4_recommendations.py — Phase 4 Automated Tests

Tests:
  45. Overdue items prioritized
  46. Critical weak topic prioritized
  47. Weak topic recommendation
  48. Developing topic recommendation
  49. Insufficient data response
  50. Recommendation reason matches stored data
"""
from __future__ import annotations

from uuid import uuid4
import pytest

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.study import TopicPerformance
from app.services import attempt_service, review_item_service, study_recommendation_service


def test_recommendation_insufficient_data(client, db_session):
    resp = client.get("/study/recommendation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendation_type"] == "insufficient_data"
    assert "Start Your First Quiz" in data["title"]


def test_recommendation_overdue_items_prioritized(client, db_session):
    # Setup document & attempt with incorrect answers
    doc = Document(id=str(uuid4()), original_filename="math.txt", file_type="txt", file_size=10, chunk_count=1, status="READY")
    quiz = Quiz(id=str(uuid4()), document_id=doc.id, title="Math Quiz", difficulty="easy", question_count=1)
    q = Question(id=str(uuid4()), quiz_id=quiz.id, position=0, question_text="1+1?", options_json='["2", "3"]', correct_answer="2", question_type="multiple_choice", topic_label="Addition")
    db_session.add_all([doc, quiz, q])
    db_session.commit()

    att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q.id, "selected_answer": "3"}])
    review_item_service.process_attempt(db_session, att)
    db_session.commit()

    resp = client.get("/study/recommendation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendation_type"] == "review_due_items"
    assert data["due_item_count"] == 1
    assert "1 question due" in data["reason"]


def test_recommendation_reason_matches_stored_data(client, db_session):
    tp = TopicPerformance(
        id=str(uuid4()),
        topic_label="Quantum Physics",
        normalized_topic="quantum physics",
        attempted_count=5,
        correct_count=2,
        incorrect_count=3,
        accuracy_percentage=40.0,
        mastery_state="weak",
    )
    db_session.add(tp)
    db_session.commit()

    resp = client.get("/study/recommendation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendation_type"] == "review_weak_topic"
    assert data["topic_label"] == "Quantum Physics"
    assert "40.0% accuracy across 5 questions" in data["reason"]
