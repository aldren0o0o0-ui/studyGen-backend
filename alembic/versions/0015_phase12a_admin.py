"""Phase 12A Admin role-transition audit actions."""
from alembic import op
revision="0015_phase12a_admin"
down_revision="0014_phase11d_moderation"
branch_labels=None
depends_on=None
OLD="'REPORT_CREATED','REPORT_ASSIGNED','REPORT_UNASSIGNED','REPORT_REVIEW_STARTED','REPORT_DISMISSED','REPORT_RESOLVED','POST_HIDDEN','POST_REMOVED','POST_RESTORED','COMMENT_HIDDEN','COMMENT_REMOVED','COMMENT_RESTORED','USER_RESTRICTED','RESTRICTION_REVOKED','USER_DEACTIVATED','USER_REACTIVATED'"
NEW=OLD+",'USER_PROMOTED_TO_MODERATOR','USER_DEMOTED_FROM_MODERATOR'"
def _replace(values):
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("moderation_actions") as batch:
            batch.drop_constraint("ck_moderation_actions_type",type_="check")
            batch.create_check_constraint("ck_moderation_actions_type",f"action_type IN ({values})")
    else:
        op.drop_constraint("ck_moderation_actions_type","moderation_actions",type_="check")
        op.create_check_constraint("ck_moderation_actions_type","moderation_actions",f"action_type IN ({values})")
def upgrade():_replace(NEW)
def downgrade():_replace(OLD)
