"""Store prepared opening audio for the answering machine."""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_configurations", sa.Column("integrated_greeting", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("agent_configurations", sa.Column("greeting_audio_digest", sa.String(64)))
    op.add_column("agent_configurations", sa.Column("greeting_audio", sa.LargeBinary()))
    op.add_column("call_sessions", sa.Column("prepared_greeting_audio", sa.LargeBinary()))


def downgrade():
    op.drop_column("call_sessions", "prepared_greeting_audio")
    for column in ("greeting_audio", "greeting_audio_digest", "integrated_greeting"):
        op.drop_column("agent_configurations", column)
