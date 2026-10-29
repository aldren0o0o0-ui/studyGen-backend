"""
database/models/job.py — Phase 9 Processing Jobs and Worker Heartbeats
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

DateTime = UTC_DATETIME


_utcnow = utc_now


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    progress_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    queue_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    result_reference_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    result_reference_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Validated config only — no tokens, passwords, or secrets
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON_VALUE, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)
    queued_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)

    # Retry chain linking
    parent_job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_jobs_user_id", "user_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_resource", "resource_id", "status"),
        Index("ix_jobs_idempotency", "idempotency_key"),
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_next_retry", "next_retry_at"),
        Index("ix_jobs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ProcessingJob id={self.id} type={self.job_type} status={self.status}>"


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    queue_name: Mapped[str] = mapped_column(String(40), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    process_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=_utcnow, nullable=False)
    current_job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="STARTING", nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="9.0.0", nullable=False)

    __table_args__ = (
        Index("ix_heartbeats_queue", "queue_name"),
        Index("ix_heartbeats_status", "status"),
        Index("ix_heartbeats_last_heartbeat", "last_heartbeat_at"),
    )

    def __repr__(self) -> str:
        return f"<WorkerHeartbeat worker_id={self.worker_id} status={self.status}>"
