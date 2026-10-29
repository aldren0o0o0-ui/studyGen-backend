"""Folder Flashcards (Wave 4).

Revision ID: 0020_folder_flashcards
Revises: 0019_folder_quiz_generation
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_folder_flashcards"
down_revision = "0019_folder_quiz_generation"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Update study_sets table with scope columns
    with op.batch_alter_table("study_sets") as batch:
        batch.add_column(
            sa.Column("scope_type", sa.String(30), nullable=False, server_default="DOCUMENT")
        )
        batch.add_column(
            sa.Column(
                "folder_id",
                sa.String(36),
                sa.ForeignKey("folders.id", name="fk_study_sets_folder_id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("include_subfolders", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch.alter_column("document_id", existing_type=sa.String(36), nullable=True)
        batch.create_index("ix_study_sets_folder_id", ["folder_id"])

    # 2. Create study_set_source_documents table
    op.create_table(
        "study_set_source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "study_set_id",
            sa.String(36),
            sa.ForeignKey("study_sets.id", name="fk_study_set_source_documents_study_set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", name="fk_study_set_source_documents_document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("study_set_id", "document_id", name="uq_study_set_source_document"),
    )
    op.create_index("ix_study_set_source_documents_study_set_id", "study_set_source_documents", ["study_set_id"])
    op.create_index("ix_study_set_source_documents_document_id", "study_set_source_documents", ["document_id"])


def downgrade():
    op.drop_index("ix_study_set_source_documents_document_id", table_name="study_set_source_documents")
    op.drop_index("ix_study_set_source_documents_study_set_id", table_name="study_set_source_documents")
    op.drop_table("study_set_source_documents")

    with op.batch_alter_table("study_sets") as batch:
        batch.drop_index("ix_study_sets_folder_id")
        batch.alter_column("document_id", existing_type=sa.String(36), nullable=True)
        batch.drop_column("include_subfolders")
        batch.drop_column("folder_id")
        batch.drop_column("scope_type")
