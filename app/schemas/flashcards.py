"""
app/schemas/flashcards.py — Phase 5
Pydantic schemas for Study Sets, Flashcards, and Review Sessions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_CARD_TYPES = {"basic", "definition", "concept", "question_answer"}
VALID_CARD_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_SET_TYPES = {"generated_from_document", "generated_from_quiz", "manual", "mixed"}
VALID_SESSION_MODES = {"due", "all_cards", "new_cards", "weak_cards", "custom"}
VALID_RATINGS = {"again", "hard", "good", "easy"}


# ---------------------------------------------------------------------------
# Source reference
# ---------------------------------------------------------------------------

class FlashcardSource(BaseModel):
    document_id: Optional[str] = None
    chunk_index: int
    page_number: int          # -1 when unknown
    filename: str
    excerpt: str


# ---------------------------------------------------------------------------
# Flashcard Schemas
# ---------------------------------------------------------------------------

class FlashcardCreate(BaseModel):
    front_text: str = Field(..., min_length=1, max_length=2000)
    back_text: str = Field(..., min_length=1, max_length=4000)
    hint_text: Optional[str] = Field(default=None, max_length=1000)
    topic_label: Optional[str] = Field(default=None, max_length=255)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    card_type: Literal["basic", "definition", "concept", "question_answer"] = "concept"

    @model_validator(mode="after")
    def validate_card_content(self) -> "FlashcardCreate":
        front = self.front_text.strip()
        back = self.back_text.strip()
        if not front:
            raise ValueError("front_text cannot be empty.")
        if not back:
            raise ValueError("back_text cannot be empty.")
        if front.lower() == back.lower():
            raise ValueError("front_text and back_text cannot be identical.")
        return self


class FlashcardUpdate(BaseModel):
    front_text: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    back_text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    hint_text: Optional[str] = Field(default=None, max_length=1000)
    topic_label: Optional[str] = Field(default=None, max_length=255)
    difficulty: Optional[Literal["easy", "medium", "hard"]] = None
    card_type: Optional[Literal["basic", "definition", "concept", "question_answer"]] = None
    status: Optional[Literal["ACTIVE", "SUSPENDED", "DELETED"]] = None


class FlashcardSafePreview(BaseModel):
    """Browsing card view — safe to include both front and back during browsing."""
    flashcard_id: str
    study_set_id: str
    front_text: str
    back_text: str
    hint_text: Optional[str] = None
    topic_label: Optional[str] = None
    difficulty: str
    card_type: str
    generation_method: str
    position: int
    status: str
    is_user_edited: bool = False
    review_status: str = "NEW"
    mastery_score: float = 0.0
    next_review_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class FlashcardDetail(FlashcardSafePreview):
    """Full detail view for editing / inspecting cards."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    document_id: Optional[str] = None
    source_question_id: Optional[str] = None
    sources: List[FlashcardSource] = []
    prompt_version: Optional[str] = None
    ai_model_name: Optional[str] = Field(
        default=None,
        validation_alias="model_name",
        serialization_alias="model_name",
    )
    source_validation_status: str = "valid"
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Study Set Schemas
# ---------------------------------------------------------------------------

class StudySetCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    description: Optional[str] = Field(default=None, max_length=2000)
    document_id: Optional[str] = None
    topic_label: Optional[str] = Field(default=None, max_length=255)
    set_type: Literal["generated_from_document", "generated_from_quiz", "manual", "mixed", "generated_from_folder", "generated_from_selected_documents"] = "manual"
    initial_cards: List[FlashcardCreate] = Field(default=[])


class StudySetUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=512)
    description: Optional[str] = Field(default=None, max_length=2000)
    topic_label: Optional[str] = Field(default=None, max_length=255)
    status: Optional[Literal["ACTIVE", "ARCHIVED", "DELETED"]] = None


class StudySetSummary(BaseModel):
    study_set_id: str
    title: str
    description: Optional[str] = None
    document_id: Optional[str] = None
    topic_label: Optional[str] = None
    set_type: str
    status: str
    card_count: int
    scope_type: str = "DOCUMENT"
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    source_document_count: Optional[int] = None
    due_card_count: int = 0
    new_card_count: int = 0
    mastered_card_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_reviewed_at: Optional[datetime] = None
    document_filename: Optional[str] = None

    model_config = {"from_attributes": True}


class StudySetListResponse(BaseModel):
    items: List[StudySetSummary]
    page: int
    page_size: int
    total: int


class StudySetDetail(StudySetSummary):
    cards: List[FlashcardSafePreview] = []


