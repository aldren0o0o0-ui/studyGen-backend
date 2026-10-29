"""
routers/study.py — Phase 4

Analytics, recommendation, goals, activities, topics, and question-type endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, func

from app.database.models.attempt import AttemptAnswer
from app.database.models.quiz import Question
from app.database.session import SessionLocal
from app.schemas.study import (
    CreateStudyGoalRequest,
    QuestionTypePerformance,
    QuestionTypePerformanceListResponse,
    StudyActivityListResponse,
    StudyActivitySchema,
    StudyGoalListResponse,
    StudyGoalSchema,
    StudyOverviewResponse,
    StudyRecommendationSchema,
    TopicPerformanceListResponse,
    TopicPerformanceSchema,
    UpdateStudyGoalRequest,
)
from app.services import (
    study_activity_service,
    study_goal_service,
    study_recommendation_service,
    topic_performance_service,
)
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/study", tags=["study"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# GET /study/overview
# ---------------------------------------------------------------------------

@router.get("/overview", response_model=StudyOverviewResponse)
async def get_study_overview():
    def _query():
        db = SessionLocal()
        try:
            return study_recommendation_service.get_study_overview(db)
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# GET /study/recommendation
# ---------------------------------------------------------------------------

@router.get("/recommendation", response_model=StudyRecommendationSchema)
async def get_study_recommendation():
    def _query():
        db = SessionLocal()
        try:
            return study_recommendation_service.get_next_study_recommendation(db)
        finally:
            db.close()

    return await run_in_threadpool(_query)


# ---------------------------------------------------------------------------
# GET /study/topics
# ---------------------------------------------------------------------------

@router.get("/topics", response_model=TopicPerformanceListResponse)
async def list_topics(
    document_id: Optional[str] = Query(default=None),
    mastery_state: Optional[str] = Query(default=None),
    sort_by: str = Query(default="weakest", pattern="^(weakest|strongest|recent)$"),
    limit: int = Query(default=100, ge=1, le=500),
):
    def _query():
        db = SessionLocal()
        try:
            items = topic_performance_service.list_topic_performance(
                db,
                document_id=document_id,
                mastery_state=mastery_state,
                sort_by=sort_by,
                limit=limit,
            )
            schemas = [TopicPerformanceSchema.model_validate(t) for t in items]
            return schemas, len(schemas)
        finally:
            db.close()

    items, total = await run_in_threadpool(_query)
    return TopicPerformanceListResponse(items=items, total=total)


# ---------------------------------------------------------------------------
# GET /study/question-types
# ---------------------------------------------------------------------------

@router.get("/question-types", response_model=QuestionTypePerformanceListResponse)
async def get_question_type_performance(document_id: Optional[str] = Query(default=None)):
    """
    Calculate performance metrics grouped by question_type directly from attempt answers.
    Supported types: multiple_choice, true_false, identification.
    """
    def _query():
        db = SessionLocal()
        try:
            results = []
            for qtype in ["multiple_choice", "true_false", "identification"]:
                stmt = (
                    select(AttemptAnswer)
                    .join(AttemptAnswer.question)
                    .where(Question.question_type == qtype)
                )
                if document_id:
                    stmt = stmt.join(Question.quiz).where(Question.quiz.has(document_id=document_id))

                answers = list(db.execute(stmt).scalars().all())
                attempted = len(answers)
                correct = sum(1 for a in answers if a.is_correct)
                incorrect = attempted - correct
                accuracy = round((correct / attempted) * 100, 1) if attempted > 0 else 0.0

                last_dt = max((a.answered_at for a in answers if a.answered_at), default=None)

                results.append(
                    QuestionTypePerformance(
                        question_type=qtype,
                        attempted_count=attempted,
                        correct_count=correct,
                        incorrect_count=incorrect,
                        accuracy_percentage=accuracy,
                        last_attempted_at=last_dt,
                    )
                )
            return results
        finally:
            db.close()

    items = await run_in_threadpool(_query)
    return QuestionTypePerformanceListResponse(items=items)


# ---------------------------------------------------------------------------
# GET & POST & PATCH /study/goals
# ---------------------------------------------------------------------------

@router.get("/goals", response_model=StudyGoalListResponse)
async def list_goals(goal_date: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    def _query():
        db = SessionLocal()
        try:
            items = study_goal_service.get_goals(db, goal_date=goal_date)
            return [StudyGoalSchema.model_validate(g) for g in items]
        finally:
            db.close()

    goals = await run_in_threadpool(_query)
    return StudyGoalListResponse(items=goals)


@router.post("/goals", response_model=StudyGoalSchema, status_code=201)
async def create_goal(body: CreateStudyGoalRequest):
    def _create():
        db = SessionLocal()
        try:
            goal = study_goal_service.create_or_update_goal(
                db,
                goal_type=body.goal_type,
                target_value=body.target_value,
                goal_date=body.goal_date,
            )
            return StudyGoalSchema.model_validate(goal)
        finally:
            db.close()

    return await run_in_threadpool(_create)


@router.patch("/goals/{goal_id}", response_model=StudyGoalSchema)
async def update_goal(goal_id: str, body: UpdateStudyGoalRequest):
    def _update():
        db = SessionLocal()
        try:
            goal = study_goal_service.get_goal_by_id(db, goal_id)
            if not goal:
                raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found.")
            
            if body.target_value is not None:
                goal.target_value = body.target_value
                if goal.current_value >= goal.target_value:
                    goal.status = "completed"
            if body.status is not None:
                goal.status = body.status

            db.commit()
            db.refresh(goal)
            return StudyGoalSchema.model_validate(goal)
        finally:
            db.close()

    return await run_in_threadpool(_update)


# ---------------------------------------------------------------------------
# GET /study/activity
# ---------------------------------------------------------------------------

@router.get("/activity", response_model=StudyActivityListResponse)
async def list_activity(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = study_activity_service.list_activities(db, page=page, page_size=page_size)
            schemas = [StudyActivitySchema.model_validate(a) for a in items]
            return schemas, total
        finally:
            db.close()

    items, total = await run_in_threadpool(_query)
    return StudyActivityListResponse(items=items, page=page, page_size=page_size, total=total)
