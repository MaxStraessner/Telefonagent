"""Attribute configuration changes by platform admins without a membership."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_configuration_audits") as batch:
        batch.add_column(sa.Column("platform_actor_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_agent_audit_platform_actor", "app_users", ["platform_actor_id"], ["id"])


def downgrade():
    with op.batch_alter_table("agent_configuration_audits") as batch:
        batch.drop_constraint("fk_agent_audit_platform_actor", type_="foreignkey")
        batch.drop_column("platform_actor_id")