# ---------------------------------------------------------------------------
# Generation Requests
# ---------------------------------------------------------------------------

class GenerateFlashcardsRequest(BaseModel):
    document_id: Optional[str] = None
    scope_type: Optional[str] = "DOCUMENT"
    folder_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    include_subfolders: bool = False
    title: Optional[str] = Field(default=None, max_length=512)
    card_count: int = Field(default=10, ge=1, le=50)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"
    card_types: List[str] = Field(default=["concept", "definition"])
    coverage_mode: Literal["balanced", "focused", "key_concepts", "definitions", "exam_review"] = "balanced"
    focus_mode: Optional[Literal["balanced", "key_concepts", "definitions", "exam_review"]] = None
    topic_query: Optional[str] = None
    selected_chunk_indexes: Optional[List[int]] = None
    include_hints: bool = True
    include_sources: bool = True

    @model_validator(mode="after")
    def validate_request(self) -> "GenerateFlashcardsRequest":
        st = (self.scope_type or "DOCUMENT").upper()
        if st == "DOCUMENT" and not self.document_id and self.document_ids:
            if len(self.document_ids) == 1:
                self.document_id = self.document_ids[0]
            else:
                self.scope_type = "SELECTED_DOCUMENTS"

        if st == "DOCUMENT" and not self.document_id:
            raise ValueError("document_id is required for DOCUMENT scope.")
        elif st == "SELECTED_DOCUMENTS" and not self.document_ids:
            raise ValueError("document_ids is required for SELECTED_DOCUMENTS scope.")
        elif st == "FOLDER" and not self.folder_id:
            raise ValueError("folder_id is required for FOLDER scope.")

        if self.coverage_mode == "focused":
            has_indexes = bool(self.selected_chunk_indexes)
            has_query = bool(self.topic_query and self.topic_query.strip())
            if not has_indexes and not has_query:
                raise ValueError("focused coverage_mode requires topic_query or selected_chunk_indexes.")
        return self


class FolderFlashcardRequest(BaseModel):
    include_subfolders: bool = False
    card_count: int = Field(default=10, ge=1, le=50)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"
    card_types: List[str] = Field(default=["concept", "definition"])
    focus_mode: Literal["balanced", "key_concepts", "definitions", "exam_review"] = "balanced"
    topic_query: Optional[str] = None
    include_hints: bool = True
    include_sources: bool = True


class ConvertQuizToFlashcardsRequest(BaseModel):
    quiz_id: str
    title: Optional[str] = Field(default=None, max_length=512)
    question_ids: Optional[List[str]] = None
    study_set_id: Optional[str] = None  # If provided, appends to existing study set


# ---------------------------------------------------------------------------
# Review Session Schemas
# ---------------------------------------------------------------------------

class CreateReviewSessionRequest(BaseModel):
    study_set_id: str
    session_mode: Literal["due", "all_cards", "new_cards", "weak_cards", "custom"] = "due"
    card_count: int = Field(default=20, ge=1, le=50)
    topic_label: Optional[str] = None
    difficulty: Optional[Literal["easy", "medium", "hard"]] = None
    shuffle: bool = True


class ActiveReviewCard(BaseModel):
    """Active card in a review session — NEVER exposes back_text or sources until revealed."""
    item_id: str
    flashcard_id: str
    position: int
    front_text: str
    hint_text: Optional[str] = None
    topic_label: Optional[str] = None
    difficulty: str
    card_type: str
    is_revealed: bool = False
    options: Optional[List[str]] = None


class RevealedReviewCard(ActiveReviewCard):
    """Card after reveal — includes back_text and safe source citations."""
    back_text: str
    sources: List[FlashcardSource] = []
    review_status: str = "NEW"
    interval_days: int = 0
    consecutive_correct: int = 0
    mastery_score: float = 0.0


class ReviewRatingRequest(BaseModel):
    item_id: str
    rating: Literal["again", "hard", "good", "easy"]


class FlashcardReviewSessionSummary(BaseModel):
    session_id: str
    study_set_id: str
    document_id: Optional[str] = None
    session_mode: str
    status: str
    requested_count: int
    actual_count: int
    reviewed_count: int
    correct_count: int
    started_at: datetime
    completed_at: Optional[datetime] = None


class FlashcardReviewSessionDetail(FlashcardReviewSessionSummary):
    current_card: Optional[ActiveReviewCard] = None
    revealed_card: Optional[RevealedReviewCard] = None
    items: List[ActiveReviewCard] = []
