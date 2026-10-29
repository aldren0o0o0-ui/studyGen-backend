"""Phase 12A.1 permanent session invalidation.

Revision ID: 0016_phase12a_1_session_security
Revises: 0015_phase12a_admin
"""
from alembic import op
import sqlalchemy as sa

revision="0016_phase12a_1_session_security"
down_revision="0015_phase12a_admin"
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("session_version",sa.Integer(),nullable=False,server_default="1"))
        batch.create_check_constraint("ck_users_session_version_positive","session_version >= 1")
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.add_column(sa.Column("session_version",sa.Integer(),nullable=False,server_default="1"))
        batch.create_check_constraint("ck_refresh_tokens_session_version_positive","session_version >= 1")

def downgrade():
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.drop_constraint("ck_refresh_tokens_session_version_positive",type_="check")
        batch.drop_column("session_version")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_session_version_positive",type_="check")
        batch.drop_column("session_version")
