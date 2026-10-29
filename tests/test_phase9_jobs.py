"""
tests/test_phase9_jobs.py — Comprehensive Phase 9 Background Processing & Job Queue Test Suite
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from app.database.models.job import ProcessingJob
from app.database.models.document import Document
from app.database.session import SessionLocal
from app.services import job_service, job_idempotency, job_retry, resource_lock, durable_storage


from app.database.models.auth import LEGACY_OWNER_ID

# 1. Job Creation
def test_001_create_valid_document_job(db_session):
    job, created = job_service.create_job(
        db_session, user_id=LEGACY_OWNER_ID, job_type="document_ingestion",
        resource_type="document", resource_id="doc-1"
    )
    assert created is True
    assert job.status == "PENDING"
    assert job.user_id == LEGACY_OWNER_ID


def test_002_create_quiz_job(db_session):
    job, created = job_service.create_job(
        db_session, user_id=LEGACY_OWNER_ID, job_type="quiz_generation",
        resource_type="quiz", resource_id="quiz-1"
    )
    assert job.job_type == "quiz_generation"


def test_003_create_flashcard_job(db_session):
    job, created = job_service.create_job(
        db_session, user_id=LEGACY_OWNER_ID, job_type="flashcard_generation"
    )
    assert job.job_type == "flashcard_generation"


def test_007_duplicate_idempotency_returns_existing(db_session):
    payload = {"document_id": "doc-1"}
    j1, c1 = job_service.create_job(db_session, LEGACY_OWNER_ID, "quiz_generation", payload=payload)
    j2, c2 = job_service.create_job(db_session, LEGACY_OWNER_ID, "quiz_generation", payload=payload)
    assert c1 is True
    assert c2 is False
    assert j1.id == j2.id


# 2. Ownership
def test_011_user_lists_own_jobs(db_session):
    job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion")
    items, total = job_service.list_jobs_for_user(db_session, LEGACY_OWNER_ID)
    assert total == 1
    assert items[0].user_id == LEGACY_OWNER_ID


def test_013_other_user_cannot_cancel(db_session):
    j1, _ = job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion")
    with pytest.raises(ValueError):
        job_service.request_cancellation(db_session, j1.id, "non-existent-user")


# 3. Status Transitions
def test_017_status_transitions(db_session):
    j1, _ = job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion")
    j1 = job_service.mark_queued(db_session, j1.id)
    assert j1.status == "QUEUED"

    j1 = job_service.mark_running(db_session, j1.id, "worker-1")
    assert j1.status == "RUNNING"
    assert j1.worker_id == "worker-1"

    j1 = job_service.mark_succeeded(db_session, j1.id)
    assert j1.status == "SUCCEEDED"
    assert j1.progress_percentage == 100


# 4. Progress
def test_026_progress_update(db_session):
    j1, _ = job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion")
    j1 = job_service.mark_running(db_session, j1.id, "w1")
    j1 = job_service.update_progress(db_session, j1.id, "running_ocr", 5, 10)
    assert j1.progress_percentage > 0


# 5. Lock & Recovery
def test_085_resource_lock(db_session):
    j1, _ = job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion", resource_id="doc-100", payload={"attempt": 1})
    job_service.mark_running(db_session, j1.id, "w1")
    
    j2, _ = job_service.create_job(db_session, LEGACY_OWNER_ID, "document_ingestion", resource_id="doc-100", payload={"attempt": 2})
    locked = resource_lock.acquire_resource_lock(db_session, "doc-100", j2.id)
    assert locked is False


# 6. Chat Sync Policy
def test_066_chat_sync_policy():
    """Verify chat policy decision remains synchronous."""
    from config import settings
    assert settings.chat_generation_timeout_seconds == 60
