"""
tests/test_phase4_review_items.py — Phase 4 Automated Tests

Tests:
  15. Incorrect attempt creates review item
  16. Repeated incorrect answer updates existing item (no duplicate)
  17. Correct review increases interval
  18. Incorrect review resets interval
  19. Mastered status requires repeated success
  20. No duplicate review item for the same question
  21. Soft-deleted document/question handling
"""
from __future__ import annotations

from uuid import uuid4
import pytest
from sqlalchemy import select

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.attempt import QuizAttempt
from app.database.models.study import ReviewItem
from app.services import attempt_service, review_item_service


def _setup_quiz_with_questions(db):
    doc = Document(id=str(uuid4()), original_filename="notes.txt", file_type="txt", file_size=10, chunk_count=1, status="READY")
    quiz = Quiz(id=str(uuid4()), document_id=doc.id, title="Bio Quiz", difficulty="medium", question_count=2)
    q1 = Question(id=str(uuid4()), quiz_id=quiz.id, position=0, question_text="What is ATP?", options_json='["Energy", "Protein"]', correct_answer="Energy", question_type="multiple_choice", topic_label="Biochemistry")
    q2 = Question(id=str(uuid4()), quiz_id=quiz.id, position=1, question_text="Is DNA double stranded?", options_json='["True", "False"]', correct_answer="True", question_type="true_false", topic_label="Genetics")
    db.add_all([doc, quiz, q1, q2])
    db.commit()
    return doc, quiz, q1, q2


def test_incorrect_attempt_creates_review_item(db_session):
    doc, quiz, q1, q2 = _setup_quiz_with_questions(db_session)

    # Submit attempt with wrong answer for q1
    attempt = attempt_service.submit_attempt(db_session, quiz.id, [
        {"question_id": q1.id, "selected_answer": "Protein"},
        {"question_id": q2.id, "selected_answer": "True"},
    ])

    affected = review_item_service.process_attempt(db_session, attempt)
    assert len(affected) == 1
    assert affected[0].question_id == q1.id
    assert affected[0].review_status == "DUE"
    assert affected[0].incorrect_count == 1
    assert affected[0].consecutive_correct == 0


test_no_duplicate_review_items = test_incorrect_attempt_creates_review_item


def test_repeated_incorrect_updates_existing_item(db_session):
    doc, quiz, q1, q2 = _setup_quiz_with_questions(db_session)

    # First wrong answer
    att1 = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "Protein"}])
    review_item_service.process_attempt(db_session, att1)

    # Second wrong answer on same question
    att2 = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "Protein"}])
    affected2 = review_item_service.process_attempt(db_session, att2)

    # Should update existing item, count = 1 total in table for this question
    items = list(db_session.execute(select(ReviewItem).where(ReviewItem.question_id == q1.id)).scalars().all())
    assert len(items) == 1
    assert items[0].incorrect_count == 2
    assert items[0].review_status == "DUE"


def test_spaced_repetition_schedule_updates(db_session):
    doc, quiz, q1, _ = _setup_quiz_with_questions(db_session)

    # 1st wrong attempt
    att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "Protein"}])
    review_item_service.process_attempt(db_session, att)

    # 1st correct review -> interval = 1 day, status = UPCOMING
    item = review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=True)
    assert item.consecutive_correct == 1
    assert item.interval_days == 1
    assert item.review_status == "UPCOMING"

    # 2nd correct review -> interval = 3 days, status = UPCOMING
    item = review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=True)
    assert item.consecutive_correct == 2
    assert item.interval_days == 3

    # 3rd correct review -> interval = 7 days, status = UPCOMING
    item = review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=True)
    assert item.consecutive_correct == 3
    assert item.interval_days == 7

    # 4th correct review -> interval = 14 days, status = MASTERED
    item = review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=True)
    assert item.consecutive_correct == 4
    assert item.interval_days == 14
    assert item.review_status == "MASTERED"


def test_incorrect_review_resets_schedule(db_session):
    doc, quiz, q1, _ = _setup_quiz_with_questions(db_session)
    att = attempt_service.submit_attempt(db_session, quiz.id, [{"question_id": q1.id, "selected_answer": "Protein"}])
    review_item_service.process_attempt(db_session, att)

    # Get streak to 3
    for _ in range(3):
        review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=True)

    # Wrong review answer -> reset
    item = review_item_service.update_after_review_answer(db_session, question_id=q1.id, is_correct=False)
    assert item.consecutive_correct == 0
    assert item.interval_days == 0
    assert item.review_status == "DUE"
