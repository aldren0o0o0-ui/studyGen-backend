"""Course and Folder Intelligence AI Scope Resolver Service (Wave 1).

Authoritative backend service for validating, resolving, and bounding AI context scope
across Single Document, Selected Documents, Direct Folders, and Recursive Folders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database.models.document import Document
from app.database.models.folder import Folder
from app.schemas.ai_scope import (
    AIScopeDocumentSummary,
    AIScopePreviewResponse,
    AIScopeRequest,
    AIScopeType,
)

logger = logging.getLogger(__name__)

MAX_AI_SCOPE_DOCUMENTS = 50


class AIScopeError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ResolvedAIScope:
    scope_type: str
    folder_id: Optional[str]
    folder_name: Optional[str]
    include_subfolders: bool
    document_ids: List[str]
    document_count: int
    folder_count: int
    total_files_in_scope: int
    non_ready_count: int
    documents: List[AIScopeDocumentSummary]


def _collect_descendant_folder_ids(db: Session, root_folder_id: str, user_id: str) -> List[str]:
    """Defensive, tenant-scoped iterative BFS traversal to collect all descendant folder IDs."""
    folder_ids = [root_folder_id]
    queue = [root_folder_id]

    while queue:
        parent_id = queue.pop(0)
        sub_rows = db.execute(
            select(Folder.id).where(
                Folder.parent_folder_id == parent_id,
                Folder.user_id == user_id,
                Folder.deleted_at.is_(None),
            )
        ).scalars().all()

        for sub_id in sub_rows:
            if sub_id not in folder_ids:
                folder_ids.append(sub_id)
                queue.append(sub_id)

    return folder_ids


def resolve_ai_scope(
    db: Session,
    user_id: str,
    request: AIScopeRequest,
    *,
    strict_readiness: bool = True,
) -> ResolvedAIScope:
    """
    Authoritatively resolve eligible document IDs for an AI request.
    Enforces tenant boundaries, trash exclusion, readiness check, deduplication, and max limits.
    """
    scope_type = request.scope_type

    # 1. Single Document Scope
    if scope_type == AIScopeType.DOCUMENT:
        doc_id = request.document_id or (
            request.document_ids[0] if request.document_ids and len(request.document_ids) == 1 else None
        )
        if not doc_id:
            raise AIScopeError(422, "document_id is required for document scope.")

        doc = db.execute(
            select(Document).where(
                Document.id == doc_id,
                Document.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not doc:
            raise AIScopeError(404, f"Document '{doc_id}' not found.")

        if doc.deleted_at is not None:
            raise AIScopeError(422, f"Document '{doc.id}' is not ready for chat.")

        if strict_readiness and doc.status != "READY":
            raise AIScopeError(422, f"Document '{doc.id}' is not ready for chat.")

        doc_summary = AIScopeDocumentSummary(
            document_id=doc.id,
            filename=doc.original_filename,
            file_type=doc.file_type,
            file_size=doc.file_size,
            status=doc.status,
            folder_id=doc.folder_id,
        )

        return ResolvedAIScope(
            scope_type="document",
            folder_id=doc.folder_id,
            folder_name=None,
            include_subfolders=False,
            document_ids=[doc.id],
            document_count=1,
            folder_count=1 if doc.folder_id else 0,
            total_files_in_scope=1,
            non_ready_count=0 if doc.status == "READY" else 1,
            documents=[doc_summary],
        )

    # 2. Explicit Selected Documents Scope
    elif scope_type == AIScopeType.SELECTED_DOCUMENTS:
        raw_ids = request.document_ids or []
        if not raw_ids:
            raise AIScopeError(422, "At least one document must be selected.")

        # Deduplicate while preserving original order
        doc_ids = list(dict.fromkeys(raw_ids))

        if len(doc_ids) > MAX_AI_SCOPE_DOCUMENTS:
            raise AIScopeError(
                422,
                f"Selected {len(doc_ids)} documents, exceeding the maximum allowed limit of {MAX_AI_SCOPE_DOCUMENTS} documents for an AI session.",
            )

        docs = list(
            db.execute(
                select(Document).where(
                    Document.id.in_(doc_ids),
                    Document.user_id == user_id,
                )
            ).scalars().all()
        )

        found_by_id = {d.id: d for d in docs}
        missing = [did for did in doc_ids if did not in found_by_id]
        if missing:
            raise AIScopeError(404, f"Document '{missing[0]}' not found.")

        if strict_readiness:
            invalid = [d for d in docs if d.status != "READY" or d.deleted_at is not None]
            if invalid:
                raise AIScopeError(
                    422,
                    f"Document '{invalid[0].id}' is not ready for chat.",
                )

        doc_summaries = [
            AIScopeDocumentSummary(
                document_id=found_by_id[did].id,
                filename=found_by_id[did].original_filename,
                file_type=found_by_id[did].file_type,
                file_size=found_by_id[did].file_size,
                status=found_by_id[did].status,
                folder_id=found_by_id[did].folder_id,
            )
            for did in doc_ids
        ]

        return ResolvedAIScope(
            scope_type="selected_documents",
            folder_id=None,
            folder_name=None,
            include_subfolders=False,
            document_ids=doc_ids,
            document_count=len(doc_ids),
            folder_count=0,
            total_files_in_scope=len(doc_ids),
            non_ready_count=sum(1 for d in docs if d.status != "READY" or d.deleted_at is not None),
            documents=doc_summaries,
        )

    # 3. Folder Scope (Direct or Recursive)
    elif scope_type == AIScopeType.FOLDER:
        f_id = request.folder_id
        if not f_id:
            raise AIScopeError(422, "folder_id is required for folder scope.")

        folder = db.execute(
            select(Folder).where(
                Folder.id == f_id,
                Folder.user_id == user_id,
                Folder.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if not folder:
            raise AIScopeError(404, f"Folder '{f_id}' not found.")

        # Determine target folders
        if request.include_subfolders:
            folder_ids = _collect_descendant_folder_ids(db, folder.id, user_id)
        else:
            folder_ids = [folder.id]

        all_docs = list(
            db.execute(
                select(Document).where(
                    Document.folder_id.in_(folder_ids),
                    Document.user_id == user_id,
                    Document.deleted_at.is_(None),
                )
            ).scalars().all()
        )

        ready_docs = [d for d in all_docs if d.status == "READY"]
        non_ready_count = len(all_docs) - len(ready_docs)

        if strict_readiness and not ready_docs:
            desc_text = "and its subfolders " if request.include_subfolders else ""
            raise AIScopeError(
                400,
                f"Folder '{folder.name}' {desc_text}contains no ready documents for AI.",
            )

        if strict_readiness and len(ready_docs) > MAX_AI_SCOPE_DOCUMENTS:
            raise AIScopeError(
                422,
                f"Scope contains {len(ready_docs)} ready documents, exceeding the maximum allowed limit of {MAX_AI_SCOPE_DOCUMENTS} documents.",
            )

        doc_summaries = [
            AIScopeDocumentSummary(
                document_id=d.id,
                filename=d.original_filename,
                file_type=d.file_type,
                file_size=d.file_size,
                status=d.status,
                folder_id=d.folder_id,
            )
            for d in ready_docs
        ]

        return ResolvedAIScope(
            scope_type="folder",
            folder_id=folder.id,
            folder_name=folder.name,
            include_subfolders=request.include_subfolders,
            document_ids=[d.id for d in ready_docs],
            document_count=len(ready_docs),
            folder_count=len(folder_ids),
            total_files_in_scope=len(all_docs),
            non_ready_count=non_ready_count,
            documents=doc_summaries,
        )

    else:
        raise AIScopeError(422, f"Unsupported scope_type: '{scope_type}'")


def preview_ai_scope(
    db: Session,
    user_id: str,
    request: AIScopeRequest,
) -> AIScopePreviewResponse:
    """Preview AI scope without side effects or creating chat sessions."""
    resolved = resolve_ai_scope(db, user_id, request, strict_readiness=False)
    return AIScopePreviewResponse(
        scope_type=resolved.scope_type,
        folder_id=resolved.folder_id,
        folder_name=resolved.folder_name,
        include_subfolders=resolved.include_subfolders,
        document_count=resolved.document_count,
        folder_count=resolved.folder_count,
        total_files_in_scope=resolved.total_files_in_scope,
        non_ready_count=resolved.non_ready_count,
        documents=resolved.documents,
    )
