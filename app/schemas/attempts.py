"""
app/schemas/attempts.py — Phase 3
Attempt submission and result schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.quizzes import QuestionSource


class AnswerSubmission(BaseModel):
    question_id: str
    selected_answer: str


class AttemptRequest(BaseModel):
    answers: List[AnswerSubmission] = Field(min_length=1)
    started_at: Optional[datetime] = None

    @model_validator(mode="after")
    def no_duplicate_question_ids(self) -> "AttemptRequest":
        ids = [a.question_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question_id values in answers list.")
        return self


class AttemptAnswerDetail(BaseModel):
    question_id: str
    question_text: str
    question_type: str = "multiple_choice"
    selected_answer: str
    correct_answer: str
    accepted_answers: List[str] = []
    is_correct: bool
    explanation: Optional[str] = None
    difficulty: str = "medium"
    topic: Optional[str] = None
    sources: List[QuestionSource] = []


class AttemptResponse(BaseModel):
    attempt_id: str
    quiz_id: str
    score: int
    total_questions: int
    percentage: float
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    answers: List[AttemptAnswerDetail] = []

    model_config = {"from_attributes": True}


class AttemptSummary(BaseModel):
    attempt_id: str
    quiz_id: str
    score: int
    total_questions: int
    percentage: float
    created_at: datetime

    model_config = {"from_attributes": True}


class AttemptListResponse(BaseModel):
    items: List[AttemptSummary]
    page: int
    page_size: int
    total: int
