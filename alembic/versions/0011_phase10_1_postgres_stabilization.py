"""Phase 10.1 PostgreSQL stabilization.

Revision ID: 0011_phase10_1_stabilization
Revises: 0010_phase10_postgresql
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_phase10_1_stabilization"
down_revision: Union[str, None] = "0010_phase10_postgresql"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_STATUSES = (
    "PENDING", "QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED",
    "CANCEL_REQUESTED", "CANCELLED", "TIMED_OUT",
)


def _timestamp_columns(bind):
    inspector = sa.inspect(bind)
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        for column in inspector.get_columns(table):
            if isinstance(column["type"], sa.DateTime):
                yield table, column["name"]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    allowed = ", ".join(repr(value) for value in JOB_STATUSES)
    op.create_check_constraint(
        "ck_processing_jobs_status", "processing_jobs", f"status IN ({allowed})"
    )

    # Phase 10 missed this JSON field.
    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json "
        "TYPE JSONB USING source_references_json::jsonb"
    )
    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json "
        "SET DEFAULT '[]'::jsonb"
    )

    # Legacy timestamps were written as naive UTC. Interpret them explicitly as UTC.
    for table, column in list(_timestamp_columns(bind)):
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TIMESTAMPTZ '
            f'USING "{column}" AT TIME ZONE \'UTC\''
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, column in list(_timestamp_columns(bind)):
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TIMESTAMP '
            f'USING "{column}" AT TIME ZONE \'UTC\''
        )

    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json "
        "TYPE TEXT USING source_references_json::text"
    )
    op.execute(
        "ALTER TABLE flashcards ALTER COLUMN source_references_json "
        "SET DEFAULT '[]'::text"
    )
    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    old_statuses = tuple(value for value in JOB_STATUSES if value != "CANCEL_REQUESTED")
    allowed = ", ".join(repr(value) for value in old_statuses)
    op.create_check_constraint(
        "ck_processing_jobs_status", "processing_jobs", f"status IN ({allowed})"
    )
