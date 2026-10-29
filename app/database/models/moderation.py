"""Phase 11D reporting, restrictions, and append-only moderation audit."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import JSON_VALUE, UTC_DATETIME, utc_now

REPORT_REASONS = (
    "SPAM", "HARASSMENT", "HATE_OR_ABUSE", "SEXUAL_OR_EXPLICIT_CONTENT",
    "VIOLENCE_OR_THREATS", "MISINFORMATION", "PRIVACY_VIOLATION",
    "COPYRIGHT_OR_UNAUTHORIZED_CONTENT", "IMPERSONATION", "OTHER",
)
RESTRICTION_TYPES = (
    "COMMUNITY_POSTING", "COMMENTING", "REACTING", "SHARING",
    "MEDIA_UPLOAD", "COMMUNITY_ACCESS", "ACCOUNT",
)
ACTION_TYPES = (
    "REPORT_CREATED", "REPORT_ASSIGNED", "REPORT_UNASSIGNED",
    "REPORT_REVIEW_STARTED", "REPORT_DISMISSED", "REPORT_RESOLVED",
    "POST_HIDDEN", "POST_REMOVED", "POST_RESTORED", "COMMENT_HIDDEN",
    "COMMENT_REMOVED", "COMMENT_RESTORED", "USER_RESTRICTED",
    "RESTRICTION_REVOKED", "USER_DEACTIVATED", "USER_REACTIVATED",
    "USER_PROMOTED_TO_MODERATOR", "USER_DEMOTED_FROM_MODERATOR",
)


class ContentReport(Base):
    __tablename__ = "content_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reporter_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reported_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    assigned_moderator_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_by_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolution_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resource_caption_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resource_status_at_report: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    __table_args__ = (
        CheckConstraint("resource_type IN ('POST','COMMENT')", name="ck_content_reports_resource_type"),
        CheckConstraint("status IN ('OPEN','UNDER_REVIEW','RESOLVED','DISMISSED')", name="ck_content_reports_status"),
        CheckConstraint("priority IN ('NORMAL','HIGH','CRITICAL')", name="ck_content_reports_priority"),
        CheckConstraint("reason IN ('" + "','".join(REPORT_REASONS) + "')", name="ck_content_reports_reason"),
        Index("ix_content_reports_queue", "status", "priority", "created_at", "id"),
        Index("ix_content_reports_resource", "resource_type", "resource_id"),
        Index("ix_content_reports_reported_user", "reported_user_id", "status"),
        Index("uq_content_reports_active_reporter_resource", "reporter_user_id", "resource_type", "resource_id", unique=True,
              postgresql_where=text("status IN ('OPEN','UNDER_REVIEW')"), sqlite_where=text("status IN ('OPEN','UNDER_REVIEW')")),
    )


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source_report_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("content_reports.id", ondelete="SET NULL"), nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON_VALUE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    __table_args__ = (
        CheckConstraint("action_type IN ('" + "','".join(ACTION_TYPES) + "')", name="ck_moderation_actions_type"),
        Index("ix_moderation_actions_target_user", "target_user_id", "created_at"),
        Index("ix_moderation_actions_target", "target_type", "target_id", "created_at"),
        Index("ix_moderation_actions_source_report", "source_report_id"),
    )


class UserRestriction(Base):
    __tablename__ = "user_restrictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    restriction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    ends_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    source_report_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("content_reports.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    revoked_by_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revocation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint("restriction_type IN ('" + "','".join(RESTRICTION_TYPES) + "')", name="ck_user_restrictions_type"),
        CheckConstraint("status IN ('ACTIVE','EXPIRED','REVOKED')", name="ck_user_restrictions_status"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_user_restrictions_period"),
        Index("ix_user_restrictions_active", "user_id", "status", "starts_at", "ends_at"),
        Index("uq_user_restrictions_active_scope", "user_id", "restriction_type", unique=True,
              postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'")),
    )
