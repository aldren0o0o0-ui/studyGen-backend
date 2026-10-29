"""
services/durable_storage.py — Phase 9 Durable Local File Storage

Manages document files in a persistent, worker-accessible storage directory.
Files are stored at:
  {DOCUMENT_STORAGE_ROOT}/users/{user_id}/documents/{document_id}/original.{ext}

Never trusts or exposes client-provided filenames as paths.
Path traversal is prevented by using only UUIDs as directory components.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Allowlist of permitted file extensions (lowercase, no dot)
_ALLOWED_EXTENSIONS = {"pdf", "txt", "docx"}


def _safe_ext(file_type: str) -> str:
    """Return a validated lowercase extension, raising ValueError for unsafe inputs."""
    ext = file_type.lower().lstrip(".")
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension for durable storage: {ext!r}")
    return ext


def get_document_storage_path(
    storage_root: str,
    user_id: str,
    document_id: str,
    file_type: str,
) -> Path:
    """
    Return the canonical durable storage path for a document.

    Both user_id and document_id must be UUIDs to prevent path traversal.
    The path is NOT created by this function.
    """
    # Validate UUIDs (must be hex + dashes only)
    import uuid
    try:
        uuid.UUID(user_id)
        uuid.UUID(document_id)
    except ValueError:
        raise ValueError("user_id and document_id must be valid UUIDs")

    ext = _safe_ext(file_type)
    return (
        Path(storage_root)
        / "users"
        / user_id
        / "documents"
        / document_id
        / f"original.{ext}"
    )


def store_document_file(
    storage_root: str,
    user_id: str,
    document_id: str,
    file_type: str,
    file_bytes: bytes,
) -> Path:
    """
    Persist document bytes to durable storage.

    Returns the absolute path where the file was written.
    Raises ValueError on invalid arguments.
    Raises IOError on write failure.
    """
    dest = get_document_storage_path(storage_root, user_id, document_id, file_type)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)
    logger.info("Stored document file: document_id=%s size=%d bytes", document_id, len(file_bytes))
    return dest


def get_durable_file_path(
    storage_root: str,
    user_id: str,
    document_id: str,
    file_type: str,
) -> Optional[Path]:
    """
    Return the durable path if the file exists, None otherwise.
    """
    path = get_document_storage_path(storage_root, user_id, document_id, file_type)
    return path if path.exists() else None


def delete_document_storage(
    storage_root: str,
    user_id: str,
    document_id: str,
) -> bool:
    """
    Remove the entire document storage directory for one document.

    Returns True if the directory was removed, False if it didn't exist.
    """
    import uuid
    try:
        uuid.UUID(user_id)
        uuid.UUID(document_id)
    except ValueError:
        raise ValueError("user_id and document_id must be valid UUIDs")

    doc_dir = Path(storage_root) / "users" / user_id / "documents" / document_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)
        logger.info("Deleted document storage directory: %s", doc_dir)
        return True
    return False


def durable_relative_path(
    storage_root: str,
    user_id: str,
    document_id: str,
    file_type: str,
) -> str:
    """
    Return the relative path (relative to storage_root) for DB storage.
    This avoids persisting absolute paths that change between environments.
    """
    full_path = get_document_storage_path(storage_root, user_id, document_id, file_type)
    return str(full_path.relative_to(storage_root))


def resolve_durable_path(storage_root: str, relative_path: str) -> Optional[Path]:
    """
    Resolve a stored relative path back to an absolute path.
    Performs path traversal check to ensure the result is within storage_root.

    Returns None if the file does not exist or the path is unsafe.
    """
    root = Path(storage_root).resolve()
    try:
        full_path = (root / relative_path).resolve()
    except Exception:
        return None

    # Prevent path traversal
    try:
        full_path.relative_to(root)
    except ValueError:
        logger.warning("Path traversal attempt detected: %s", relative_path)
        return None

    return full_path if full_path.exists() else None
