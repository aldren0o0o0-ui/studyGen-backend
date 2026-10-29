"""Phase 11B privacy-safe social interactions.

Revision ID: 0013_phase11b_interactions
Revises: 0012_phase11a_social
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0013_phase11b_interactions"
down_revision: Union[str, None] = "0012_phase11a_social"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dt():
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.add_column(sa.Column("shared_post_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("reaction_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("share_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("save_count", sa.Integer(), nullable=False, server_default="0"))
        batch.create_foreign_key("fk_posts_shared_post_id", "posts", ["shared_post_id"], ["id"], ondelete="RESTRICT")
        batch.create_check_constraint("ck_posts_not_self_shared", "shared_post_id IS NULL OR shared_post_id <> id")
        batch.create_check_constraint("ck_posts_nonnegative_interaction_counts", "reaction_count >= 0 AND comment_count >= 0 AND share_count >= 0 AND save_count >= 0")
    op.create_index("ix_posts_shared_post_id", "posts", ["shared_post_id"])

    op.create_table(
        "post_reactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reaction_type", sa.String(20), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False),
        sa.CheckConstraint("reaction_type IN ('LIKE','HELPFUL','INSIGHTFUL')", name="ck_post_reactions_type"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_post_reaction_user"),
    )
    op.create_index("ix_post_reactions_user_created", "post_reactions", ["user_id", "created_at"])

    op.create_table(
        "post_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("parent_comment_id", sa.String(36), sa.ForeignKey("post_comments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="VISIBLE"),
        sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False),
        sa.Column("edited_at", _dt(), nullable=True),
        sa.Column("deleted_at", _dt(), nullable=True),
        sa.CheckConstraint("status IN ('VISIBLE','DELETED','HIDDEN','REMOVED')", name="ck_post_comments_status"),
        sa.CheckConstraint("parent_comment_id IS NULL OR parent_comment_id <> id", name="ck_post_comments_not_self_parent"),
    )
    op.create_index("ix_post_comments_post_created", "post_comments", ["post_id", "created_at", "id"])
    op.create_index("ix_post_comments_parent_created", "post_comments", ["parent_comment_id", "created_at", "id"])
    op.create_index("ix_post_comments_author_created", "post_comments", ["author_user_id", "created_at"])

    op.create_table(
        "saved_posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", _dt(), nullable=False),
        sa.UniqueConstraint("user_id", "post_id", name="uq_saved_post_user"),
    )
    op.create_index("ix_saved_posts_user_created", "saved_posts", ["user_id", "created_at", "id"])
    op.create_index("ix_saved_posts_post", "saved_posts", ["post_id"])


def downgrade() -> None:
    op.drop_table("saved_posts")
    op.drop_table("post_comments")
    op.drop_table("post_reactions")
    op.drop_index("ix_posts_shared_post_id", table_name="posts")
    with op.batch_alter_table("posts") as batch:
        batch.drop_constraint("ck_posts_nonnegative_interaction_counts", type_="check")
        batch.drop_constraint("ck_posts_not_self_shared", type_="check")
        batch.drop_constraint("fk_posts_shared_post_id", type_="foreignkey")
        batch.drop_column("save_count")
        batch.drop_column("share_count")
        batch.drop_column("comment_count")
        batch.drop_column("reaction_count")
        batch.drop_column("shared_post_id")
