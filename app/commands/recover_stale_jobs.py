"""
app/commands/recover_stale_jobs.py — Phase 9 Stale Job Recovery Command

Scans for jobs stuck in RUNNING state without a heartbeat beyond JOB_STALE_AFTER_SECONDS.
Either requeues retryable jobs or marks them FAILED.

Usage:
  python -m app.commands.recover_stale_jobs           # Dry run
  python -m app.commands.recover_stale_jobs --apply   # Apply changes
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging
import sys

from sqlalchemy import select, and_

from app.database.models.job import ProcessingJob, WorkerHeartbeat
from app.database.session import SessionLocal
from app.services.job_service import mark_failed, mark_queued
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recover_stale_jobs")


def recover_stale_jobs(apply: bool = False) -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=settings.job_stale_after_seconds)

        stmt = select(ProcessingJob).where(
            ProcessingJob.status == "RUNNING",
            and_(
                ProcessingJob.heartbeat_at < cutoff,
                ProcessingJob.started_at < cutoff,
            )
        )
        stale_jobs = list(db.scalars(stmt).all())
        logger.info("Found %d stale RUNNING jobs (no heartbeat since %s).", len(stale_jobs), cutoff)

        recovered_count = 0
        for job in stale_jobs:
            logger.warning(
                "Stale job detected: id=%s type=%s user_id=%s attempt=%d/%d heartbeat_at=%s",
                job.id, job.job_type, job.user_id, job.attempt_count, job.max_attempts, job.heartbeat_at
            )
            if apply:
                if job.attempt_count < job.max_attempts:
                    mark_queued(db, job.id)
                    logger.info("Requeued stale job %s", job.id)
                else:
                    mark_failed(db, job.id, "WORKER_STALE", "Worker heartbeat expired and max attempts reached.")
                    logger.info("Marked stale job %s FAILED", job.id)
            recovered_count += 1

        return recovered_count
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recover stale jobs")
    parser.add_argument("--apply", action="store_true", help="Apply changes instead of dry run")
    args = parser.parse_args()

    count = recover_stale_jobs(apply=args.apply)
    print(f"Stale job recovery complete. Processed {count} jobs (apply={args.apply}).")
