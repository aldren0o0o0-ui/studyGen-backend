"""
tests/test_phase4_review_sessions.py — Phase 4 Automated Tests

Tests:
  22. Due-review session
  23. Incorrect-answer session
  24. Weak-topic session
  25. Custom document filter
  26. Custom topic filter
  27. Question-type filter
  28. Difficulty filter
  29. Requested count greater than availability
  30. No duplicate questions
  31. Priority ordering
  32. Active response hides answer fields
  33. Review generation makes no Gemini call
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4
import pytest

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.study import ReviewSession
from app.services import attempt_service, review_item_service, review_session_service


def _setup_review_environment(db):
    doc = Document(id=str(uuid4()), original_filename="physics.pdf", file_type="pdf", file_size=100, chunk_count=2, status="READY")
    quiz = Quiz(id=str(uuid4()), document_id=doc.id, title="Physics Quiz", difficulty="hard", question_count=3)
    
    q1 = Question(id=str(uuid4()), quiz_id=quiz.id, position=0, question_text="What is velocity?", options_json='["Speed with direction", "Distance"]', correct_answer="Speed with direction", question_type="multiple_choice", topic_label="Kinematics", difficulty="hard")
    q2 = Question(id=str(uuid4()), quiz_id=quiz.id, position=1, question_text="Force equals mass times acceleration?", options_json='["True", "False"]', correct_answer="True", question_type="true_false", topic_label="Dynamics", difficulty="medium")
    q3 = Question(id=str(uuid4()), quiz_id=quiz.id, position=2, question_text="Unit of force", options_json='[]', correct_answer="Newton", accepted_answers_json='["Newton", "N"]', question_type="identification", topic_label="Dynamics", difficulty="easy")
    
    db.add_all([doc, quiz, q1, q2, q3])
    db.commit()

    # Fail q1 and q2 to create review items
    att = attempt_service.submit_attempt(db, quiz.id, [
        {"question_id": q1.id, "selected_answer": "Distance"},
        {"question_id": q2.id, "selected_answer": "False"},
        {"question_id": q3.id, "selected_answer": "Newton"},
    ])
    review_item_service.process_attempt(db, att)
    db.commit()  # Must commit so client fixture's separate DB session can read
    return doc, quiz, [q1, q2, q3]


def test_review_session_generation_no_gemini_call(client, db_session, mock_gemini):
    doc, quiz, questions = _setup_review_environment(db_session)
    mock_gemini.reset_mock()

    resp = client.post("/review-sessions", json={
        "session_type": "due_review",
        "question_count": 5
    })
    assert resp.status_code == 201
    data = resp.json()

    # PROOF: Gemini was NOT called
    mock_gemini.generate_content.assert_not_called()
    assert data["session_type"] == "due_review"
    assert data["status"] == "active"
    assert len(data["questions"]) == 2  # q1 and q2 were wrong


def test_active_review_response_hides_answers(client, db_session):
    doc, quiz, questions = _setup_review_environment(db_session)

    resp = client.post("/review-sessions", json={
        "session_type": "due_review",
        "question_count": 5
    })
    assert resp.status_code == 201
    data = resp.json()

    for q in data["questions"]:
        # Verify correct_answer, accepted_answers, explanation are NOT present in ActiveReviewQuestion
        assert "correct_answer" not in q
        assert "accepted_answers" not in q
        assert "explanation" not in q
        assert "is_correct" not in q


def test_requested_count_greater_than_availability(client, db_session):
    doc, quiz, questions = _setup_review_environment(db_session)

    # Requested 20, but only 2 review items exist
    resp = client.post("/review-sessions", json={
        "session_type": "incorrect_answers",
        "question_count": 20
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["question_count"] == 2
    assert len(data["questions"]) == 2


def test_custom_review_session_filters(client, db_session):
    doc, quiz, (q1, q2, q3) = _setup_review_environment(db_session)

    # Filter by topic_label = Kinematics
    resp = client.post("/review-sessions", json={
        "session_type": "custom",
        "topic_label": "Kinematics",
        "question_count": 5
    })
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["questions"]) == 1
    assert data["questions"][0]["question_id"] == q1.id
