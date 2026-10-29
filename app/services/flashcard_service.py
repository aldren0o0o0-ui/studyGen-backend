"""
app/services/flashcard_service.py — Phase 5
CRUD operations and management services for StudySets and Flashcards.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database.models.document import Document
from app.database.models.flashcard import StudySet, StudySetSourceDocument, Flashcard, FlashcardReviewState
from app.schemas.flashcards import (
    StudySetCreate, StudySetUpdate, FlashcardCreate, FlashcardUpdate, FlashcardSafePreview
)
from app.services.flashcard_validation_service import validate_flashcard_dict, _normalize_text

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# StudySet Operations
# ---------------------------------------------------------------------------

def create_study_set(db: Session, req: StudySetCreate) -> StudySet:
    if req.document_id:
        doc = db.get(Document, req.document_id)
        if not doc or doc.deleted_at is not None:
            raise HTTPException(404, f"Document '{req.document_id}' not found.")

    study_set_id = str(uuid4())
    study_set = StudySet(
        id=study_set_id,
        title=req.title.strip(),
        description=req.description.strip() if req.description else None,
        document_id=req.document_id,
        topic_label=req.topic_label.strip() if req.topic_label else None,
        set_type=req.set_type,
        status="ACTIVE",
        card_count=0,
    )
    db.add(study_set)
    db.flush()

    if req.initial_cards:
        for pos, c in enumerate(req.initial_cards, start=1):
            _add_card_internal(db, study_set, c, position=pos)

    study_set.card_count = len(req.initial_cards)
    db.commit()
    db.refresh(study_set)
    return study_set


def list_study_sets(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    document_id: Optional[str] = None,
    folder_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    topic: Optional[str] = None,
    set_type: Optional[str] = None,
    status: str = "ACTIVE",
) -> Tuple[List[StudySet], int]:
    query = db.query(StudySet).options(selectinload(StudySet.source_documents)).filter(StudySet.status == status)

    if document_id:
        query = query.filter(StudySet.document_id == document_id)
    if folder_id:
        query = query.filter(StudySet.folder_id == folder_id)
    if scope_type:
        query = query.filter(StudySet.scope_type == scope_type.upper())
    if topic:
        query = query.filter(StudySet.topic_label == topic)
    if set_type:
        query = query.filter(StudySet.set_type == set_type)
    if search:
        search_fmt = f"%{search.strip()}%"
        query = query.join(Document, StudySet.document_id == Document.id, isouter=True).filter(
            or_(
                StudySet.title.ilike(search_fmt),
                StudySet.description.ilike(search_fmt),
                Document.original_filename.ilike(search_fmt),
            )
        )

    total = query.count()
    items = query.order_by(StudySet.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_study_set(db: Session, study_set_id: str) -> StudySet:
    study_set = db.query(StudySet).options(selectinload(StudySet.source_documents)).filter(StudySet.id == study_set_id).first()
    if not study_set or study_set.status == "DELETED":
        raise HTTPException(404, f"StudySet '{study_set_id}' not found.")
    return study_set


def update_study_set(db: Session, study_set_id: str, req: StudySetUpdate) -> StudySet:
    study_set = get_study_set(db, study_set_id)

    if req.title is not None:
        study_set.title = req.title.strip()
    if req.description is not None:
        study_set.description = req.description.strip() if req.description else None
    if req.topic_label is not None:
        study_set.topic_label = req.topic_label.strip() if req.topic_label else None
    if req.status is not None:
        study_set.status = req.status

    study_set.updated_at = _utcnow()
    db.commit()
    db.refresh(study_set)
    return study_set


def delete_study_set(db: Session, study_set_id: str) -> StudySet:
    study_set = get_study_set(db, study_set_id)
    now = _utcnow()
    study_set.status = "DELETED"
    study_set.deleted_at = now

    # Soft delete cards
    cards = db.query(Flashcard).filter(Flashcard.study_set_id == study_set_id).all()
    for card in cards:
        card.status = "DELETED"
        card.deleted_at = now

    db.commit()
    return study_set


def list_due_study_sets(db: Session) -> List[StudySet]:
    """List all ACTIVE study sets that have overdue or active cards for review."""
    sets = db.query(StudySet).filter(StudySet.status == "ACTIVE").all()
    due_sets = []
    for st in sets:
        counts = get_study_set_counts(db, st.id)
        if counts["due_card_count"] > 0 or counts["new_card_count"] > 0 or counts["card_count"] > 0:
            due_sets.append(st)
    return due_sets


def get_study_set_counts(db: Session, study_set_id: str) -> dict[str, int]:
    """Calculate counts for NEW, DUE, and MASTERED cards in a StudySet."""
    now = _utcnow()
    cards = db.query(Flashcard).filter(
        Flashcard.study_set_id == study_set_id,
        Flashcard.status == "ACTIVE",
    ).all()

    due = 0
    new_cards = 0
    mastered = 0

    for card in cards:
        state = card.review_state
        if not state:
            new_cards += 1
            continue

        if state.review_status == "NEW":
            new_cards += 1
        elif state.review_status == "MASTERED":
            mastered += 1
            # A mastered card could also be due if next_review_at <= now
            if state.next_review_at and state.next_review_at <= now:
                due += 1
        elif state.review_status in ("DUE", "LEARNING"):
            if not state.next_review_at or state.next_review_at <= now:
                due += 1

    return {
        "card_count": len(cards),
        "due_card_count": due,
        "new_card_count": new_cards,
        "mastered_card_count": mastered,
    }


# ---------------------------------------------------------------------------
# Flashcard Operations
# ---------------------------------------------------------------------------

def _add_card_internal(
    db: Session, study_set: StudySet, card_create: FlashcardCreate, position: int
) -> Flashcard:
    is_valid, reason = validate_flashcard_dict(card_create.model_dump())
    if not is_valid:
        raise HTTPException(422, f"Invalid flashcard content: {reason}")

    card_id = str(uuid4())
    flashcard = Flashcard(
        id=card_id,
        study_set_id=study_set.id,
        document_id=study_set.document_id,
        source_question_id=None,
        front_text=card_create.front_text.strip(),
        back_text=card_create.back_text.strip(),
        hint_text=card_create.hint_text.strip() if card_create.hint_text else None,
        topic_label=card_create.topic_label.strip() if card_create.topic_label else study_set.topic_label,
        difficulty=card_create.difficulty,
        card_type=card_create.card_type,
        source_references_json="[]",
        generation_method="manual",
        prompt_version=None,
        model_name=None,
        position=position,
        status="ACTIVE",
        is_user_edited=False,
        source_validation_status="valid",
    )
    db.add(flashcard)

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
    return flashcard


def add_flashcard(db: Session, study_set_id: str, req: FlashcardCreate) -> Flashcard:
    study_set = get_study_set(db, study_set_id)

    # Duplicate front text check
    front_norm = _normalize_text(req.front_text)
    existing = db.query(Flashcard).filter(
        Flashcard.study_set_id == study_set_id,
        Flashcard.status != "DELETED",
    ).all()

    for c in existing:
        if _normalize_text(c.front_text) == front_norm:
            raise HTTPException(409, f"A flashcard with front text '{req.front_text[:40]}' already exists in this study set.")

    max_pos = max([c.position for c in existing], default=0)
    card = _add_card_internal(db, study_set, req, position=max_pos + 1)

    study_set.card_count = len(existing) + 1
    study_set.updated_at = _utcnow()
    db.commit()
    db.refresh(card)
    return card


def get_flashcard(db: Session, flashcard_id: str) -> Flashcard:
    # Join through the owned StudySet so a guessed child ID cannot cross users.
    card = db.execute(
        select(Flashcard)
        .join(StudySet, Flashcard.study_set_id == StudySet.id)
        .where(Flashcard.id == flashcard_id)
    ).scalar_one_or_none()
    if not card or card.status == "DELETED":
        raise HTTPException(404, f"Flashcard '{flashcard_id}' not found.")
    return card


def _normalize_formatting(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFC", text)
    norm = re.sub(r"\s+", " ", norm)
    return norm.strip()


def _is_formatting_only_edit(old_text: str, new_text: str) -> bool:
    return _normalize_formatting(old_text) == _normalize_formatting(new_text)


def update_flashcard(db: Session, flashcard_id: str, req: FlashcardUpdate) -> Flashcard:
    card = get_flashcard(db, flashcard_id)
    meaningful_content_changed = False

    if req.front_text is not None and req.front_text.strip() != card.front_text:
        is_format_only = _is_formatting_only_edit(card.front_text, req.front_text)
        card.front_text = req.front_text.strip()
        if not is_format_only:
            meaningful_content_changed = True

    if req.back_text is not None and req.back_text.strip() != card.back_text:
        is_format_only = _is_formatting_only_edit(card.back_text, req.back_text)
        card.back_text = req.back_text.strip()
        if not is_format_only:
            meaningful_content_changed = True

    if req.hint_text is not None:
        card.hint_text = req.hint_text.strip() if req.hint_text else None
    if req.topic_label is not None:
        card.topic_label = req.topic_label.strip() if req.topic_label else None
    if req.difficulty is not None:
        card.difficulty = req.difficulty
    if req.card_type is not None:
        card.card_type = req.card_type
    if req.status is not None:
        card.status = req.status

    # Validate resulting card content
    is_valid, reason = validate_flashcard_dict({
        "front_text": card.front_text,
        "back_text": card.back_text,
        "card_type": card.card_type,
        "difficulty": card.difficulty,
    })
    if not is_valid:
        raise HTTPException(422, f"Invalid flashcard content: {reason}")

    if meaningful_content_changed:
        card.is_user_edited = True
        if card.source_references_json and card.source_references_json != "[]":
            card.source_validation_status = "needs_review"
        if card.review_state:
            card.review_state.review_status = "LEARNING"
            card.review_state.consecutive_correct = 0
            card.review_state.interval_days = 0
            card.review_state.next_review_at = _utcnow()
            card.review_state.mastery_score = 0.0

    card.updated_at = _utcnow()
    db.commit()
    db.refresh(card)
    return card


def delete_flashcard(db: Session, flashcard_id: str) -> Flashcard:
    card = get_flashcard(db, flashcard_id)
    card.status = "DELETED"
    card.deleted_at = _utcnow()
    db.flush()

    # Sync card_count in StudySet
    study_set = db.get(StudySet, card.study_set_id)
    if study_set:
        study_set.card_count = db.query(Flashcard).filter(
            Flashcard.study_set_id == study_set.id,
            Flashcard.status != "DELETED",
        ).count()
        study_set.updated_at = _utcnow()

    db.commit()
    return card


def list_flashcards_in_set(db: Session, study_set_id: str) -> List[FlashcardSafePreview]:
    get_study_set(db, study_set_id)  # verifies exists & active

    cards = db.query(Flashcard).filter(
        Flashcard.study_set_id == study_set_id,
        Flashcard.status != "DELETED",
    ).order_by(Flashcard.position).all()

    previews: List[FlashcardSafePreview] = []
    for c in cards:
        state = c.review_state
        previews.append(
            FlashcardSafePreview(
                flashcard_id=c.id,
                study_set_id=c.study_set_id,
                front_text=c.front_text,
                back_text=c.back_text,
                hint_text=c.hint_text,
                topic_label=c.topic_label,
                difficulty=c.difficulty,
                card_type=c.card_type,
                generation_method=c.generation_method,
                position=c.position,
                status=c.status,
                is_user_edited=c.is_user_edited,
                review_status=state.review_status if state else "NEW",
                mastery_score=state.mastery_score if state else 0.0,
                next_review_at=state.next_review_at if state else None,
            )
        )
    return previews
