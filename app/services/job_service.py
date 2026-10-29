"""
services/job_service.py — Phase 9 Persistent Processing Job State Machine and Service

Manages database job records, state transitions, progress updates, cancellation,
and queries with strict user ownership isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from app.database.models.job import ProcessingJob
from app.database.job_status import PROCESSING_JOB_STATUSES
from app.database.types import utc_now
from app.services.job_idempotency import compute_idempotency_key
from app.services.job_progress import get_stage_label, calculate_progress
from app.services.job_retry import evaluate_job_retry, DEFAULT_MAX_ATTEMPTS

logger = logging.getLogger(__name__)

VALID_STATUSES = set(PROCESSING_JOB_STATUSES)

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _utcnow() -> datetime:
    return utc_now()


def create_job(
    db: Session,
    user_id: str,
    job_type: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    client_idempotency_key: Optional[str] = None,
    priority: int = 0,
    parent_job_id: Optional[str] = None,
) -> Tuple[ProcessingJob, bool]:
    """
    Create a new PENDING job record or return an existing job matching idempotency.

    Returns (job, created_new) tuple.
    """
    idempotency_key = compute_idempotency_key(
        user_id=user_id,
        job_type=job_type,
        resource_id=resource_id,
        payload=payload,
        client_key=client_idempotency_key,
    )

    # Check for existing job with same idempotency key
    stmt = select(ProcessingJob).where(
        ProcessingJob.user_id == user_id,
        ProcessingJob.idempotency_key == idempotency_key,
    ).order_by(ProcessingJob.created_at.desc())
    existing = db.scalars(stmt).first()

    if existing:
        if existing.status not in ("FAILED", "CANCELLED", "TIMED_OUT"):
            logger.info("Reusing existing job_id=%s status=%s for idempotency_key=%s", existing.id, existing.status, idempotency_key)
            return existing, False

    job_id = str(uuid4())
    max_attempts = DEFAULT_MAX_ATTEMPTS.get(job_type, 3)

    job = ProcessingJob(
        id=job_id,
        user_id=user_id,
        job_type=job_type,
        resource_type=resource_type,
        resource_id=resource_id,
        status="PENDING",
        stage="pending",
        progress_percentage=0,
        attempt_count=0,
        max_attempts=max_attempts,
        priority=priority,
        idempotency_key=idempotency_key,
        payload_json=payload if payload else None,
        parent_job_id=parent_job_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Created PENDING job_id=%s type=%s user_id=%s", job.id, job.job_type, user_id)
    return job, True


def mark_queued(db: Session, job_id: str, queue_name: str = "default") -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    job.status = "QUEUED"
    job.stage = "queued"
    job.queue_name = queue_name
    job.queued_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def mark_running(db: Session, job_id: str, worker_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    if job.status == "CANCEL_REQUESTED":
        job.status = "CANCELLED"
        job.cancelled_at = _utcnow()
        job.updated_at = _utcnow()
        db.commit()
        db.refresh(job)
        return job

    job.status = "RUNNING"
    job.worker_id = worker_id
    job.attempt_count += 1
    if not job.started_at:
        job.started_at = _utcnow()
    job.heartbeat_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def update_progress(
    db: Session,
    job_id: str,
    stage_key: str,
    completed_subunits: int = 0,
    total_subunits: int = 0,
) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    job.stage = stage_key
    progress = calculate_progress(job.job_type, stage_key, completed_subunits, total_subunits)
    # Ensure progress doesn't decrease unless reset during retry
    if progress > job.progress_percentage or job.status == "RETRYING":
        job.progress_percentage = min(100, max(0, progress))
    job.heartbeat_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def check_cancellation(db: Session, job_id: str) -> bool:
    """Check if cancellation was requested for a job."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        return False
    return job.status in ("CANCEL_REQUESTED", "CANCELLED")


def mark_succeeded(
    db: Session,
    job_id: str,
    result_type: Optional[str] = None,
    result_id: Optional[str] = None,
) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    job.status = "SUCCEEDED"
    job.stage = "finalizing"
    job.progress_percentage = 100
    job.result_reference_type = result_type or job.resource_type
    job.result_reference_id = result_id or job.resource_id
    job.completed_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def mark_failed(
    db: Session,
    job_id: str,
    error_code: str,
    error_message: str,
) -> Tuple[ProcessingJob, bool]:
    """
    Mark job failed or transition to RETRYING based on retry policy.
    Returns (job, scheduled_retry) tuple.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    should_retry, next_retry_at = evaluate_job_retry(
        job_type=job.job_type,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        error_code=error_code,
    )

    if should_retry and next_retry_at:
        job.status = "RETRYING"
        job.error_code = error_code
        job.error_message = f"[Attempt {job.attempt_count}/{job.max_attempts}] {error_message}"
        job.next_retry_at = next_retry_at
        job.updated_at = _utcnow()
        db.commit()
        db.refresh(job)
        logger.info("Scheduled retry for job_id=%s attempt=%d at %s", job.id, job.attempt_count, next_retry_at)
        return job, True
    else:
        job.status = "FAILED"
        job.error_code = error_code
        job.error_message = error_message
        job.failed_at = _utcnow()
        job.updated_at = _utcnow()
        db.commit()
        db.refresh(job)
        logger.warning("Job_id=%s failed permanently: [%s] %s", job.id, error_code, error_message)
        return job, False


def mark_cancelled(db: Session, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    job.status = "CANCELLED"
    job.cancelled_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def request_cancellation(db: Session, job_id: str, user_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job or job.user_id != user_id:
        raise ValueError("Job not found or ownership mismatch")
    if job.status in TERMINAL_STATUSES:
        return job

    if job.status in ("PENDING", "QUEUED"):
        job.status = "CANCELLED"
        job.cancelled_at = _utcnow()
    else:
        job.status = "CANCEL_REQUESTED"
        job.cancel_requested_at = _utcnow()

    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def get_job_for_user(db: Session, job_id: str, user_id: str) -> Optional[ProcessingJob]:
    stmt = select(ProcessingJob).where(
        ProcessingJob.id == job_id,
        ProcessingJob.user_id == user_id,
    )
    return db.scalars(stmt).first()


def list_jobs_for_user(
    db: Session,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    job_type_filter: Optional[str] = None,
) -> Tuple[List[ProcessingJob], int]:
    stmt = select(ProcessingJob).where(ProcessingJob.user_id == user_id)
    if status_filter:
        stmt = stmt.where(ProcessingJob.status == status_filter)
    if job_type_filter:
        stmt = stmt.where(ProcessingJob.job_type == job_type_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(ProcessingJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = list(db.scalars(stmt).all())

    return items, total
