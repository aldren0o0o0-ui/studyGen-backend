"""
app/schemas/quizzes.py — Phase 3
All schemas related to quiz generation, retrieval, and active sessions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_QUESTION_TYPES = {"multiple_choice", "true_false", "identification"}
VALID_DIFFICULTIES = {"easy", "medium", "hard", "mixed"}
VALID_COVERAGE_MODES = {"balanced", "focused"}


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class GenerateQuizRequest(BaseModel):
    document_id: Optional[str] = None
    # Wave 1 & 3 Scope Fields
    scope_type: Optional[str] = None  # "DOCUMENT", "SELECTED_DOCUMENTS", "FOLDER"
    folder_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    include_subfolders: bool = False

    question_count: int = Field(default=5, ge=1, le=50)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"
    question_types: List[str] = Field(default=["multiple_choice"])
    include_explanations: bool = True
    include_sources: bool = True
    coverage_mode: Literal["balanced", "focused"] = "balanced"

    # Focused-mode fields
    topic_query: Optional[str] = None
    selected_chunk_indexes: Optional[List[int]] = None

    @model_validator(mode="after")
    def validate_request(self) -> "GenerateQuizRequest":
        # Ensure at least document_id, document_ids, or folder_id is provided
        if not self.document_id and not self.folder_id and not self.document_ids:
            raise ValueError("Either document_id, folder_id, or document_ids must be specified.")

        # At least one type required
        if not self.question_types:
            raise ValueError("At least one question_type must be specified.")

        # Validate types
        unsupported = set(self.question_types) - SUPPORTED_QUESTION_TYPES
        if unsupported:
            raise ValueError(
                f"Unsupported question type(s): {sorted(unsupported)}. "
                f"Supported: {sorted(SUPPORTED_QUESTION_TYPES)}"
            )

        # No duplicates
        if len(set(self.question_types)) != len(self.question_types):
            raise ValueError("question_types must not contain duplicates.")

        return self


class FolderQuizRequest(BaseModel):
    include_subfolders: bool = False
    question_count: int = Field(default=10, ge=1, le=50)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"
    question_types: List[str] = Field(default=["multiple_choice", "true_false", "identification"])
    coverage_mode: Literal["balanced", "focused"] = "balanced"
    topic_query: Optional[str] = None
    include_explanations: bool = True
    include_sources: bool = True


# ---------------------------------------------------------------------------
# Source reference (attached to each question)
# ---------------------------------------------------------------------------

class QuestionSource(BaseModel):
    document_id: Optional[str] = None
    chunk_index: int
    page_number: int          # -1 when unknown
    filename: str
    excerpt: str


# ---------------------------------------------------------------------------
# Active quiz — NEVER exposes correct_answer, accepted_answers, or explanation
# ---------------------------------------------------------------------------

class ActiveQuestion(BaseModel):
    """Returned to the client during an active quiz session."""
    question_id: str
    question_type: str
    question: str
    options: List[str]                  # Empty for identification
    difficulty: str
    topic: str


class ActiveQuizResponse(BaseModel):
    """Safe response for active quiz — omits correct answers."""
    quiz_id: str
    document_id: Optional[str] = None
    scope_type: str = "DOCUMENT"
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    source_document_count: int = 1
    quiz_title: str
    difficulty: str
    questions: List[ActiveQuestion]
    source_file: str
    chunks_used: int
    prompt_version: str


# ---------------------------------------------------------------------------
# Full question (used in review / quiz history — after submission)
# ---------------------------------------------------------------------------

class Question(BaseModel):
    question_id: Optional[str] = None
    question_type: str = "multiple_choice"
    question: str
    options: List[str] = []
    correct_answer: str
    accepted_answers: List[str] = []
    explanation: Optional[str] = None
    difficulty: str = "medium"
    topic: Optional[str] = None
    sources: List[QuestionSource] = []
    # Legacy field — kept for backward compat
    source_chunk_indexes: List[int] = []

    @model_validator(mode="after")
    def validate_question(self) -> "Question":
        if self.question_type == "multiple_choice":
            if self.correct_answer and self.options and self.correct_answer not in self.options:
                raise ValueError(
                    f"correct_answer '{self.correct_answer}' not in options {self.options}"
                )
        return self


# ---------------------------------------------------------------------------
# Quiz responses
# ---------------------------------------------------------------------------

class QuizResponse(BaseModel):
    """Full quiz response (legacy-compatible, used in history)."""
    quiz_id: Optional[str] = None
    document_id: Optional[str] = None
    scope_type: str = "DOCUMENT"
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    source_document_count: int = 1
    quiz_title: str
    difficulty: str
    questions: List[Question]
    source_file: str
    chunks_used: int


class QuizSummary(BaseModel):
    quiz_id: str
    document_id: Optional[str] = None
    scope_type: str = "DOCUMENT"
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    source_document_count: int = 1
    title: str
    difficulty: str
    question_count: int
    chunks_used: int
    coverage_mode: str = "balanced"
    quiz_mode: str = "generated"
    prompt_version: str = "legacy_phase2"
    created_at: datetime

    model_config = {"from_attributes": True}


class QuizListResponse(BaseModel):
    items: List[QuizSummary]
    page: int
    page_size: int
    total: int


class QuizDetail(QuizSummary):
    questions: List[Question]
    configuration: Optional[Any] = None
