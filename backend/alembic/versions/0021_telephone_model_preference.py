"""Let each tenant choose the telephone speech model."""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_configurations",
        sa.Column("telephone_model", sa.String(30), nullable=False, server_default="auto"),
    )


def downgrade():
    op.drop_column("agent_configurations", "telephone_model")
