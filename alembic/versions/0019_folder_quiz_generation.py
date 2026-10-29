"""Folder Quiz Generation (Wave 3).

Revision ID: 0019_folder_quiz_generation
Revises: 0018_ai_scope_model
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_folder_quiz_generation"
down_revision = "0018_ai_scope_model"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Update quizzes table with scope columns
    with op.batch_alter_table("quizzes") as batch:
        batch.add_column(
            sa.Column("scope_type", sa.String(30), nullable=False, server_default="DOCUMENT")
        )
        batch.add_column(
            sa.Column(
                "folder_id",
                sa.String(36),
                sa.ForeignKey("folders.id", name="fk_quizzes_folder_id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("include_subfolders", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch.alter_column("document_id", existing_type=sa.String(36), nullable=True)
        batch.create_index("ix_quizzes_folder_id", ["folder_id"])

    # 2. Create quiz_source_documents table
    op.create_table(
        "quiz_source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "quiz_id",
            sa.String(36),
            sa.ForeignKey("quizzes.id", name="fk_quiz_source_documents_quiz_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", name="fk_quiz_source_documents_document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("quiz_id", "document_id", name="uq_quiz_source_document"),
    )
    op.create_index("ix_quiz_source_documents_quiz_id", "quiz_source_documents", ["quiz_id"])
    op.create_index("ix_quiz_source_documents_document_id", "quiz_source_documents", ["document_id"])


def downgrade():
    op.drop_index("ix_quiz_source_documents_document_id", table_name="quiz_source_documents")
    op.drop_index("ix_quiz_source_documents_quiz_id", table_name="quiz_source_documents")
    op.drop_table("quiz_source_documents")

    with op.batch_alter_table("quizzes") as batch:
        batch.drop_index("ix_quizzes_folder_id")
        batch.alter_column("document_id", existing_type=sa.String(36), nullable=False)
        batch.drop_column("include_subfolders")
        batch.drop_column("folder_id")
        batch.drop_column("scope_type")
