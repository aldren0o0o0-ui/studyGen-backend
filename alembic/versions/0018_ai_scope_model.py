"""AI Scope Model for Course and Folder Intelligence (Wave 1).

Revision ID: 0018_ai_scope_model
Revises: 0017_file_workspace_folders
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_ai_scope_model"
down_revision = "0017_file_workspace_folders"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(
            sa.Column("scope_type", sa.String(30), nullable=False, server_default="document")
        )
        batch.add_column(
            sa.Column(
                "folder_id",
                sa.String(36),
                sa.ForeignKey("folders.id", name="fk_chat_sessions_folder_id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("include_subfolders", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch.create_index("ix_chat_sessions_folder_id", ["folder_id"])


def downgrade():
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_index("ix_chat_sessions_folder_id")
        batch.drop_column("include_subfolders")
        batch.drop_column("folder_id")
        batch.drop_column("scope_type")
