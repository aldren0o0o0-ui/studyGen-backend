"""Phase 4 — Smart Study and Review System schema

Revision ID: a1b2c3d4e5f6
Revises: 538716082ba8
Create Date: 2026-07-22

Adds five new tables:
  topic_performance     — per-topic accuracy + mastery aggregate
  review_items          — spaced-repetition review queue
  review_sessions       — grouped review sessions
  review_session_items  — immutable question snapshots per session
  study_goals           — daily study goals
  study_activities      — event log (quiz/review/upload)

Safe on SQLite: all new tables; no existing table modifications.
Supports upgrade, downgrade, and re-upgrade.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "538716082ba8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # topic_performance
    # ------------------------------------------------------------------
    op.create_table(
        "topic_performance",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic_label", sa.String(200), nullable=False),
        sa.Column("normalized_topic", sa.String(200), nullable=False),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy_percentage", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("mastery_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("mastery_state", sa.String(20), nullable=False, server_default="insufficient_data"),
        sa.Column("last_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("last_correct_at", sa.DateTime(), nullable=True),
        sa.Column("last_incorrect_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("document_id", "normalized_topic", name="uq_topic_per_document"),
        sa.CheckConstraint("attempted_count >= 0", name="ck_tp_attempted_non_negative"),
        sa.CheckConstraint("correct_count >= 0", name="ck_tp_correct_non_negative"),
        sa.CheckConstraint("incorrect_count >= 0", name="ck_tp_incorrect_non_negative"),
    )
    op.create_index("ix_topic_perf_document_id", "topic_performance", ["document_id"])
    op.create_index("ix_topic_perf_mastery_state", "topic_performance", ["mastery_state"])
    op.create_index("ix_topic_perf_accuracy", "topic_performance", ["accuracy_percentage"])

    # ------------------------------------------------------------------
    # review_items
    # ------------------------------------------------------------------
    op.create_table(
        "review_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "question_id",
            sa.String(36),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_attempt_id",
            sa.String(36),
            sa.ForeignKey("quiz_attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("topic_label", sa.String(200), nullable=True),
        sa.Column("question_type", sa.String(50), nullable=False, server_default="multiple_choice"),
        sa.Column("difficulty", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="DUE"),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("correct_review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("next_review_at", sa.DateTime(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("question_id", name="uq_review_item_question"),
        sa.CheckConstraint("incorrect_count >= 1", name="ck_ri_incorrect_ge_one"),
        sa.CheckConstraint("correct_review_count >= 0", name="ck_ri_correct_non_negative"),
        sa.CheckConstraint("interval_days >= 0", name="ck_ri_interval_non_negative"),
    )
    op.create_index("ix_review_items_next_review_at", "review_items", ["next_review_at"])
    op.create_index("ix_review_items_status", "review_items", ["review_status"])
    op.create_index("ix_review_items_document_id", "review_items", ["document_id"])
    op.create_index("ix_review_items_topic_label", "review_items", ["topic_label"])

    # ------------------------------------------------------------------
    # review_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "review_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_type", sa.String(30), nullable=False),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("topic_label", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_sessions_status", "review_sessions", ["status"])
    op.create_index("ix_review_sessions_created_at", "review_sessions", ["created_at"])
    op.create_index("ix_review_sessions_document_id", "review_sessions", ["document_id"])

    # ------------------------------------------------------------------
    # review_session_items
    # ------------------------------------------------------------------
    op.create_table(
        "review_session_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("review_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.String(36),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("selected_answer", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("session_id", "position", name="uq_session_item_position"),
    )
    op.create_index("ix_rsi_session_id", "review_session_items", ["session_id"])

    # ------------------------------------------------------------------
    # study_goals
    # ------------------------------------------------------------------
    op.create_table(
        "study_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("goal_type", sa.String(40), nullable=False),
        sa.Column("target_value", sa.Integer(), nullable=False),
        sa.Column("current_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goal_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("goal_type", "goal_date", name="uq_goal_per_type_per_day"),
        sa.CheckConstraint("target_value >= 1", name="ck_goal_target_ge_one"),
        sa.CheckConstraint("current_value >= 0", name="ck_goal_current_non_negative"),
    )
    op.create_index("ix_study_goals_goal_date", "study_goals", ["goal_date"])
    op.create_index("ix_study_goals_status", "study_goals", ["status"])

    # ------------------------------------------------------------------
    # study_activities
    # ------------------------------------------------------------------
    op.create_table(
        "study_activities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("activity_type", sa.String(30), nullable=False),
        sa.Column("related_document_id", sa.String(36), nullable=True),
        sa.Column("related_quiz_id", sa.String(36), nullable=True),
        sa.Column("related_attempt_id", sa.String(36), nullable=True),
        sa.Column("related_review_session_id", sa.String(36), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_study_activities_occurred_at", "study_activities", ["occurred_at"])
    op.create_index("ix_study_activities_type", "study_activities", ["activity_type"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index("ix_study_activities_type", table_name="study_activities")
    op.drop_index("ix_study_activities_occurred_at", table_name="study_activities")
    op.drop_table("study_activities")

    op.drop_index("ix_study_goals_status", table_name="study_goals")
    op.drop_index("ix_study_goals_goal_date", table_name="study_goals")
    op.drop_table("study_goals")

    op.drop_index("ix_rsi_session_id", table_name="review_session_items")
    op.drop_table("review_session_items")

    op.drop_index("ix_review_sessions_document_id", table_name="review_sessions")
    op.drop_index("ix_review_sessions_created_at", table_name="review_sessions")
    op.drop_index("ix_review_sessions_status", table_name="review_sessions")
    op.drop_table("review_sessions")

    op.drop_index("ix_review_items_topic_label", table_name="review_items")
    op.drop_index("ix_review_items_document_id", table_name="review_items")
    op.drop_index("ix_review_items_status", table_name="review_items")
    op.drop_index("ix_review_items_next_review_at", table_name="review_items")
    op.drop_table("review_items")

    op.drop_index("ix_topic_perf_accuracy", table_name="topic_performance")
    op.drop_index("ix_topic_perf_mastery_state", table_name="topic_performance")
    op.drop_index("ix_topic_perf_document_id", table_name="topic_performance")
    op.drop_table("topic_performance")
