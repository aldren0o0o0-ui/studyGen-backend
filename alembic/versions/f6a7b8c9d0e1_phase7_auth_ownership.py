"""Phase 7 authentication and root-resource ownership.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_OWNER_ID = "00000000-0000-0000-0000-000000000007"
OWNED_TABLES = (
    "documents",
    "quizzes",
    "quiz_attempts",
    "topic_performance",
    "review_items",
    "review_sessions",
    "study_goals",
    "study_activities",
    "study_sets",
    "flashcard_review_sessions",
    "chat_sessions",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("normalized_email", name="uq_users_normalized_email"),
    )
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("parent_token_id", sa.String(36), sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_name", sa.String(120), nullable=True),
        sa.Column("device_identifier", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("replaced_by_token_id", sa.String(36), sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoke_reason", sa.String(80), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
    )
    op.create_index("ix_refresh_tokens_user", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_expires", "refresh_tokens", ["expires_at"])

    now = sa.text("CURRENT_TIMESTAMP")
    users = sa.table(
        "users",
        sa.column("id"), sa.column("email"), sa.column("normalized_email"),
        sa.column("password_hash"), sa.column("display_name"), sa.column("status"),
        sa.column("failed_login_attempts"), sa.column("password_changed_at"),
        sa.column("created_at"), sa.column("updated_at"),
    )
    op.bulk_insert(users, [{
        "id": LEGACY_OWNER_ID,
        "email": "legacy@local.invalid",
        "normalized_email": "legacy@local.invalid",
        "password_hash": "!disabled-legacy-owner",
        "display_name": "Legacy Data Owner",
        "status": "DISABLED",
        "failed_login_attempts": 0,
        "password_changed_at": op.get_bind().execute(sa.select(now)).scalar_one(),
        "created_at": op.get_bind().execute(sa.select(now)).scalar_one(),
        "updated_at": op.get_bind().execute(sa.select(now)).scalar_one(),
    }])

    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_user_id_users", "users", ["user_id"], ["id"],
                ondelete="RESTRICT",
            )
        op.execute(sa.text(f"UPDATE {table} SET user_id = :owner WHERE user_id IS NULL").bindparams(owner=LEGACY_OWNER_ID))
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "user_id",
                existing_type=sa.String(36),
                nullable=False,
                server_default=LEGACY_OWNER_ID,
            )

    with op.batch_alter_table("study_goals") as batch:
        batch.drop_constraint("uq_goal_per_type_per_day", type_="unique")
        batch.create_unique_constraint(
            "uq_goal_per_type_per_day", ["user_id", "goal_type", "goal_date"]
        )
    with op.batch_alter_table("topic_performance") as batch:
        batch.drop_constraint("uq_topic_per_document", type_="unique")
        batch.create_unique_constraint(
            "uq_topic_per_document",
            ["user_id", "document_id", "normalized_topic"],
        )
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("uq_review_item_question", type_="unique")
        batch.create_unique_constraint(
            "uq_review_item_question", ["user_id", "question_id"]
        )

    op.create_index("ix_documents_user_created", "documents", ["user_id", "created_at"])
    op.create_index("ix_documents_user_status", "documents", ["user_id", "status"])
    op.create_index("ix_quizzes_user_created", "quizzes", ["user_id", "created_at"])
    op.create_index("ix_quiz_attempts_user_created", "quiz_attempts", ["user_id", "created_at"])
    op.create_index("ix_topic_performance_user", "topic_performance", ["user_id"])
    op.create_index("ix_review_items_user_due", "review_items", ["user_id", "next_review_at"])
    op.create_index("ix_review_sessions_user_created", "review_sessions", ["user_id", "created_at"])
    op.create_index("ix_study_goals_user_date", "study_goals", ["user_id", "goal_date"])
    op.create_index("ix_study_activities_user_time", "study_activities", ["user_id", "occurred_at"])
    op.create_index("ix_study_sets_user_status", "study_sets", ["user_id", "status"])
    op.create_index("ix_flashcard_review_sessions_user", "flashcard_review_sessions", ["user_id", "created_at"])
    op.create_index("ix_chat_sessions_user_recent", "chat_sessions", ["user_id", "last_message_at"])


def downgrade() -> None:
    indexes = (
        ("chat_sessions", "ix_chat_sessions_user_recent"),
        ("flashcard_review_sessions", "ix_flashcard_review_sessions_user"),
        ("study_sets", "ix_study_sets_user_status"),
        ("study_activities", "ix_study_activities_user_time"),
        ("study_goals", "ix_study_goals_user_date"),
        ("review_sessions", "ix_review_sessions_user_created"),
        ("review_items", "ix_review_items_user_due"),
        ("topic_performance", "ix_topic_performance_user"),
        ("quiz_attempts", "ix_quiz_attempts_user_created"),
        ("quizzes", "ix_quizzes_user_created"),
        ("documents", "ix_documents_user_status"),
        ("documents", "ix_documents_user_created"),
    )
    for table, index in indexes:
        op.drop_index(index, table_name=table)

    with op.batch_alter_table("study_goals") as batch:
        batch.drop_constraint("uq_goal_per_type_per_day", type_="unique")
        batch.create_unique_constraint(
            "uq_goal_per_type_per_day", ["goal_type", "goal_date"]
        )
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("uq_review_item_question", type_="unique")
        batch.create_unique_constraint("uq_review_item_question", ["question_id"])
    with op.batch_alter_table("topic_performance") as batch:
        batch.drop_constraint("uq_topic_per_document", type_="unique")
        batch.create_unique_constraint(
            "uq_topic_per_document", ["document_id", "normalized_topic"]
        )

    for table in reversed(OWNED_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
            batch.drop_column("user_id")

    op.drop_table("refresh_tokens")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
