"""
workers/quiz_worker.py — Phase 9 Quiz Generation Worker Task
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4

from app.database.models.quiz import Question as QuestionModel, Quiz as QuizModel
from app.database.session import SessionLocal
from app.ownership_context import set_ownership_context
from app.schemas.quizzes import GenerateQuizRequest
from app.services import document_service, job_service
from app.services.context_selection import select_context_chunks
from app.services.quiz_generation import generate_quiz
from app.services.question_validation import QuestionValidator
from app.services.resource_lock import acquire_resource_lock

logger = logging.getLogger(__name__)


async def run_quiz_generation_task(ctx: dict, job_id: str) -> str:
    worker_id = ctx.get("worker_id", "worker-ai-1")
    db = SessionLocal()
    try:
        job = job_service.mark_running(db, job_id, worker_id)
        if job.status == "CANCELLED":
            return "CANCELLED"

        user_id = job.user_id
        set_ownership_context(user_id)
        payload = job.payload_json or {}

        doc_id = payload.get("document_id")
        doc = document_service.get_by_id(db, doc_id)
        if not doc or doc.status != "READY":
            job_service.mark_failed(db, job_id, "DOCUMENT_NOT_READY", "Source document is missing or not ready.")
            return "DOCUMENT_NOT_READY"

        req = GenerateQuizRequest(**payload)

        job_service.update_progress(db, job_id, "selecting_context")
        collection = ctx.get("collection")
        gemini_model = ctx.get("gemini_model")
        settings = ctx.get("settings")

        context_chunks = select_context_chunks(
            collection, doc_id, user_id=user_id,
            question_count=req.question_count,
            coverage_mode=req.coverage_mode,
            topic_query=req.topic_query,
            selected_chunk_indexes=req.selected_chunk_indexes,
        )

        if not context_chunks:
            job_service.mark_failed(db, job_id, "NO_CONTEXT", "No index chunks found for document.")
            return "NO_CONTEXT"

        if job_service.check_cancellation(db, job_id):
            job_service.mark_cancelled(db, job_id)
            return "CANCELLED"

        job_service.update_progress(db, job_id, "generating_questions")
        gen_result = generate_quiz(
            gemini_model, context_chunks,
            question_count=req.question_count,
            difficulty=req.difficulty,
            question_types=req.question_types,
            include_explanations=req.include_explanations,
            include_sources=req.include_sources,
        )

        job_service.update_progress(db, job_id, "validating_questions")
        validator = QuestionValidator(min_questions=settings.min_question_count, max_questions=settings.max_question_count)
        valid_result = validator.validate_quiz(gen_result, context_chunks)

        if not valid_result.questions:
            job_service.mark_failed(db, job_id, "VALIDATION_FAILED", "Generated questions failed quality validation.")
            return "VALIDATION_FAILED"

        if job_service.check_cancellation(db, job_id):
            job_service.mark_cancelled(db, job_id)
            return "CANCELLED"

        job_service.update_progress(db, job_id, "saving_quiz")
        quiz_id = str(uuid4())
        quiz_db = QuizModel(
            id=quiz_id,
            user_id=user_id,
            document_id=doc_id,
            quiz_title=valid_result.quiz_title,
            difficulty=req.difficulty,
            coverage_mode=req.coverage_mode,
            quiz_mode="active",
            prompt_version="3.0",
        )
        db.add(quiz_db)

        for idx, vq in enumerate(valid_result.questions):
            q_db = QuestionModel(
                id=str(uuid4()),
                quiz_id=quiz_id,
                question_type=vq.question_type,
                question_text=vq.question,
                options_json=vq.options,
                correct_answer=vq.correct_answer,
                accepted_answers_json=vq.accepted_answers,
                explanation=vq.explanation,
                difficulty=vq.difficulty,
                topic_label=vq.topic,
                source_references_json=[s.model_dump() for s in vq.sources],
                source_chunk_indexes_json=vq.source_chunk_indexes,
                position=idx + 1,
            )
            db.add(q_db)

        db.commit()

        job_service.mark_succeeded(db, job_id, result_type="quiz", result_id=quiz_id)
        logger.info("Quiz generation job %s succeeded -> quiz_id=%s", job_id, quiz_id)
        return "SUCCEEDED"

    except Exception as exc:
        logger.error("Quiz generation worker failed for job_id=%s: %s", job_id, exc, exc_info=True)
        job_service.mark_failed(db, job_id, "QUIZ_GEN_ERROR", str(exc))
        return "FAILED"
    finally:
        db.close()
