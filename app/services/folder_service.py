"""
services/folder_service.py — File Workspace Folder Management & Tree Navigation
All functions are synchronous — call via run_in_threadpool from async routes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.document import Document
from app.database.models.folder import Folder

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_folder(
    db: Session,
    user_id: str,
    name: str,
    parent_folder_id: Optional[str] = None,
) -> Folder:
    """Create a new folder for the given user, optionally nested under a parent folder."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Folder name cannot be empty.")

    if parent_folder_id:
        parent = db.get(Folder, parent_folder_id)
        if not parent or parent.user_id != user_id or parent.is_deleted:
            raise ValueError(f"Parent folder '{parent_folder_id}' not found or access denied.")

    folder_id = str(uuid.uuid4())
    folder = Folder(
        id=folder_id,
        user_id=user_id,
        name=clean_name,
        parent_folder_id=parent_folder_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    logger.info("Created folder id=%s name='%s' user=%s", folder_id, clean_name, user_id)
    return folder


def get_folder(db: Session, folder_id: str, user_id: str) -> Optional[Folder]:
    """Retrieve an active folder owned by the user."""
    folder = db.get(Folder, folder_id)
    if folder and folder.user_id == user_id and not folder.is_deleted:
        return folder
    return None


def is_descendant_of(db: Session, potential_child_id: str, ancestor_id: str) -> bool:
    """Check if potential_child_id is ancestor_id or a descendant of ancestor_id (cycle detection)."""
    curr_id: Optional[str] = potential_child_id
    visited = set()
    while curr_id:
        if curr_id == ancestor_id:
            return True
        if curr_id in visited:
            break
        visited.add(curr_id)
        parent_folder = db.get(Folder, curr_id)
        if not parent_folder:
            break
        curr_id = parent_folder.parent_folder_id
    return False


def get_breadcrumbs(db: Session, folder_id: str, user_id: str) -> List[dict]:
    """Generate breadcrumb trail from root down to the target folder."""
    crumbs: List[dict] = []
    curr_id: Optional[str] = folder_id
    visited = set()

    while curr_id and curr_id not in visited:
        visited.add(curr_id)
        folder = db.get(Folder, curr_id)
        if not folder or folder.user_id != user_id or folder.is_deleted:
            break
        crumbs.append({"id": folder.id, "name": folder.name})
        curr_id = folder.parent_folder_id

    crumbs.reverse()
    return crumbs


def list_folders(
    db: Session,
    user_id: str,
    parent_folder_id: Optional[str] = None,
    all_levels: bool = False,
) -> List[Tuple[Folder, int, int]]:
    """
    List user folders with aggregated document count and subfolder count (single grouped query).
    If all_levels is False, filters by parent_folder_id.
    """
    # 1. Base query for folders
    query = select(Folder).where(Folder.user_id == user_id, Folder.deleted_at.is_(None))
    if not all_levels:
        if parent_folder_id:
            query = query.where(Folder.parent_folder_id == parent_folder_id)
        else:
            query = query.where(Folder.parent_folder_id.is_(None))

    query = query.order_by(Folder.name.asc())
    folders = list(db.execute(query).scalars().all())
    if not folders:
        return []

    folder_ids = [f.id for f in folders]

    # 2. Aggregated document counts per folder
    doc_counts_query = (
        select(Document.folder_id, func.count(Document.id))
        .where(
            Document.folder_id.in_(folder_ids),
            Document.user_id == user_id,
            Document.deleted_at.is_(None),
        )
        .group_by(Document.folder_id)
    )
    doc_counts_map = dict(db.execute(doc_counts_query).all())

    # 3. Aggregated subfolder counts per folder
    subfolder_counts_query = (
        select(Folder.parent_folder_id, func.count(Folder.id))
        .where(
            Folder.parent_folder_id.in_(folder_ids),
            Folder.user_id == user_id,
            Folder.deleted_at.is_(None),
        )
        .group_by(Folder.parent_folder_id)
    )
    subfolder_counts_map = dict(db.execute(subfolder_counts_query).all())

    results = []
    for f in folders:
        doc_count = doc_counts_map.get(f.id, 0)
        sub_count = subfolder_counts_map.get(f.id, 0)
        results.append((f, doc_count, sub_count))

    return results


def update_folder(
    db: Session,
    folder_id: str,
    user_id: str,
    name: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
    clear_parent: bool = False,
) -> Folder:
    """Rename or move a folder, enforcing ownership and cycle prevention."""
    folder = get_folder(db, folder_id, user_id)
    if not folder:
        raise ValueError(f"Folder '{folder_id}' not found or access denied.")

    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Folder name cannot be empty.")
        folder.name = clean_name

    if clear_parent:
        folder.parent_folder_id = None
    elif parent_folder_id is not None and parent_folder_id != folder.parent_folder_id:
        if parent_folder_id == folder.id:
            raise ValueError("A folder cannot be its own parent.")

        target_parent = db.get(Folder, parent_folder_id)
        if not target_parent or target_parent.user_id != user_id or target_parent.is_deleted:
            raise ValueError(f"Target folder '{parent_folder_id}' not found or access denied.")

        # Cycle prevention: target cannot be a descendant of folder
        if is_descendant_of(db, parent_folder_id, folder.id):
            raise ValueError("Cannot move a folder into one of its own descendants.")

        folder.parent_folder_id = parent_folder_id

    folder.updated_at = _utcnow()
    db.commit()
    db.refresh(folder)
    logger.info("Updated folder id=%s user=%s", folder_id, user_id)
    return folder


def delete_folder(db: Session, folder_id: str, user_id: str) -> int:
    """
    Safely delete a folder:
    1. Moves all contained documents to root (folder_id = NULL) transactionally.
    2. Moves all direct subfolders to the deleted folder's parent.
    3. Deletes the folder record.
    Returns the number of documents moved to root.
    """
    folder = get_folder(db, folder_id, user_id)
    if not folder:
        raise ValueError(f"Folder '{folder_id}' not found or access denied.")

    # 1. Relocate documents to root
    docs = list(
        db.query(Document)
        .filter(Document.folder_id == folder_id, Document.user_id == user_id)
        .all()
    )
    moved_count = len(docs)
    for d in docs:
        d.folder_id = None
        d.updated_at = _utcnow()

    # 2. Relocate direct subfolders to parent (or root)
    new_parent_id = folder.parent_folder_id
    subfolders = list(
        db.query(Folder)
        .filter(Folder.parent_folder_id == folder_id, Folder.user_id == user_id)
        .all()
    )
    for sub in subfolders:
        sub.parent_folder_id = new_parent_id
        sub.updated_at = _utcnow()

    db.flush()

    # 3. Delete the folder record
    db.delete(folder)
    db.commit()
    logger.info("Deleted folder id=%s, relocated %d documents to root", folder_id, moved_count)
    return moved_count
