"""Canonical processing-job states shared by services and migrations."""
from enum import Enum


class ProcessingJobStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


PROCESSING_JOB_STATUSES = tuple(status.value for status in ProcessingJobStatus)
ACTIVE_PROCESSING_JOB_STATUSES = (
    ProcessingJobStatus.PENDING.value,
    ProcessingJobStatus.QUEUED.value,
    ProcessingJobStatus.RUNNING.value,
    ProcessingJobStatus.RETRYING.value,
)
