"""
routers/jobs.py — Phase 9 Job APIs

Provides user-scoped endpoints to list, retrieve status, cancel, and retry jobs.
Enforces strict authentication and user isolation.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool

from app.database.session import SessionLocal
from app.dependencies import CurrentUser, get_current_user
from app.schemas.jobs import (
    JobAcceptedResponse,
    JobDetail,
    JobListResponse,
    JobResourceRef,
    JobSummary,
    WorkerHealthSummary,
)
from app.services import job_service
from app.services.job_progress import get_stage_label

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


def _to_summary(job) -> JobSummary:
    return JobSummary(
        id=job.id,
        job_type=job.job_type,
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        status=job.status,
        stage=job.stage,
        stage_label=get_stage_label(job.job_type, job.stage or ""),
        progress_percentage=job.progress_percentage,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
    )


def _to_detail(job) -> JobDetail:
    summary = _to_summary(job)
    return JobDetail(
        **summary.model_dump(),
        error_code=job.error_code,
        error_message=job.error_message,
        result_reference_type=job.result_reference_type,
        result_reference_id=job.result_reference_id,
        started_at=job.started_at,
        cancel_requested_at=job.cancel_requested_at,
        cancelled_at=job.cancelled_at,
        next_retry_at=job.next_retry_at,
        parent_job_id=job.parent_job_id,
    )


@router.get("", response_model=JobListResponse)
async def list_user_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = Query(default=None),
    job_type: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            items, total = job_service.list_jobs_for_user(
                db, user_id=current_user.id,
                page=page, page_size=page_size,
                status_filter=status, job_type_filter=job_type,
            )
            return [_to_summary(j) for j in items], total
        finally:
            db.close()

    items, total = await run_in_threadpool(_query)
    return JobListResponse(items=items, page=page, page_size=page_size, total=total)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _query():
        db = SessionLocal()
        try:
            job = job_service.get_job_for_user(db, job_id, current_user.id)
            if not job:
                raise HTTPException(404, f"Job '{job_id}' not found.")
            return _to_detail(job)
        finally:
            db.close()

    return await run_in_threadpool(_query)


@router.post("/{job_id}/cancel", response_model=JobDetail)
async def cancel_job(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _cancel():
        db = SessionLocal()
        try:
            try:
                job = job_service.request_cancellation(db, job_id, current_user.id)
            except ValueError:
                raise HTTPException(404, f"Job '{job_id}' not found.")
            return _to_detail(job)
        finally:
            db.close()

    return await run_in_threadpool(_cancel)


@router.post("/{job_id}/retry", response_model=JobDetail)
async def retry_job(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    def _retry():
        db = SessionLocal()
        try:
            job = job_service.get_job_for_user(db, job_id, current_user.id)
            if not job:
                raise HTTPException(404, f"Job '{job_id}' not found.")

            if job.status not in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise HTTPException(400, f"Cannot retry job in status '{job.status}'.")

            # Create a linked retry job
            new_job, _ = job_service.create_job(
                db,
                user_id=current_user.id,
                job_type=job.job_type,
                resource_type=job.resource_type,
                resource_id=job.resource_id,
                payload=job.payload_json or None,
                parent_job_id=job.id,
            )
            return _to_detail(new_job)
        finally:
            db.close()

    return await run_in_threadpool(_retry)
