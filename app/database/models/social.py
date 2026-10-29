"""Phase 11A community posts, recipients, and post-owned media."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UTC_DATETIME, utc_now


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    post_type: Mapped[str] = mapped_column(String(20), nullable=False, default="TEXT")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="PUBLIC")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    allow_comments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    edited_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    shared_post_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=True)
    reaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    share_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    save_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    author = relationship("User", lazy="joined")
    recipients: Mapped[List["PostAllowedUser"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    media: Mapped[List["PostMedia"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="PostMedia.position")
    shared_post: Mapped[Optional["Post"]] = relationship(remote_side="Post.id", foreign_keys=[shared_post_id], lazy="joined")

    __table_args__ = (
        CheckConstraint("visibility IN ('PUBLIC','SPECIFIC_USERS','PRIVATE')", name="ck_posts_visibility"),
        CheckConstraint("status IN ('DRAFT','PROCESSING','PUBLISHED','FAILED','HIDDEN','REMOVED','DELETED')", name="ck_posts_status"),
        CheckConstraint("post_type IN ('TEXT','DOCUMENT','IMAGE','VIDEO','MIXED')", name="ck_posts_type"),
        CheckConstraint("shared_post_id IS NULL OR shared_post_id <> id", name="ck_posts_not_self_shared"),
        CheckConstraint("reaction_count >= 0 AND comment_count >= 0 AND share_count >= 0 AND save_count >= 0", name="ck_posts_nonnegative_interaction_counts"),
        Index("ix_posts_feed", "status", "published_at", "id"),
        Index("ix_posts_author_published", "author_user_id", "published_at", "id"),
        Index("ix_posts_visibility_status_published", "visibility", "status", "published_at"),
        Index("ix_posts_shared_post_id", "shared_post_id"),
    )


class PostAllowedUser(Base):
    __tablename__ = "post_allowed_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    allowed_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    __table_args__ = (
        UniqueConstraint("post_id", "allowed_user_id", name="uq_post_allowed_user"),
        Index("ix_post_allowed_user_lookup", "allowed_user_id", "post_id"),
    )


class PostMedia(Base):
    __tablename__ = "post_media"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    thumbnail_storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    document_processing_reference_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    __table_args__ = (
        CheckConstraint("media_type IN ('DOCUMENT','IMAGE','VIDEO')", name="ck_post_media_type"),
        CheckConstraint("processing_status IN ('PENDING','PROCESSING','READY','FAILED')", name="ck_post_media_status"),
        UniqueConstraint("post_id", "position", name="uq_post_media_position"),
        Index("ix_post_media_post_position", "post_id", "position"),
    )


class PostReaction(Base):
    __tablename__ = "post_reactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    __table_args__ = (
        CheckConstraint("reaction_type IN ('LIKE','HELPFUL','INSIGHTFUL')", name="ck_post_reactions_type"),
        UniqueConstraint("post_id", "user_id", name="uq_post_reaction_user"),
        Index("ix_post_reactions_user_created", "user_id", "created_at"),
    )


class PostComment(Base):
    __tablename__ = "post_comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    parent_comment_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("post_comments.id", ondelete="RESTRICT"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="VISIBLE")
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    edited_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    author = relationship("User", lazy="joined")
    __table_args__ = (
        CheckConstraint("status IN ('VISIBLE','DELETED','HIDDEN','REMOVED')", name="ck_post_comments_status"),
        CheckConstraint("parent_comment_id IS NULL OR parent_comment_id <> id", name="ck_post_comments_not_self_parent"),
        Index("ix_post_comments_post_created", "post_id", "created_at", "id"),
        Index("ix_post_comments_parent_created", "parent_comment_id", "created_at", "id"),
        Index("ix_post_comments_author_created", "author_user_id", "created_at"),
    )


class SavedPost(Base):
    __tablename__ = "saved_posts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False, default=utc_now)
    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="uq_saved_post_user"),
        Index("ix_saved_posts_user_created", "user_id", "created_at", "id"),
        Index("ix_saved_posts_post", "post_id"),
    )
