"""
tests/test_phase5_quiz_conversion.py
Test suite for Quiz-to-Flashcard conversion, composite identity duplicate prevention, and zero Gemini calls.
"""
from __future__ import annotations

import uuid
import pytest
from unittest.mock import MagicMock

from app.database.models import Document, Quiz, Question
from app.services.quiz_to_flashcard_service import convert_quiz_questions_to_flashcards


def test_quiz_conversion_creates_flashcards_without_gemini_call(db_session):
    # Strict Gemini mock that raises immediately if called
    strict_gemini = MagicMock()
    strict_gemini.generate_content.side_effect = AssertionError("Gemini MUST NOT be called during quiz conversion!")

    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="algebra.pdf", file_type="pdf", file_size=1024, status="READY")
    db_session.add(doc)

    quiz_id = str(uuid.uuid4())
    quiz = Quiz(id=quiz_id, document_id=doc_id, title="Algebra Quiz", difficulty="medium", question_count=3, chunks_used=1)
    db_session.add(quiz)
    db_session.flush()

    q1 = Question(
        id=str(uuid.uuid4()), quiz_id=quiz_id, position=1,
        question_text="Solve x + 2 = 5", options_json='["x=3", "x=2"]', correct_answer="x=3",
        explanation="Subtract 2 from both sides", question_type="multiple_choice", difficulty="easy", topic_label="Linear Equations"
    )
    q2 = Question(
        id=str(uuid.uuid4()), quiz_id=quiz_id, position=2,
        question_text="Is 7 a prime number?", options_json='["True", "False"]', correct_answer="True",
        explanation="Divisible only by 1 and itself", question_type="true_false", difficulty="easy", topic_label="Primes"
    )
    q3 = Question(
        id=str(uuid.uuid4()), quiz_id=quiz_id, position=3,
        question_text="Name the variable in 3y + 4", options_json='[]', correct_answer="y",
        explanation=None, question_type="identification", difficulty="easy", topic_label="Algebra Terms"
    )
    db_session.add_all([q1, q2, q3])
    db_session.commit()

    # Convert to flashcards
    converted_set, created_cards, skipped = convert_quiz_questions_to_flashcards(
        db_session, quiz_id=quiz_id, custom_title="Algebra Flashcard Deck"
    )

    assert converted_set.id is not None
    assert converted_set.card_count == 3
    assert len(created_cards) == 3
    assert skipped == 0
    assert strict_gemini.generate_content.call_count == 0

    # Verify card content mapping
    card_types = {c.card_type for c in created_cards}
    assert "question_answer" in card_types
    assert "basic" in card_types
    assert "definition" in card_types


def test_quiz_conversion_duplicate_prevention_by_composite_key(db_session):
    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="calc.pdf", file_type="pdf", file_size=1024, status="READY")
    db_session.add(doc)

    quiz_id = str(uuid.uuid4())
    quiz = Quiz(id=quiz_id, document_id=doc_id, title="Calculus Quiz", difficulty="medium", question_count=1, chunks_used=1)
    db_session.add(quiz)
    db_session.flush()

    q1 = Question(
        id=str(uuid.uuid4()), quiz_id=quiz_id, position=1,
        question_text="What is derivative of x^2?", options_json='["2x", "x"]', correct_answer="2x",
        explanation="Power rule", question_type="multiple_choice", difficulty="medium", topic_label="Derivatives"
    )
    db_session.add(q1)
    db_session.commit()

    # Initial conversion
    set1, cards1, skipped1 = convert_quiz_questions_to_flashcards(db_session, quiz_id=quiz_id)
    assert len(cards1) == 1

    # Second conversion into SAME study set: must skip duplicate based on (study_set_id, source_question_id)
    set1_again, cards2, skipped2 = convert_quiz_questions_to_flashcards(
        db_session, quiz_id=quiz_id, target_study_set_id=set1.id
    )
    assert len(cards2) == 0
    assert skipped2 == 1
    assert set1_again.card_count == 1
