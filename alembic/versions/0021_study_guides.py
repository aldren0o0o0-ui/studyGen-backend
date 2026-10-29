"""Comprehensive Reviewer / Study Guides (Wave 5).

Revision ID: 0021_study_guides
Revises: 0020_folder_flashcards
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

revision = "0021_study_guides"
down_revision = "0020_folder_flashcards"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    # 1. Create study_guides table
    op.create_table(
        "study_guides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", name="fk_study_guides_user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", name="fk_study_guides_document_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_type", sa.String(30), nullable=False, server_default="DOCUMENT"),
        sa.Column(
            "folder_id",
            sa.String(36),
            sa.ForeignKey("folders.id", name="fk_study_guides_folder_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("include_subfolders", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("guide_mode", sa.String(30), nullable=False, server_default="comprehensive"),
        sa.Column("status", sa.String(20), nullable=False, server_default="READY"),
        sa.Column("target_sections_json", JSON_TYPE, nullable=False),
        sa.Column("generation_metrics_json", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_guides_user_id", "study_guides", ["user_id"])
    op.create_index("ix_study_guides_status", "study_guides", ["status"])
    op.create_index("ix_study_guides_document_id", "study_guides", ["document_id"])
    op.create_index("ix_study_guides_folder_id", "study_guides", ["folder_id"])
    op.create_index("ix_study_guides_created_at", "study_guides", ["created_at"])

    # 2. Create study_guide_sections table
    op.create_table(
        "study_guide_sections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "study_guide_id",
            sa.String(36),
            sa.ForeignKey("study_guides.id", name="fk_study_guide_sections_guide_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("structured_content_json", JSON_TYPE, nullable=False),
        sa.Column("source_references_json", JSON_TYPE, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_guide_sections_guide_id", "study_guide_sections", ["study_guide_id"])
    op.create_index("ix_study_guide_sections_type", "study_guide_sections", ["section_type"])
    op.create_index("ix_study_guide_sections_guide_pos", "study_guide_sections", ["study_guide_id", "position"])

    # 3. Create study_guide_source_documents table
    op.create_table(
        "study_guide_source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "study_guide_id",
            sa.String(36),
            sa.ForeignKey("study_guides.id", name="fk_study_guide_source_docs_guide_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", name="fk_study_guide_source_docs_doc_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("study_guide_id", "document_id", name="uq_study_guide_source_doc"),
    )
    op.create_index("ix_guide_source_docs_guide_id", "study_guide_source_documents", ["study_guide_id"])
    op.create_index("ix_guide_source_docs_doc_id", "study_guide_source_documents", ["document_id"])


def downgrade():
    op.drop_index("ix_guide_source_docs_doc_id", table_name="study_guide_source_documents")
    op.drop_index("ix_guide_source_docs_guide_id", table_name="study_guide_source_documents")
    op.drop_table("study_guide_source_documents")

    op.drop_index("ix_study_guide_sections_guide_pos", table_name="study_guide_sections")
    op.drop_index("ix_study_guide_sections_type", table_name="study_guide_sections")
    op.drop_index("ix_study_guide_sections_guide_id", table_name="study_guide_sections")
    op.drop_table("study_guide_sections")

    op.drop_index("ix_study_guides_created_at", table_name="study_guides")
    op.drop_index("ix_study_guides_folder_id", table_name="study_guides")
    op.drop_index("ix_study_guides_document_id", table_name="study_guides")
    op.drop_index("ix_study_guides_status", table_name="study_guides")
    op.drop_index("ix_study_guides_user_id", table_name="study_guides")
    op.drop_table("study_guides")
