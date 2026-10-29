"""
workers/tasks.py — Phase 9 Central Job Task Dispatcher Registry

Provides safe function invocation registry mapping job types to concrete tasks.
Prevents arbitrary code execution by restricting jobs to explicit registered callables.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

from app.workers.document_worker import (
    run_document_ingestion_task,
    run_document_reprocessing_task,
)
from app.workers.quiz_worker import run_quiz_generation_task
from app.workers.flashcard_worker import run_flashcard_generation_task

logger = logging.getLogger(__name__)

# Registry mapping job_type strings to async task callables (§10)
JOB_TASK_REGISTRY: Dict[str, Callable] = {
    "document_ingestion": run_document_ingestion_task,
    "document_reprocessing": run_document_reprocessing_task,
    "quiz_generation": run_quiz_generation_task,
    "flashcard_generation": run_flashcard_generation_task,
}


async def execute_job_task(ctx: dict, job_id: str, job_type: str) -> str:
    """
    Safely dispatch a job to its registered handler function.
    Raises ValueError if job_type is not registered.
    """
    handler = JOB_TASK_REGISTRY.get(job_type)
    if not handler:
        logger.error("Attempted invocation of unregistered job type: %s", job_type)
        raise ValueError(f"Unregistered job_type '{job_type}'")

    return await handler(ctx, job_id)
