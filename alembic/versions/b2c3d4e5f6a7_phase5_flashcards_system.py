"""Phase 5 Flashcards and Study Sets system schema.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. study_sets
    op.create_table(
        "study_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("topic_label", sa.String(255), nullable=True),
        sa.Column("set_type", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("card_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_study_sets_status", "study_sets", ["status"])
    op.create_index("ix_study_sets_document_id", "study_sets", ["document_id"])
    op.create_index("ix_study_sets_created_at", "study_sets", ["created_at"])

    # 2. flashcards
    op.create_table(
        "flashcards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_set_id", sa.String(36), sa.ForeignKey("study_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_question_id", sa.String(36), sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("front_text", sa.Text, nullable=False),
        sa.Column("back_text", sa.Text, nullable=False),
        sa.Column("hint_text", sa.Text, nullable=True),
        sa.Column("topic_label", sa.String(255), nullable=True),
        sa.Column("difficulty", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("card_type", sa.String(30), nullable=False, server_default="concept"),
        sa.Column("source_references_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("generation_method", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("is_user_edited", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("source_validation_status", sa.String(20), nullable=False, server_default="valid"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("study_set_id", "position", name="uq_flashcard_position_per_set"),
    )
    op.create_index("ix_flashcards_study_set_id", "flashcards", ["study_set_id"])
    op.create_index("ix_flashcards_document_id", "flashcards", ["document_id"])
    op.create_index("ix_flashcards_status", "flashcards", ["status"])

    # 3. flashcard_review_states
    op.create_table(
        "flashcard_review_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flashcard_id", sa.String(36), sa.ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="NEW"),
        sa.Column("ease_level", sa.Float, nullable=False, server_default="2.5"),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("consecutive_correct", sa.Integer, nullable=False, server_default="0"),
        sa.Column("interval_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_reviewed_at", sa.DateTime, nullable=True),
        sa.Column("next_review_at", sa.DateTime, nullable=True),
        sa.Column("mastery_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_flashcard_review_states_status", "flashcard_review_states", ["review_status"])
    op.create_index("ix_flashcard_review_states_next_review", "flashcard_review_states", ["next_review_at"])

    # 4. flashcard_review_sessions
    op.create_table(
        "flashcard_review_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_set_id", sa.String(36), sa.ForeignKey("study_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_mode", sa.String(20), nullable=False, server_default="due"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("requested_count", sa.Integer, nullable=False),
        sa.Column("actual_count", sa.Integer, nullable=False),
        sa.Column("reviewed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_flashcard_review_sessions_study_set_id", "flashcard_review_sessions", ["study_set_id"])
    op.create_index("ix_flashcard_review_sessions_status", "flashcard_review_sessions", ["status"])

    # 5. flashcard_review_session_items
    op.create_table(
        "flashcard_review_session_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("flashcard_review_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flashcard_id", sa.String(36), sa.ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("is_revealed", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("rating", sa.String(20), nullable=True),
        sa.Column("reviewed_at", sa.DateTime, nullable=True),
        # PostgreSQL unique constraints create schema-level index relations.
        # Phase 4 already uses uq_session_item_position on review_session_items,
        # so this table needs a globally distinct constraint/index name.
        sa.UniqueConstraint(
            "session_id",
            "position",
            name="uq_flashcard_review_session_item_position",
        ),
    )
    op.create_index("ix_flashcard_review_session_items_session_id", "flashcard_review_session_items", ["session_id"])

    # 6. flashcard_review_results
    op.create_table(
        "flashcard_review_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("flashcard_review_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flashcard_id", sa.String(36), sa.ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("was_correct", sa.Boolean, nullable=False),
        sa.Column("previous_interval_days", sa.Integer, nullable=False),
        sa.Column("next_interval_days", sa.Integer, nullable=False),
        sa.Column("reviewed_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_flashcard_review_results_session_id", "flashcard_review_results", ["session_id"])
    op.create_index("ix_flashcard_review_results_flashcard_id", "flashcard_review_results", ["flashcard_id"])
    op.create_index("ix_flashcard_review_results_reviewed_at", "flashcard_review_results", ["reviewed_at"])


def downgrade() -> None:
    op.drop_table("flashcard_review_results")
    op.drop_table("flashcard_review_session_items")
    op.drop_table("flashcard_review_sessions")
    op.drop_table("flashcard_review_states")
    op.drop_table("flashcards")
    op.drop_table("study_sets")
