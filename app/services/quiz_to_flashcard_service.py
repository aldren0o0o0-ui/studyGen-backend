"""
app/services/quiz_to_flashcard_service.py — Phase 5
Converts existing Quiz Questions into Flashcards (Zero-Gemini operation).
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.flashcard import StudySet, Flashcard, FlashcardReviewState
from app.services.flashcard_validation_service import _normalize_text

logger = logging.getLogger(__name__)


def convert_quiz_questions_to_flashcards(
    db: Session,
    quiz_id: str,
    target_study_set_id: Optional[str] = None,
    custom_title: Optional[str] = None,
    question_ids: Optional[List[str]] = None,
) -> tuple[StudySet, List[Flashcard], int]:
    """
    Convert quiz questions into flashcards without calling Gemini.

    Returns:
        (study_set, created_flashcards, skipped_count)
    """
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise ValueError(f"Quiz '{quiz_id}' not found.")

    # Filter questions
    query = db.query(Question).filter(Question.quiz_id == quiz_id)
    if question_ids:
        query = query.filter(Question.id.in_(question_ids))
    questions = query.order_by(Question.position).all()

    if not questions:
        raise ValueError("No matching questions found for conversion.")

    # Find or create target StudySet
    if target_study_set_id:
        study_set = db.get(StudySet, target_study_set_id)
        if not study_set or study_set.status == "DELETED":
            raise ValueError(f"Target StudySet '{target_study_set_id}' not found.")
    else:
        title = custom_title or f"Flashcards from {quiz.title}"
        study_set = StudySet(
            id=str(uuid4()),
            title=title,
            description=f"Converted from quiz '{quiz.title}' ({len(questions)} questions)",
            document_id=quiz.document_id,
            topic_label=questions[0].topic_label if questions else None,
            set_type="generated_from_quiz",
            status="ACTIVE",
            card_count=0,
        )
        db.add(study_set)
        db.flush()

    # Existing cards in set to avoid duplicates (by source_question_id and front_text)
    existing_cards = db.query(Flashcard).filter(
        Flashcard.study_set_id == study_set.id,
        Flashcard.status != "DELETED",
    ).all()
    existing_fronts = {_normalize_text(c.front_text) for c in existing_cards}
    existing_question_ids = {c.source_question_id for c in existing_cards if c.source_question_id}

    # Get max position in set
    max_pos = max([c.position for c in existing_cards], default=0)

    created_cards: List[Flashcard] = []
    skipped_count = 0

    for q in questions:
        front = q.question_text.strip()
        front_norm = _normalize_text(front)

        if q.id in existing_question_ids or front_norm in existing_fronts:
            skipped_count += 1
            continue
        existing_question_ids.add(q.id)
        existing_fronts.add(front_norm)

        # Build Back text
        if q.question_type == "true_false":
            back = f"Answer: {q.correct_answer}"
            if q.explanation:
                back += f"\n\nExplanation: {q.explanation}"
            card_type = "basic"
        elif q.question_type == "identification":
            back = q.correct_answer
            card_type = "definition"
        else:  # multiple_choice
            back = f"Correct Answer: {q.correct_answer}"
            if q.explanation:
                back += f"\n\nExplanation: {q.explanation}"
            card_type = "question_answer"

        max_pos += 1
        card_id = str(uuid4())

        flashcard = Flashcard(
            id=card_id,
            study_set_id=study_set.id,
            document_id=quiz.document_id,
            source_question_id=q.id,
            front_text=front,
            back_text=back,
            hint_text=None,
            topic_label=q.topic_label,
            difficulty=q.difficulty or "medium",
            card_type=card_type,
            source_references_json=q.source_references_json or "[]",
            generation_method="quiz_conversion",
            prompt_version=None,
            model_name=None,
            position=max_pos,
            status="ACTIVE",
            is_user_edited=False,
            source_validation_status="valid",
        )
        db.add(flashcard)

        # Create review state
        review_state = FlashcardReviewState(
            id=str(uuid4()),
            flashcard_id=card_id,
            review_status="NEW",
            ease_level=2.5,
            review_count=0,
            correct_count=0,
            incorrect_count=0,
            consecutive_correct=0,
            interval_days=0,
            last_reviewed_at=None,
            next_review_at=None,
            mastery_score=0.0,
        )
        db.add(review_state)
        created_cards.append(flashcard)

    # Flush pending inserts so the COUNT query sees them
    db.flush()

    # Sync card_count
    study_set.card_count = db.query(Flashcard).filter(
        Flashcard.study_set_id == study_set.id,
        Flashcard.status != "DELETED",
    ).count()

    db.commit()
    db.refresh(study_set)
    return study_set, created_cards, skipped_count
