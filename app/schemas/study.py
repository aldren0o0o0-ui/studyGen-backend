"""
app/schemas/study.py — Phase 4

Pydantic schemas for Phase 4 Smart Study and Review System.
Covers:
  - Topic performance & mastery
  - Question-type performance
  - Review items & spaced repetition
  - Review session creation, active view, submission & feedback
  - Study goals & progress
  - Study activities
  - Study recommendation & overview
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator

from app.schemas.quizzes import ActiveQuestion, QuestionSource


# ---------------------------------------------------------------------------
# Topic & Question-Type Performance
# ---------------------------------------------------------------------------

class TopicPerformanceSchema(BaseModel):
    id: str
    topic_label: str
    document_id: Optional[str] = None
    attempted_count: int
    correct_count: int
    incorrect_count: int
    accuracy_percentage: float
    mastery_score: float
    mastery_state: str  # insufficient_data | weak | developing | strong
    last_attempted_at: Optional[datetime] = None
    last_correct_at: Optional[datetime] = None
    last_incorrect_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TopicPerformanceListResponse(BaseModel):
    items: List[TopicPerformanceSchema]
    total: int


class QuestionTypePerformance(BaseModel):
    question_type: str  # multiple_choice | true_false | identification
    attempted_count: int
    correct_count: int
    incorrect_count: int
    accuracy_percentage: float
    last_attempted_at: Optional[datetime] = None


class QuestionTypePerformanceListResponse(BaseModel):
    items: List[QuestionTypePerformance]


# ---------------------------------------------------------------------------
# Review Item
# ---------------------------------------------------------------------------

class ReviewItemSchema(BaseModel):
    id: str
    question_id: str
    document_id: Optional[str] = None
    source_attempt_id: Optional[str] = None
    topic_label: Optional[str] = None
    question_type: str
    difficulty: str
    review_status: str  # DUE | UPCOMING | MASTERED | SUSPENDED
    priority_score: float
    incorrect_count: int
    correct_review_count: int
    consecutive_correct: int
    last_reviewed_at: Optional[datetime] = None
    next_review_at: datetime
    interval_days: int
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReviewItemListResponse(BaseModel):
    items: List[ReviewItemSchema]
    total: int


# ---------------------------------------------------------------------------
# Review Session
# ---------------------------------------------------------------------------

class CreateReviewSessionRequest(BaseModel):
    session_type: str = Field(description="incorrect_answers | weak_topics | due_review | custom")
    document_id: Optional[str] = None
    topic_label: Optional[str] = None
    question_type: Optional[str] = None
    difficulty: Optional[str] = None
    question_count: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_session_type(self) -> "CreateReviewSessionRequest":
        valid = {"incorrect_answers", "weak_topics", "due_review", "custom"}
        if self.session_type not in valid:
            raise ValueError(f"Invalid session_type '{self.session_type}'. Valid options: {sorted(valid)}")
        return self


class ActiveReviewQuestion(BaseModel):
    """
    Active question payload for review session — SAFE.
    NEVER exposes correct_answer, accepted_answers, or explanation.
    """
    question_id: str
    question_type: str
    question: str
    options: List[str]
    difficulty: str
    topic: str
    position: int


class ActiveReviewSessionResponse(BaseModel):
    """Returned when a review session is started/retrieved before completion."""
    session_id: str
    session_type: str
    document_id: Optional[str] = None
    topic_label: Optional[str] = None
    status: str
    question_count: int
    completed_count: int
    started_at: datetime
    questions: List[ActiveReviewQuestion]


class ReviewAnswerSubmission(BaseModel):
    question_id: str
    selected_answer: str


class ReviewSubmitRequest(BaseModel):
    answers: List[ReviewAnswerSubmission] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> "ReviewSubmitRequest":
        qids = [a.question_id for a in self.answers]
        if len(qids) != len(set(qids)):
            raise ValueError("Duplicate question_id in review submission answers.")
        return self


class ReviewAnswerFeedback(BaseModel):
    question_id: str
    question_text: str
    question_type: str
    selected_answer: str
    correct_answer: str
    accepted_answers: List[str] = []
    is_correct: bool
    explanation: Optional[str] = None
    difficulty: str = "medium"
    topic: Optional[str] = None
    next_review_at: Optional[datetime] = None
    interval_days: Optional[int] = None
    sources: List[QuestionSource] = []


class ReviewSessionFeedbackResponse(BaseModel):
    session_id: str
    session_type: str
    status: str
    question_count: int
    correct_count: int
    completed_count: int
    percentage: float
    started_at: datetime
    completed_at: Optional[datetime] = None
    answers: List[ReviewAnswerFeedback]


class ReviewSessionSummary(BaseModel):
    session_id: str
    session_type: str
    document_id: Optional[str] = None
    topic_label: Optional[str] = None
    status: str
    question_count: int
    correct_count: int
    completed_count: int
    started_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReviewSessionListResponse(BaseModel):
    items: List[ReviewSessionSummary]
    page: int
    page_size: int
    total: int


# ---------------------------------------------------------------------------
# Study Goals
# ---------------------------------------------------------------------------

class CreateStudyGoalRequest(BaseModel):
    goal_type: str = Field(description="questions_answered | review_items_completed | study_sessions_completed")
    target_value: int = Field(ge=1)
    goal_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class UpdateStudyGoalRequest(BaseModel):
    target_value: Optional[int] = Field(default=None, ge=1)
    status: Optional[str] = Field(default=None, pattern=r"^(active|completed|expired)$")


class StudyGoalSchema(BaseModel):
    id: str
    goal_type: str
    target_value: int
    current_value: int
    goal_date: str
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class StudyGoalListResponse(BaseModel):
    items: List[StudyGoalSchema]


# ---------------------------------------------------------------------------
# Study Activities
# ---------------------------------------------------------------------------

class StudyActivitySchema(BaseModel):
    id: str
    activity_type: str
    related_document_id: Optional[str] = None
    related_quiz_id: Optional[str] = None
    related_attempt_id: Optional[str] = None
    related_review_session_id: Optional[str] = None
    item_count: int
    duration_seconds: Optional[int] = None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class StudyActivityListResponse(BaseModel):
    items: List[StudyActivitySchema]
    page: int
    page_size: int
    total: int


# ---------------------------------------------------------------------------
# Recommendation & Overview
# ---------------------------------------------------------------------------

class StudyRecommendationSchema(BaseModel):
    recommendation_type: str
    title: str
    reason: str
    document_id: Optional[str] = None
    topic_label: Optional[str] = None
    due_item_count: int
    suggested_question_count: int


class StudyOverviewResponse(BaseModel):
    quizzes_completed: int
    review_sessions_completed: int
    questions_answered: int
    overall_accuracy: float
    due_review_count: int
    weak_topic_count: int
    recommendation: StudyRecommendationSchema
    daily_goals: List[StudyGoalSchema]
