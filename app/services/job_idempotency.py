"""
services/job_idempotency.py — Phase 9 Idempotency and Request Fingerprinting

Computes deterministic idempotency keys for background jobs and verifies
that duplicate creation requests safely return existing active or completed jobs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def compute_idempotency_key(
    user_id: str,
    job_type: str,
    resource_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    client_key: Optional[str] = None,
) -> str:
    """
    Compute a deterministic 64-character hex idempotency key.

    If client_key is explicitly provided, it takes precedence combined with user_id and job_type.
    Otherwise, a hash is computed from (user_id, job_type, resource_id, sorted_payload).
    """
    if client_key and client_key.strip():
        raw = f"client:{user_id}:{job_type}:{client_key.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    payload_str = json.dumps(payload or {}, sort_keys=True)
    raw = f"auto:{user_id}:{job_type}:{resource_id or ''}:{payload_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
