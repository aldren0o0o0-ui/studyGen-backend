"""
schemas/jobs.py — Phase 9 Processing Job Schemas
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class JobResourceRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    id: str


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    status: str
    stage: Optional[str] = None
    stage_label: Optional[str] = None
    progress_percentage: int
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None


class JobDetail(JobSummary):
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    result_reference_type: Optional[str] = None
    result_reference_id: Optional[str] = None
    started_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    parent_job_id: Optional[str] = None


class JobListResponse(BaseModel):
    items: List[JobSummary]
    page: int
    page_size: int
    total: int


class CreateJobRequest(BaseModel):
    job_type: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None


class JobAcceptedResponse(BaseModel):
    job: JobSummary
    resource: Optional[JobResourceRef] = None


class WorkerHealthSummary(BaseModel):
    healthy_workers: int
    busy_workers: int
    stale_workers: int
    queues: Dict[str, int]
