"""File workspace folders, favorites, and recents.

Revision ID: 0017_file_workspace_folders
Revises: 0016_phase12a_1_session_security
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_file_workspace_folders"
down_revision = "0016_phase12a_1_session_security"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create folders table
    op.create_table(
        "folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", name="fk_folders_user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_folder_id", sa.String(36), sa.ForeignKey("folders.id", name="fk_folders_parent_folder_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_folders_user_id", "folders", ["user_id"])
    op.create_index("ix_folders_parent_folder_id", "folders", ["parent_folder_id"])
    op.create_index("ix_folders_user_parent", "folders", ["user_id", "parent_folder_id"])
    op.create_index("ix_folders_created_at", "folders", ["created_at"])

    # 2. Add columns to documents table
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("folder_id", sa.String(36), sa.ForeignKey("folders.id", name="fk_documents_folder_id", ondelete="SET NULL"), nullable=True)
        )
        batch.add_column(
            sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch.add_column(
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_index("ix_documents_folder_id", ["folder_id"])
        batch.create_index("ix_documents_is_favorite", ["is_favorite"])
        batch.create_index("ix_documents_last_accessed", ["last_accessed_at"])


def downgrade():
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_last_accessed")
        batch.drop_index("ix_documents_is_favorite")
        batch.drop_index("ix_documents_folder_id")
        batch.drop_column("last_accessed_at")
        batch.drop_column("is_favorite")
        batch.drop_column("folder_id")

    op.drop_index("ix_folders_created_at", table_name="folders")
    op.drop_index("ix_folders_user_parent", table_name="folders")
    op.drop_index("ix_folders_parent_folder_id", table_name="folders")
    op.drop_index("ix_folders_user_id", table_name="folders")
    op.drop_table("folders")
