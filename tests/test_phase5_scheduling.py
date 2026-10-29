"""
tests/test_phase5_scheduling.py
Test suite for SM-2 inspired flashcard rating schedule calculations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest

from app.database.models import FlashcardReviewState
from app.services.flashcard_review_service import calculate_flashcard_rating_schedule


def test_sm2_schedule_progression_again_hard_good_easy():
    state = FlashcardReviewState(
        id=str(uuid.uuid4()),
        flashcard_id=str(uuid.uuid4()),
        review_status="NEW",
        ease_level=2.5,
        review_count=0,
        correct_count=0,
        incorrect_count=0,
        consecutive_correct=0,
        interval_days=0,
        mastery_score=0.0,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. "again": resets interval to 0, marks as DUE (immediately re-schedulable), streak reset to 0
    next_dt, interval, streak, status, correct, mastery = calculate_flashcard_rating_schedule(state, "again", now=now)
    assert interval == 0
    assert streak == 0
    assert status == "DUE"
    assert correct is False

    # 2. "hard": conservative interval (1 day on first review)
    next_dt, interval, streak, status, correct, mastery = calculate_flashcard_rating_schedule(state, "hard", now=now)
    assert interval == 1
    assert streak == 1
    assert status == "LEARNING"
    assert correct is True

    # 3. "good" from consecutive=1: scheduler gives interval=3 (second good step)
    state.interval_days = 1
    state.consecutive_correct = 1
    state.review_status = "LEARNING"
    next_dt, interval, streak, status, correct, mastery = calculate_flashcard_rating_schedule(state, "good", now=now)
    assert interval == 3
    assert streak == 2
    assert correct is True

    # 4. "easy" from consecutive=2 gives larger interval than "good" from same state
    state2 = FlashcardReviewState(
        id=str(uuid.uuid4()), flashcard_id=str(uuid.uuid4()),
        review_status="LEARNING", ease_level=2.5, review_count=2,
        correct_count=2, incorrect_count=0, consecutive_correct=2,
        interval_days=3, mastery_score=0.0,
    )
    next_dt, interval_easy, _, _, _, _ = calculate_flashcard_rating_schedule(state2, "easy", now=now)
    next_dt, interval_good, _, _, _, _ = calculate_flashcard_rating_schedule(state2, "good", now=now)
    assert interval_easy > interval_good


def test_sm2_schedule_maximum_interval_enforcement():
    state = FlashcardReviewState(
        id=str(uuid.uuid4()),
        flashcard_id=str(uuid.uuid4()),
        review_status="MASTERED",
        ease_level=2.5,
        review_count=10,
        correct_count=10,
        incorrect_count=0,
        consecutive_correct=10,
        interval_days=300,
        mastery_score=1.0,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    next_dt, interval, streak, status, correct, mastery = calculate_flashcard_rating_schedule(state, "easy", now=now)
    assert interval <= 365
