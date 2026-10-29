"""Phase 10 PostgreSQL Data Layer, JSONB types, Boolean alignment, check constraints, and indexes.

Revision ID: 0010_phase10_postgresql
Revises: 0009_phase9_jobs
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010_phase10_postgresql"
down_revision: Union[str, None] = "0009_phase9_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_JSONB_COLUMNS = (
    ("documents", "extraction_warnings_json", None),
    ("document_processing_runs", "warnings_json", None),
    ("quizzes", "question_types_json", '["multiple_choice"]'),
    ("quizzes", "configuration_json", "{}"),
    ("questions", "options_json", None),
    ("questions", "source_chunk_indexes_json", "[]"),
    ("questions", "source_references_json", "[]"),
    ("questions", "accepted_answers_json", "[]"),
    ("chat_messages", "follow_up_suggestions_json", "[]"),
    ("processing_jobs", "payload_json", None),
)


def _alter_json_columns(target_type: str) -> None:
    """Change JSON storage while explicitly handling incompatible defaults."""
    for table, column, default in _JSONB_COLUMNS:
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT')
        cast = "jsonb" if target_type == "JSONB" else "text"
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
            f'TYPE {target_type} USING "{column}"::{cast}'
        )
        if default is not None:
            literal = default.replace("'", "''")
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f"SET DEFAULT '{literal}'::{cast}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. Type adjustments for PostgreSQL (JSONB conversions)
    if is_postgres:
        _alter_json_columns("JSONB")

        # 2. Boolean alignment for chat_messages.insufficient_evidence (Int -> Boolean)
        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence DROP DEFAULT")
        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence TYPE BOOLEAN USING (insufficient_evidence <> 0)")
        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence SET DEFAULT FALSE")

        # 3. PostgreSQL Check Constraints for Enums & Statuses
        # Keep constraints aligned with both seeded legacy data and live service
        # transitions. Phase 7 seeds DISABLED and account deletion writes DELETED.
        op.create_check_constraint(
            "ck_users_status",
            "users",
            "status IN ('ACTIVE', 'LOCKED', 'DISABLED', 'DEACTIVATED', 'DELETED')",
        )
        # OCR/hybrid extraction can leave a usable document in PARTIAL state.
        op.create_check_constraint(
            "ck_documents_status",
            "documents",
            "status IN ('PROCESSING', 'READY', 'PARTIAL', 'FAILED', 'DELETED')",
        )
        op.create_check_constraint("ck_processing_jobs_status", "processing_jobs", "status IN ('PENDING', 'QUEUED', 'RUNNING', 'RETRYING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT')")
        op.create_check_constraint("ck_chat_sessions_status", "chat_sessions", "status IN ('ACTIVE', 'ARCHIVED', 'DELETED')")
        op.create_check_constraint("ck_study_sets_status", "study_sets", "status IN ('ACTIVE', 'ARCHIVED', 'DELETED')")

        # 4. Partial index on active jobs for exclusive resources
        op.execute(
            "CREATE UNIQUE INDEX uq_active_job_resource ON processing_jobs (resource_type, resource_id, job_type) "
            "WHERE status IN ('PENDING', 'QUEUED', 'RUNNING', 'RETRYING') AND resource_type IS NOT NULL AND resource_id IS NOT NULL"
        )
    else:
        # SQLite fallback batch operations if needed
        with op.batch_alter_table("chat_messages", schema=None) as batch_op:
            batch_op.alter_column("insufficient_evidence", existing_type=sa.Integer(), type_=sa.Boolean(), existing_nullable=False, server_default=sa.text("0"))


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS uq_active_job_resource")
        op.drop_constraint("ck_study_sets_status", "study_sets", type_="check")
        op.drop_constraint("ck_chat_sessions_status", "chat_sessions", type_="check")
        op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
        op.drop_constraint("ck_documents_status", "documents", type_="check")
        op.drop_constraint("ck_users_status", "users", type_="check")

        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence DROP DEFAULT")
        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence TYPE INTEGER USING (CASE WHEN insufficient_evidence THEN 1 ELSE 0 END)")
        op.execute("ALTER TABLE chat_messages ALTER COLUMN insufficient_evidence SET DEFAULT 0")

        _alter_json_columns("TEXT")
    else:
        with op.batch_alter_table("chat_messages", schema=None) as batch_op:
            batch_op.alter_column("insufficient_evidence", existing_type=sa.Boolean(), type_=sa.Integer(), existing_nullable=False, server_default=sa.text("0"))
