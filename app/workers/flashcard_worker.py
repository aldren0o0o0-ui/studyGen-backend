"""
workers/flashcard_worker.py — Phase 9 Flashcard Generation Worker Task
"""
from __future__ import annotations

import json
import logging

from app.database.session import SessionLocal
from app.ownership_context import set_ownership_context
from app.schemas.flashcards import GenerateFlashcardsRequest
from app.services import job_service, flashcard_service
from app.services.flashcard_generation_service import generate_flashcards_from_document

logger = logging.getLogger(__name__)


async def run_flashcard_generation_task(ctx: dict, job_id: str) -> str:
    worker_id = ctx.get("worker_id", "worker-ai-1")
    db = SessionLocal()
    try:
        job = job_service.mark_running(db, job_id, worker_id)
        if job.status == "CANCELLED":
            return "CANCELLED"

        user_id = job.user_id
        set_ownership_context(user_id)
        payload = job.payload_json or {}
        req = GenerateFlashcardsRequest(**payload)

        job_service.update_progress(db, job_id, "selecting_context")
        collection = ctx.get("collection")
        gemini_model = ctx.get("gemini_model")

        if job_service.check_cancellation(db, job_id):
            job_service.mark_cancelled(db, job_id)
            return "CANCELLED"

        job_service.update_progress(db, job_id, "generating_cards")
        study_set = generate_flashcards_from_document(
            db, collection, gemini_model, req
        )

        job_service.mark_succeeded(db, job_id, result_type="study_set", result_id=study_set.id)
        logger.info("Flashcard generation job %s succeeded -> study_set_id=%s", job_id, study_set.id)
        return "SUCCEEDED"

    except Exception as exc:
        logger.error("Flashcard generation worker failed for job_id=%s: %s", job_id, exc, exc_info=True)
        job_service.mark_failed(db, job_id, "FLASHCARD_GEN_ERROR", str(exc))
        return "FAILED"
    finally:
        db.close()
