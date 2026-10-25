"""Extend existing agent configuration and calls without duplicating records."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_configurations") as batch:
        batch.add_column(sa.Column("operating_mode", sa.String(30), nullable=False, server_default="agent"))
        batch.add_column(sa.Column("message_greeting", sa.Text(), nullable=False, server_default="Guten Tag. Bitte schildern Sie Ihr Anliegen."))
        batch.add_column(sa.Column("call_retention_days", sa.Integer(), nullable=False, server_default="30"))
    with op.batch_alter_table("call_sessions") as batch:
        batch.add_column(sa.Column("operating_mode", sa.String(30), nullable=True))
        batch.add_column(sa.Column("voice_model", sa.String(100), nullable=True))
        batch.add_column(sa.Column("transcript_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("documentation_status", sa.String(30), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("analysis_details", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("analysis_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("analysis_lease_token", sa.String(36), nullable=True))
        batch.add_column(sa.Column("content_deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Historical mode/model remain unknown; no retrospective model or mode is invented.


def downgrade():
    with op.batch_alter_table("call_sessions") as batch:
        for name in ("content_deleted_at", "analysis_lease_token", "analysis_next_attempt_at", "analysis_details", "documentation_status", "transcript_revision", "voice_model", "operating_mode"):
            batch.drop_column(name)
    with op.batch_alter_table("agent_configurations") as batch:
        for name in ("call_retention_days", "message_greeting", "operating_mode"):
            batch.drop_column(name)
