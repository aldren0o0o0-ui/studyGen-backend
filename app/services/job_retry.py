"""
services/job_retry.py — Phase 9 Job Retry Policy and Backoff Calculation

Defines retry rules for background jobs:
- Retryable vs non-retryable error classification
- Exponential backoff with jitter calculation
- Attempt count checking against configurable limits
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Optional, Tuple


# Non-retryable error codes per Phase 9 spec (§16, §40)
NON_RETRYABLE_ERROR_CODES = {
    "UNSUPPORTED_FILE",
    "CORRUPTED_DOCUMENT",
    "PASSWORD_PROTECTED_PDF",
    "NO_USABLE_TEXT",
    "INVALID_GENERATION_REQUEST",
    "RESOURCE_DELETED",
    "OWNERSHIP_INVALID",
    "FILE_NOT_FOUND",
    "DOCUMENT_DELETED",
}

# Default retry limits per job type category
DEFAULT_MAX_ATTEMPTS = {
    "document_ingestion": 3,
    "document_reprocessing": 3,
    "quiz_generation": 3,
    "flashcard_generation": 3,
    "chat_generation": 3,
    "document_metadata_rebuild": 2,
}


def is_retryable_error(error_code: Optional[str]) -> bool:
    """Return True if the given error code can be retried."""
    if not error_code:
        return True  # Unknown exceptions default to retryable up to max_attempts
    return error_code not in NON_RETRYABLE_ERROR_CODES


def calculate_next_retry(
    attempt_count: int,
    base_delay_seconds: float = 5.0,
    max_delay_seconds: float = 300.0,
    jitter: bool = True,
) -> datetime:
    """
    Calculate the next_retry_at UTC timestamp using exponential backoff.
    Attempt 1 -> ~5s
    Attempt 2 -> ~10s
    Attempt 3 -> ~20s
    """
    delay = min(base_delay_seconds * (2 ** max(0, attempt_count - 1)), max_delay_seconds)
    if jitter:
        # Add +/- 20% random jitter
        delay = delay * random.uniform(0.8, 1.2)
    
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=delay)


def evaluate_job_retry(
    job_type: str,
    attempt_count: int,
    max_attempts: int,
    error_code: Optional[str],
) -> Tuple[bool, Optional[datetime]]:
    """
    Evaluate if a job should be retried after an error.
    
    Returns (should_retry, next_retry_at).
    """
    if attempt_count >= max_attempts:
        return False, None

    if not is_retryable_error(error_code):
        return False, None

    next_retry_at = calculate_next_retry(attempt_count)
    return True, next_retry_at
