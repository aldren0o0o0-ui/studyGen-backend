"""Phase 7 authentication identities and revocable refresh sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTC_DATETIME, utc_now

DateTime = UTC_DATETIME

LEGACY_OWNER_ID = "00000000-0000-0000-0000-000000000007"


_utcnow = utc_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    normalized_username: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, unique=True)
    bio: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    profile_image_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('USER','MODERATOR','ADMIN')", name="ck_users_role"),
        CheckConstraint("session_version >= 1", name="ck_users_session_version_positive"),
        Index("ix_users_status", "status"),
        Index("ix_users_role_status", "role", "status"),
        Index("ix_users_created_at", "created_at"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_token_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    device_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    device_identifier: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    replaced_by_token_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        CheckConstraint("session_version >= 1", name="ck_refresh_tokens_session_version_positive"),
        Index("ix_refresh_tokens_user", "user_id"),
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_expires", "expires_at"),
    )
