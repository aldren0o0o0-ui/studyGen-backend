"""
app/services/flashcard_review_service.py — Phase 5
Session management, card reveal security, SM-2 flashcard rating calculation, and study analytics integration.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models.flashcard import (
    StudySet, Flashcard, FlashcardReviewState, FlashcardReviewSession,
    FlashcardReviewSessionItem, FlashcardReviewResult
)
from app.database.types import normalize_json_value
from app.schemas.flashcards import (
    CreateReviewSessionRequest, ActiveReviewCard, RevealedReviewCard, FlashcardSource
)
from app.services.study_activity_service import log_flashcard_reviewed
from app.services.study_goal_service import record_flashcards_reviewed_progress

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Rating & Scheduling Calculation
# ---------------------------------------------------------------------------

def calculate_flashcard_rating_schedule(
    state: FlashcardReviewState,
    rating: str,
    now: Optional[datetime] = None,
) -> tuple[datetime, int, int, str, bool, float]:
    """
    Calculate new schedule state after an Again, Hard, Good, or Easy rating.

    Returns:
        (next_review_at, new_interval_days, new_consecutive_correct, new_status, was_correct, new_mastery_score)
    """
    if now is None:
        now = _utcnow()

    rating = rating.lower()
    consecutive = state.consecutive_correct
    current_interval = state.interval_days

    if rating == "again":
        was_correct = False
        new_consecutive = 0
        new_interval = 0
        next_dt = now
        new_status = "DUE"
    elif rating == "hard":
        was_correct = True
        new_consecutive = max(1, consecutive)
        new_interval = max(1, current_interval)
        next_dt = now + timedelta(days=1)
        new_status = "LEARNING"
    elif rating == "good":
        was_correct = True
        new_consecutive = consecutive + 1
        if new_consecutive == 1:
            new_interval = 1
        elif new_consecutive == 2:
            new_interval = 3
        elif new_consecutive == 3:
            new_interval = 7
        else:
            new_interval = min(current_interval * 2 if current_interval > 0 else 7, 30)
        next_dt = now + timedelta(days=new_interval)
        new_status = "MASTERED" if new_consecutive >= 4 else "LEARNING"
    elif rating == "easy":
        was_correct = True
        new_consecutive = consecutive + 1
        if new_consecutive == 1:
            new_interval = 2
        elif new_consecutive == 2:
            new_interval = 4
        elif new_consecutive == 3:
            new_interval = 10
        else:
            new_interval = min(int((current_interval if current_interval > 0 else 4) * 2.5), 30)
        next_dt = now + timedelta(days=new_interval)
        new_status = "MASTERED" if new_consecutive >= 3 else "LEARNING"
    else:
        raise ValueError(f"Invalid rating '{rating}'. Expected 'again', 'hard', 'good', or 'easy'.")

    new_mastery = min(100.0, round((new_consecutive / 4.0) * 100.0, 1))
    return next_dt, new_interval, new_consecutive, new_status, was_correct, new_mastery


# ---------------------------------------------------------------------------
# Session Operations
# ---------------------------------------------------------------------------

def create_review_session(
    db: Session, req: CreateReviewSessionRequest
) -> FlashcardReviewSession:
    study_set = db.get(StudySet, req.study_set_id)
    if not study_set or study_set.status == "DELETED":
        raise HTTPException(404, f"StudySet '{req.study_set_id}' not found.")
    if study_set.status == "ARCHIVED":
        raise HTTPException(400, f"Cannot review archived StudySet '{study_set.title}'.")

    # Fetch active cards
    query = db.query(Flashcard).filter(
        Flashcard.study_set_id == req.study_set_id,
        Flashcard.status == "ACTIVE",
    )
    if req.topic_label:
        query = query.filter(Flashcard.topic_label == req.topic_label)
    if req.difficulty:
        query = query.filter(Flashcard.difficulty == req.difficulty)

    cards = query.all()
    if not cards:
        raise HTTPException(404, "No active cards available for this review request.")

    now = _utcnow()
    eligible_cards: List[Flashcard] = []

    if req.session_mode == "due":
        for c in cards:
            st = c.review_state
            if not st or st.review_status in ("NEW", "DUE", "LEARNING"):
                if not st or not st.next_review_at or st.next_review_at <= now:
                    eligible_cards.append(c)
            elif st.review_status == "MASTERED" and st.next_review_at and st.next_review_at <= now:
                eligible_cards.append(c)
    elif req.session_mode == "new_cards":
        eligible_cards = [c for c in cards if not c.review_state or c.review_state.review_status == "NEW"]
    elif req.session_mode == "weak_cards":
        eligible_cards = [
            c for c in cards if not c.review_state or c.review_state.mastery_score < 75.0 or c.review_state.review_status in ("DUE", "LEARNING")
        ]
    else:  # all_cards or custom
        eligible_cards = list(cards)

    if not eligible_cards:
        # Fallback to all active cards if specific due/new filter returned empty
        eligible_cards = list(cards)

    if req.shuffle:
        random.shuffle(eligible_cards)

    selected_cards = eligible_cards[: req.card_count]
    session_id = str(uuid4())

    session = FlashcardReviewSession(
        id=session_id,
        study_set_id=req.study_set_id,
        document_id=study_set.document_id,
        session_mode=req.session_mode,
        status="ACTIVE",
        requested_count=req.card_count,
        actual_count=len(selected_cards),
        reviewed_count=0,
        correct_count=0,
        started_at=now,
    )
    db.add(session)
    db.flush()

    # Create immutable session items
    for pos, card in enumerate(selected_cards, start=1):
        item = FlashcardReviewSessionItem(
            id=str(uuid4()),
            session_id=session_id,
            flashcard_id=card.id,
            position=pos,
            is_revealed=False,
            rating=None,
            reviewed_at=None,
        )
        db.add(item)

    db.commit()
    db.refresh(session)
    return session


def get_review_session(db: Session, session_id: str) -> FlashcardReviewSession:
    session = db.get(FlashcardReviewSession, session_id)
    if not session:
        raise HTTPException(404, f"Review session '{session_id}' not found.")
    return session


def get_active_review_card(item: FlashcardReviewSessionItem) -> ActiveReviewCard:
    """Return front-only active card representation (hides back_text and sources)."""
    card = item.flashcard
    
    # Generate options (1 correct + up to 3 distractors from the same session)
    options = [card.back_text]
    if item.session and item.session.items:
        distractors = [other.flashcard.back_text for other in item.session.items 
                       if other.flashcard_id != card.id and other.flashcard.back_text]
        # Deduplicate distractors
        distractors = list(set(distractors))
        random.shuffle(distractors)
        options.extend(distractors[:3])
        random.shuffle(options)
        
    return ActiveReviewCard(
        item_id=item.id,
        flashcard_id=card.id,
        position=item.position,
        front_text=card.front_text,
        hint_text=card.hint_text,
        topic_label=card.topic_label,
        difficulty=card.difficulty,
        card_type=card.card_type,
        is_revealed=item.is_revealed,
        options=options
    )


def reveal_session_card(
    db: Session, session_id: str, item_id: str
) -> RevealedReviewCard:
    session = get_review_session(db, session_id)
    if session.status != "ACTIVE":
        raise HTTPException(400, f"Cannot reveal card in inactive session (status: {session.status}).")

    item = db.get(FlashcardReviewSessionItem, item_id)
    if not item or item.session_id != session_id:
        raise HTTPException(404, f"Session item '{item_id}' not found.")

    item.is_revealed = True
    db.commit()

    card = item.flashcard
    state = card.review_state

    # Parse safe source citations
    raw_sources = normalize_json_value(card.source_references_json or [], location=f"flashcards/{card.id}/source_references_json")
    sources = [FlashcardSource(**s) for s in raw_sources]

    return RevealedReviewCard(
        item_id=item.id,
        flashcard_id=card.id,
        position=item.position,
        front_text=card.front_text,
        hint_text=card.hint_text,
        topic_label=card.topic_label,
        difficulty=card.difficulty,
        card_type=card.card_type,
        is_revealed=True,
        back_text=card.back_text,
        sources=sources,
        review_status=state.review_status if state else "NEW",
        interval_days=state.interval_days if state else 0,
        consecutive_correct=state.consecutive_correct if state else 0,
        mastery_score=state.mastery_score if state else 0.0,
    )


FLASHCARD_SCHEDULER_VERSION = "flashcard_scheduler_v1"


def rate_session_card(
    db: Session, session_id: str, item_id: str, rating: str
) -> RevealedReviewCard:
    session = get_review_session(db, session_id)
    if session.status != "ACTIVE":
        raise HTTPException(400, f"Cannot rate card in inactive session (status: {session.status}).")

    item = db.get(FlashcardReviewSessionItem, item_id)
    if not item or item.session_id != session_id:
        raise HTTPException(404, f"Session item '{item_id}' not found.")

    # Security check: Must be revealed before rating!
    if not item.is_revealed:
        raise HTTPException(400, "Cannot rate an unrevealed flashcard. Reveal the card first.")

    now = _utcnow()
    rating_clean = rating.lower()
    if rating_clean not in ("again", "hard", "good", "easy"):
        raise HTTPException(422, f"Invalid rating '{rating}'. Allowed: again, hard, good, easy.")

    # Idempotency check: Repeated identical rating returns existing state safely
    if item.rating is not None:
        if item.rating == rating_clean:
            card = item.flashcard
            state = card.review_state
            raw_sources = normalize_json_value(card.source_references_json or [], location=f"flashcards/{card.id}/source_references_json")
            sources = [FlashcardSource(**s) for s in raw_sources]
            return RevealedReviewCard(
                item_id=item.id,
                flashcard_id=card.id,
                position=item.position,
                front_text=card.front_text,
                hint_text=card.hint_text,
                topic_label=card.topic_label,
                difficulty=card.difficulty,
                card_type=card.card_type,
                is_revealed=True,
                back_text=card.back_text,
                sources=sources,
                review_status=state.review_status if state else "NEW",
                interval_days=state.interval_days if state else 0,
                consecutive_correct=state.consecutive_correct if state else 0,
                mastery_score=state.mastery_score if state else 0.0,
            )
        raise HTTPException(409, f"This card has already been rated as '{item.rating}'. Cannot change rating to '{rating_clean}'.")

    card = item.flashcard
    state = card.review_state
    if not state:
        state = FlashcardReviewState(
            id=str(uuid4()),
            flashcard_id=card.id,
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
        db.add(state)
        db.flush()

    prev_interval = state.interval_days

    # Calculate schedule
    next_dt, new_interval, new_consecutive, new_status, was_correct, new_mastery = (
        calculate_flashcard_rating_schedule(state, rating_clean, now=now)
    )

    # Update state
    state.review_status = new_status
    state.interval_days = new_interval
    state.consecutive_correct = new_consecutive
    state.review_count += 1
    if was_correct:
        state.correct_count += 1
    else:
        state.incorrect_count += 1
    state.last_reviewed_at = now
    state.next_review_at = next_dt
    state.mastery_score = new_mastery
    state.updated_at = now

    # Update item
    item.rating = rating_clean
    item.reviewed_at = now

    # Add Result
    result = FlashcardReviewResult(
        id=str(uuid4()),
        session_id=session_id,
        flashcard_id=card.id,
        rating=rating_clean,
        was_correct=was_correct,
        previous_interval_days=prev_interval,
        next_interval_days=new_interval,
        reviewed_at=now,
    )
    db.add(result)

    # Update Session counters
    session.reviewed_count += 1
    if was_correct:
        session.correct_count += 1

    # Log activity
    log_flashcard_reviewed(db, session.study_set_id, card.id, was_correct)

    # Update goals
    record_flashcards_reviewed_progress(db, cards_count=1)

    # Auto-complete if all cards reviewed
    if session.reviewed_count >= session.actual_count:
        session.status = "COMPLETED"
        session.completed_at = now

    db.commit()
    db.refresh(session)

    # Return revealed card view
    raw_sources = normalize_json_value(card.source_references_json or [], location=f"flashcards/{card.id}/source_references_json")
    sources = [FlashcardSource(**s) for s in raw_sources]

    return RevealedReviewCard(
        item_id=item.id,
        flashcard_id=card.id,
        position=item.position,
        front_text=card.front_text,
        hint_text=card.hint_text,
        topic_label=card.topic_label,
        difficulty=card.difficulty,
        card_type=card.card_type,
        is_revealed=True,
        back_text=card.back_text,
        sources=sources,
        review_status=state.review_status,
        interval_days=state.interval_days,
        consecutive_correct=state.consecutive_correct,
        mastery_score=state.mastery_score,
    )


def complete_review_session(db: Session, session_id: str) -> FlashcardReviewSession:
    session = get_review_session(db, session_id)
    if session.status != "COMPLETED":
        session.status = "COMPLETED"
        session.completed_at = _utcnow()
        db.commit()
        db.refresh(session)
    return session
