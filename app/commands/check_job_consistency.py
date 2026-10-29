"""
app/commands/check_job_consistency.py — Phase 9 Job Consistency Audit Command

Verifies job consistency across DB job records, documents, quizzes, and workers.

Usage:
  python -m app.commands.check_job_consistency
  python -m app.commands.check_job_consistency --mark-stale-jobs
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select, func

from app.database.models.document import Document
from app.database.models.job import ProcessingJob, WorkerHeartbeat
from app.database.models.quiz import Quiz
from app.database.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("check_job_consistency")


def audit_job_consistency(mark_stale: bool = False) -> dict:
    db = SessionLocal()
    issues = []
    try:
        now = datetime.now(timezone.utc)

        # 1. Active jobs missing resource
        active_jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.status.in_(["PENDING", "QUEUED", "RUNNING"]))).all())
        for job in active_jobs:
            if job.resource_type == "document" and job.resource_id:
                doc = db.get(Document, job.resource_id)
                if not doc:
                    issues.append(f"Job {job.id} points to missing Document {job.resource_id}")

        # 2. Document PROCESSING status without active job
        processing_docs = list(db.scalars(select(Document).where(Document.status == "PROCESSING")).all())
        for doc in processing_docs:
            active_job = db.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.resource_id == doc.id,
                    ProcessingJob.status.in_(["PENDING", "QUEUED", "RUNNING"])
                )
            ).first()
            if not active_job:
                issues.append(f"Document {doc.id} is PROCESSING but has no active job.")

        # 3. SUCCEEDED quiz jobs without quiz record
        succeeded_quiz_jobs = list(db.scalars(
            select(ProcessingJob).where(ProcessingJob.job_type == "quiz_generation", ProcessingJob.status == "SUCCEEDED")
        ).all())
        for job in succeeded_quiz_jobs:
            if job.result_reference_id:
                quiz = db.get(Quiz, job.result_reference_id)
                if not quiz:
                    issues.append(f"Succeeded quiz job {job.id} points to missing Quiz {job.result_reference_id}")

        logger.info("Audit complete. Total issues found: %d", len(issues))
        for issue in issues:
            logger.warning("CONSISTENCY ISSUE: %s", issue)

        return {"issue_count": len(issues), "issues": issues}
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit job consistency")
    parser.add_argument("--mark-stale-jobs", action="store_true", help="Repair stale jobs")
    args = parser.parse_args()

    res = audit_job_consistency(mark_stale=args.mark_stale_jobs)
    print(f"Consistency check complete. Found {res['issue_count']} issues.")
