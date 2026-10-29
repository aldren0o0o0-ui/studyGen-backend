"""Phase 9 Background Processing, Job Queue, Progress Tracking, and Failure Recovery.

Revision ID: 0009_phase9_jobs
Revises: 0008_phase8_ocr
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_phase9_jobs"
down_revision: Union[str, None] = "0008_phase8_ocr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add durable_file_path column to documents table
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("durable_file_path", sa.String(512), nullable=True))

    # 2. Create processing_jobs table
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("stage", sa.String(60), nullable=True),
        sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("queue_name", sa.String(40), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("result_reference_type", sa.String(40), nullable=True),
        sa.Column("result_reference_id", sa.String(36), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("parent_job_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_jobs_user_id", "processing_jobs", ["user_id"])
    op.create_index("ix_jobs_status", "processing_jobs", ["status"])
    op.create_index("ix_jobs_resource", "processing_jobs", ["resource_id", "status"])
    op.create_index("ix_jobs_idempotency", "processing_jobs", ["idempotency_key"])
    op.create_index("ix_jobs_user_status", "processing_jobs", ["user_id", "status"])
    op.create_index("ix_jobs_next_retry", "processing_jobs", ["next_retry_at"])
    op.create_index("ix_jobs_created_at", "processing_jobs", ["created_at"])

    # 3. Create worker_heartbeats table
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("queue_name", sa.String(40), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("current_job_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="STARTING"),
        sa.Column("version", sa.String(20), nullable=False, server_default="9.0.0"),
    )
    op.create_index("ix_heartbeats_queue", "worker_heartbeats", ["queue_name"])
    op.create_index("ix_heartbeats_status", "worker_heartbeats", ["status"])
    op.create_index("ix_heartbeats_last_heartbeat", "worker_heartbeats", ["last_heartbeat_at"])


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_table("processing_jobs")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_column("durable_file_path")
