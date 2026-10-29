"""Phase 11D trust, safety, and moderation.

Revision ID: 0014_phase11d_moderation
Revises: 0013_phase11b_interactions
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014_phase11d_moderation"
down_revision: Union[str, None] = "0013_phase11b_interactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REPORT_REASONS = "'SPAM','HARASSMENT','HATE_OR_ABUSE','SEXUAL_OR_EXPLICIT_CONTENT','VIOLENCE_OR_THREATS','MISINFORMATION','PRIVACY_VIOLATION','COPYRIGHT_OR_UNAUTHORIZED_CONTENT','IMPERSONATION','OTHER'"
ACTION_TYPES = "'REPORT_CREATED','REPORT_ASSIGNED','REPORT_UNASSIGNED','REPORT_REVIEW_STARTED','REPORT_DISMISSED','REPORT_RESOLVED','POST_HIDDEN','POST_REMOVED','POST_RESTORED','COMMENT_HIDDEN','COMMENT_REMOVED','COMMENT_RESTORED','USER_RESTRICTED','RESTRICTION_REVOKED','USER_DEACTIVATED','USER_REACTIVATED'"


def _dt():
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role", sa.String(20), nullable=False, server_default="USER"))
        batch.create_check_constraint("ck_users_role", "role IN ('USER','MODERATOR','ADMIN')")
    op.create_index("ix_users_role_status", "users", ["role", "status"])

    op.create_table(
        "content_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reporter_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("reported_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False), sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="NORMAL"),
        sa.Column("assigned_moderator_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolution_code", sa.String(30)), sa.Column("resolution_note", sa.Text()),
        sa.Column("resource_caption_excerpt", sa.Text()),
        sa.Column("resource_status_at_report", sa.String(20), nullable=False),
        sa.Column("resource_created_at", _dt(), nullable=False), sa.Column("created_at", _dt(), nullable=False),
        sa.Column("updated_at", _dt(), nullable=False), sa.Column("resolved_at", _dt()),
        sa.CheckConstraint("resource_type IN ('POST','COMMENT')", name="ck_content_reports_resource_type"),
        sa.CheckConstraint("status IN ('OPEN','UNDER_REVIEW','RESOLVED','DISMISSED')", name="ck_content_reports_status"),
        sa.CheckConstraint("priority IN ('NORMAL','HIGH','CRITICAL')", name="ck_content_reports_priority"),
        sa.CheckConstraint(f"reason IN ({REPORT_REASONS})", name="ck_content_reports_reason"),
    )
    op.create_index("ix_content_reports_queue", "content_reports", ["status", "priority", "created_at", "id"])
    op.create_index("ix_content_reports_resource", "content_reports", ["resource_type", "resource_id"])
    op.create_index("ix_content_reports_reported_user", "content_reports", ["reported_user_id", "status"])
    op.create_index("uq_content_reports_active_reporter_resource", "content_reports", ["reporter_user_id", "resource_type", "resource_id"], unique=True, postgresql_where=sa.text("status IN ('OPEN','UNDER_REVIEW')"), sqlite_where=sa.text("status IN ('OPEN','UNDER_REVIEW')"))

    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action_type", sa.String(40), nullable=False), sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("target_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_report_id", sa.String(36), sa.ForeignKey("content_reports.id", ondelete="SET NULL")),
        sa.Column("reason_code", sa.String(50)), sa.Column("note", sa.Text()),
        sa.Column("metadata_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", _dt(), nullable=False),
        sa.CheckConstraint(f"action_type IN ({ACTION_TYPES})", name="ck_moderation_actions_type"),
    )
    op.create_index("ix_moderation_actions_target_user", "moderation_actions", ["target_user_id", "created_at"])
    op.create_index("ix_moderation_actions_target", "moderation_actions", ["target_type", "target_id", "created_at"])
    op.create_index("ix_moderation_actions_source_report", "moderation_actions", ["source_report_id"])

    op.create_table(
        "user_restrictions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("restriction_type", sa.String(30), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("starts_at", _dt(), nullable=False), sa.Column("ends_at", _dt()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_report_id", sa.String(36), sa.ForeignKey("content_reports.id", ondelete="SET NULL")),
        sa.Column("created_at", _dt(), nullable=False), sa.Column("updated_at", _dt(), nullable=False), sa.Column("revoked_at", _dt()),
        sa.Column("revoked_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("revocation_reason", sa.Text()),
        sa.CheckConstraint("restriction_type IN ('COMMUNITY_POSTING','COMMENTING','REACTING','SHARING','MEDIA_UPLOAD','COMMUNITY_ACCESS','ACCOUNT')", name="ck_user_restrictions_type"),
        sa.CheckConstraint("status IN ('ACTIVE','EXPIRED','REVOKED')", name="ck_user_restrictions_status"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_user_restrictions_period"),
    )
    op.create_index("ix_user_restrictions_active", "user_restrictions", ["user_id", "status", "starts_at", "ends_at"])
    op.create_index("uq_user_restrictions_active_scope", "user_restrictions", ["user_id", "restriction_type"], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))


def downgrade() -> None:
    op.drop_table("user_restrictions")
    op.drop_table("moderation_actions")
    op.drop_table("content_reports")
    op.drop_index("ix_users_role_status", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("role")
