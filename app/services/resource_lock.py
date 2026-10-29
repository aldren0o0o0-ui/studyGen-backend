"""
services/resource_lock.py — Phase 9 Database-backed Resource Locking

Prevents conflicting concurrent operations on exclusive resources (e.g. concurrent document reprocessing or duplicate generation).
Uses database state verification and active job checks.
"""
from __future__ import annotations

import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.job import ProcessingJob
from app.database.job_status import ACTIVE_PROCESSING_JOB_STATUSES

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = set(ACTIVE_PROCESSING_JOB_STATUSES)


def acquire_resource_lock(
    db: Session,
    resource_id: str,
    job_id: str,
) -> bool:
    """
    Attempt to acquire exclusive resource lock for a job.
    Returns True if no other job is active on resource_id.
    """
    stmt = select(ProcessingJob).where(
        ProcessingJob.resource_id == resource_id,
        ProcessingJob.status.in_(ACTIVE_STATUSES),
        ProcessingJob.id != job_id,
    )
    existing_active = db.scalars(stmt).first()
    if existing_active:
        logger.warning(
            "Resource lock conflict for resource_id=%s. Active job_id=%s status=%s",
            resource_id, existing_active.id, existing_active.status,
        )
        return False
    return True


def check_active_job_for_resource(
    db: Session,
    resource_id: str,
) -> Optional[ProcessingJob]:
    """
    Find any currently active job for a given resource_id.
    """
    stmt = select(ProcessingJob).where(
        ProcessingJob.resource_id == resource_id,
        ProcessingJob.status.in_(ACTIVE_STATUSES),
    )
    return db.scalars(stmt).first()
