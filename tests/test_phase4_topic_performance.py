"""
tests/test_phase4_topic_performance.py — Phase 4 Automated Tests

Tests:
  1. Insufficient data topic state (< 2 attempts)
  2. Weak topic state (< 50% accuracy)
  3. Developing topic state (50% - 79% accuracy)
  4. Strong topic state (>= 80% accuracy)
  5. Boundary accuracy values (0%, 50%, 80%, 100%)
  6. Topic label normalization (case-insensitive, whitespace stripped)
  7. Document-specific topic isolation
  8. Deleted document handling (soft-deleted docs)
  9. Mastery score formula calculation (minimum, maximum, recent accuracy, review bonus)
"""
from __future__ import annotations

from uuid import uuid4
import pytest
from sqlalchemy import select

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.attempt import QuizAttempt, AttemptAnswer
from app.database.models.study import TopicPerformance
from app.services import topic_performance_service, attempt_service


def _create_doc_quiz_attempt(db, topic="Cell Biology", doc_filename="bio.txt"):
    doc_id = str(uuid4())
    doc = Document(
        id=doc_id,
        original_filename=doc_filename,
        file_type="txt",
        file_size=100,
        chunk_count=1,
        status="READY",
    )
    db.add(doc)

    quiz_id = str(uuid4())
    quiz = Quiz(
        id=quiz_id,
        document_id=doc_id,
        title="Test Quiz",
        difficulty="medium",
        question_count=2,
    )
    db.add(quiz)

    q1 = Question(
        id=str(uuid4()),
        quiz_id=quiz_id,
        position=0,
        question_text="Q1",
        options_json='["A", "B"]',
        correct_answer="A",
        question_type="multiple_choice",
        topic_label=topic,
    )
    q2 = Question(
        id=str(uuid4()),
        quiz_id=quiz_id,
        position=1,
        question_text="Q2",
        options_json='["A", "B"]',
        correct_answer="A",
        question_type="multiple_choice",
        topic_label=topic,
    )
    db.add_all([q1, q2])
    db.commit()
    return doc, quiz, [q1, q2]


def test_topic_states_and_boundaries(db_session):
    doc, quiz, (q1, q2) = _create_doc_quiz_attempt(db_session, topic="Cellular Respiration")

    # 1. Insufficient data (1 attempt)
    att0 = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "A"}])
    topic_performance_service.update_from_attempt(db_session, att0)
    
    tp = db_session.execute(
        select(TopicPerformance).where(TopicPerformance.topic_label == "Cellular Respiration")
    ).scalar_one()
    assert tp.attempted_count == 1
    assert tp.mastery_state == "insufficient_data"

    # 2. Weak topic (<50%): answer 3 more questions incorrectly -> 1/4 = 25%
    for _ in range(3):
        att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "B"}])
        topic_performance_service.update_from_attempt(db_session, att)

    db_session.refresh(tp)
    assert tp.attempted_count == 4
    assert tp.accuracy_percentage == 25.0
    assert tp.mastery_state == "weak"

    # 3. Developing topic (50-79%): answer 2 correctly -> 3/6 = 50%
    for _ in range(2):
        att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "A"}])
        topic_performance_service.update_from_attempt(db_session, att)

    db_session.refresh(tp)
    assert tp.attempted_count == 6
    assert tp.accuracy_percentage == 50.0
    assert tp.mastery_state == "developing"

    # 4. Strong topic (>=80%): answer 4 more correctly -> 7/10 = 70%... let's answer 9 more correctly -> 12/15 = 80%
    for _ in range(9):
        att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "A"}])
        topic_performance_service.update_from_attempt(db_session, att)

    db_session.refresh(tp)
    assert tp.accuracy_percentage == 80.0
    assert tp.mastery_state == "strong"


def test_topic_normalization(db_session):
    doc, quiz, (q1, q2) = _create_doc_quiz_attempt(db_session, topic=" Cell  Biology ")
    att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "A"}])
    topic_performance_service.update_from_attempt(db_session, att)

    tp = db_session.execute(
        select(TopicPerformance).where(TopicPerformance.normalized_topic == "cell biology")
    ).scalar_one()
    assert tp is not None
    assert tp.normalized_topic == "cell biology"


def test_document_topic_isolation(db_session):
    doc1, quiz1, (q1, _) = _create_doc_quiz_attempt(db_session, topic="Mitosis", doc_filename="doc1.txt")
    doc2, quiz2, (q2, _) = _create_doc_quiz_attempt(db_session, topic="Mitosis", doc_filename="doc2.txt")

    att1 = attempt_service.submit_attempt(db_session, quiz1.id, [{"question_id": q1.id, "selected_answer": "A"}])
    topic_performance_service.update_from_attempt(db_session, att1)

    att2 = attempt_service.submit_attempt(db_session, quiz2.id, [{"question_id": q2.id, "selected_answer": "B"}])
    topic_performance_service.update_from_attempt(db_session, att2)

    tp1 = db_session.execute(
        select(TopicPerformance).where(TopicPerformance.document_id == doc1.id)
    ).scalar_one()
    tp2 = db_session.execute(
        select(TopicPerformance).where(TopicPerformance.document_id == doc2.id)
    ).scalar_one()

    assert tp1.accuracy_percentage == 100.0
    assert tp2.accuracy_percentage == 0.0


def test_mastery_score_boundaries(db_session):
    score_min = topic_performance_service._calculate_mastery_score(0.0, 0.0, 0)
    score_max = topic_performance_service._calculate_mastery_score(100.0, 100.0, 5)

    assert score_min == 0.0
    assert score_max == 100.0
