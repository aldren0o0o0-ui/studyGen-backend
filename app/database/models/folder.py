"""
database/models/folder.py — File Workspace Folders
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.auth import LEGACY_OWNER_ID
from app.database.types import UTC_DATETIME, utc_now

if TYPE_CHECKING:
    from app.database.models.document import Document

_utcnow = utc_now


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, default=LEGACY_OWNER_ID
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTC_DATETIME, default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTC_DATETIME, default=_utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)

    documents: Mapped[List["Document"]] = relationship(
        "Document", back_populates="folder", lazy="select"
    )
    subfolders: Mapped[List["Folder"]] = relationship(
        "Folder",
        back_populates="parent_folder",
        lazy="select",
    )
    parent_folder: Mapped[Optional["Folder"]] = relationship(
        "Folder",
        back_populates="subfolders",
        remote_side=[id],
        lazy="select",
    )

    __table_args__ = (
        Index("ix_folders_user_id", "user_id"),
        Index("ix_folders_parent_folder_id", "parent_folder_id"),
        Index("ix_folders_user_parent", "user_id", "parent_folder_id"),
        Index("ix_folders_created_at", "created_at"),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = _utcnow()
        self.updated_at = _utcnow()

    def restore(self) -> None:
        self.deleted_at = None
        self.updated_at = _utcnow()

    def __repr__(self) -> str:
        return f"<Folder id={self.id} name={self.name} user_id={self.user_id}>"
