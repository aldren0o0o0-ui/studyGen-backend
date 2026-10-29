"""Phase 8 OCR and Advanced Document Processing.

Revision ID: 0008_phase8_ocr
Revises: f6a7b8c9d0e1
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_phase8_ocr"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add Phase 8 columns to documents table
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extraction_method", sa.String(20), nullable=True, server_default="native"))
        batch_op.add_column(sa.Column("extraction_status", sa.String(20), nullable=True, server_default="READY"))
        batch_op.add_column(sa.Column("extraction_quality_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("extraction_quality_label", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("total_pages", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("native_page_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ocr_page_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("failed_page_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("empty_page_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("extraction_warnings_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("extractor_version", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("ocr_engine", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("processed_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_documents_extraction_status", ["extraction_status"])
        batch_op.create_index("ix_documents_extraction_quality", ["extraction_quality_label"])

    # 2. Backfill existing documents
    op.execute(
        "UPDATE documents SET extraction_method = 'legacy', extraction_status = 'READY' WHERE extraction_method IS NULL"
    )

    # 3. Create document_processing_runs table
    op.create_table(
        "document_processing_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="native"),
        sa.Column("extractor_version", sa.String(50), nullable=False, server_default="1.0.0"),
        sa.Column("ocr_engine", sa.String(50), nullable=False, server_default="none"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("native_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ocr_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("empty_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("quality_label", sa.String(20), nullable=False, server_default="unusable"),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_processing_runs_document_id", "document_processing_runs", ["document_id"])
    op.create_index("ix_processing_runs_status", "document_processing_runs", ["status"])
    op.create_index("ix_processing_runs_created_at", "document_processing_runs", ["created_at"])

    # 4. Create document_pages table
    op.create_table(
        "document_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="native"),
        sa.Column("status", sa.String(20), nullable=False, server_default="OK"),
        sa.Column("character_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("section_label", sa.String(255), nullable=True),
        sa.Column("text_hash", sa.String(64), nullable=True),
        sa.Column("warning_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_pages_document_page", "document_pages", ["document_id", "page_number"], unique=True)
    op.create_index("ix_document_pages_status", "document_pages", ["status"])


def downgrade() -> None:
    op.drop_table("document_pages")
    op.drop_table("document_processing_runs")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index("ix_documents_extraction_quality")
        batch_op.drop_index("ix_documents_extraction_status")
        batch_op.drop_column("processed_at")
        batch_op.drop_column("ocr_engine")
        batch_op.drop_column("extractor_version")
        batch_op.drop_column("extraction_warnings_json")
        batch_op.drop_column("empty_page_count")
        batch_op.drop_column("failed_page_count")
        batch_op.drop_column("ocr_page_count")
        batch_op.drop_column("native_page_count")
        batch_op.drop_column("total_pages")
        batch_op.drop_column("extraction_quality_label")
        batch_op.drop_column("extraction_quality_score")
        batch_op.drop_column("extraction_status")
        batch_op.drop_column("extraction_method")
