"""phase3 schema — add Phase 3 fields to quizzes and questions

Revision ID: 538716082ba8
Revises: 0001
Create Date: 2026-07-21

Adds:
  quizzes: coverage_mode, generation_seed, configuration_json,
           prompt_version, model_name, source_quiz_id,
           source_attempt_id, quiz_mode
  questions: question_type, source_references_json, topic_label,
             difficulty, accepted_answers_json

Backfills existing Phase 2 rows conservatively.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
revision: str = "538716082ba8"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # quizzes table — add Phase 3 columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("quizzes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("coverage_mode", sa.String(length=20), nullable=False, server_default="balanced")
        )
        batch_op.add_column(
            sa.Column("generation_seed", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("configuration_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default="legacy_phase2")
        )
        batch_op.add_column(
            sa.Column("model_name", sa.String(length=100), nullable=False, server_default="gemini-1.5-flash")
        )
        batch_op.add_column(
            sa.Column("source_quiz_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_attempt_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("quiz_mode", sa.String(length=20), nullable=False, server_default="generated")
        )

    # ------------------------------------------------------------------
    # questions table — add Phase 3 columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("questions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("question_type", sa.String(length=50), nullable=False, server_default="multiple_choice")
        )
        batch_op.add_column(
            sa.Column("source_references_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("topic_label", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("difficulty", sa.String(length=10), nullable=False, server_default="medium")
        )
        batch_op.add_column(
            sa.Column("accepted_answers_json", sa.Text(), nullable=False, server_default="[]")
        )

    # ------------------------------------------------------------------
    # Backfill existing Phase 2 questions — copy quiz difficulty to each
    # question row so `difficulty` is never 'medium' for hard quizzes.
    # Uses a correlated subquery that is SQLite-compatible.
    # ------------------------------------------------------------------
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE questions
        SET difficulty = (
            SELECT quizzes.difficulty
            FROM quizzes
            WHERE quizzes.id = questions.quiz_id
        )
        WHERE difficulty = 'medium'
          AND EXISTS (
            SELECT 1 FROM quizzes
            WHERE quizzes.id = questions.quiz_id
              AND quizzes.difficulty != 'medium'
          )
    """))


def downgrade() -> None:
    # ------------------------------------------------------------------
    # questions — remove Phase 3 columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("questions", schema=None) as batch_op:
        batch_op.drop_column("accepted_answers_json")
        batch_op.drop_column("difficulty")
        batch_op.drop_column("topic_label")
        batch_op.drop_column("source_references_json")
        batch_op.drop_column("question_type")

    # ------------------------------------------------------------------
    # quizzes — remove Phase 3 columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("quizzes", schema=None) as batch_op:
        batch_op.drop_column("quiz_mode")
        batch_op.drop_column("source_attempt_id")
        batch_op.drop_column("source_quiz_id")
        batch_op.drop_column("model_name")
        batch_op.drop_column("prompt_version")
        batch_op.drop_column("configuration_json")
        batch_op.drop_column("generation_seed")
        batch_op.drop_column("coverage_mode")
