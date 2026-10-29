"""Phase 11A social foundation.

Revision ID: 0012_phase11a_social
Revises: 0011_phase10_1_stabilization
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_phase11a_social"
down_revision: Union[str, None] = "0011_phase10_1_stabilization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dt():
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(30), nullable=True))
        batch.add_column(sa.Column("normalized_username", sa.String(30), nullable=True))
        batch.add_column(sa.Column("bio", sa.String(500), nullable=True))
        batch.add_column(sa.Column("profile_image_key", sa.String(512), nullable=True))
        batch.create_unique_constraint("uq_users_normalized_username", ["normalized_username"])

    op.create_table(
        "posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("post_type", sa.String(20), nullable=False, server_default="TEXT"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="PUBLIC"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("allow_comments", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_at", _dt(), nullable=True),
        sa.Column("edited_at", _dt(), nullable=True),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False),
        sa.Column("deleted_at", _dt(), nullable=True),
        sa.CheckConstraint("visibility IN ('PUBLIC','SPECIFIC_USERS','PRIVATE')", name="ck_posts_visibility"),
        sa.CheckConstraint("status IN ('DRAFT','PROCESSING','PUBLISHED','FAILED','HIDDEN','REMOVED','DELETED')", name="ck_posts_status"),
        sa.CheckConstraint("post_type IN ('TEXT','DOCUMENT','IMAGE','VIDEO','MIXED')", name="ck_posts_type"),
    )
    op.create_index("ix_posts_feed", "posts", ["status", "published_at", "id"])
    op.create_index("ix_posts_author_published", "posts", ["author_user_id", "published_at", "id"])
    op.create_index("ix_posts_visibility_status_published", "posts", ["visibility", "status", "published_at"])

    op.create_table(
        "post_allowed_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allowed_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.UniqueConstraint("post_id", "allowed_user_id", name="uq_post_allowed_user"),
    )
    op.create_index("ix_post_allowed_user_lookup", "post_allowed_users", ["allowed_user_id", "post_id"])

    op.create_table(
        "post_media",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("display_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("thumbnail_storage_key", sa.String(512), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("document_processing_reference_id", sa.String(36), nullable=True),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False),
        sa.Column("deleted_at", _dt(), nullable=True),
        sa.CheckConstraint("media_type IN ('DOCUMENT','IMAGE','VIDEO')", name="ck_post_media_type"),
        sa.CheckConstraint("processing_status IN ('PENDING','PROCESSING','READY','FAILED')", name="ck_post_media_status"),
        sa.UniqueConstraint("post_id", "position", name="uq_post_media_position"),
    )
    op.create_index("ix_post_media_post_position", "post_media", ["post_id", "position"])


def downgrade() -> None:
    op.drop_table("post_media")
    op.drop_table("post_allowed_users")
    op.drop_table("posts")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_normalized_username", type_="unique")
        batch.drop_column("profile_image_key")
        batch.drop_column("bio")
        batch.drop_column("normalized_username")
        batch.drop_column("username")
