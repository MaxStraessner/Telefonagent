"""Opt-in call email delivery on existing tenant-protected rows.

No historical messages are queued; no schema drift is reconciled.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_configurations", sa.Column("call_email_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("agent_configurations", sa.Column("call_email_recipient", sa.String(320)))
    op.add_column("call_sessions", sa.Column("notification_status", sa.String(30), nullable=False, server_default="not_requested"))
    op.add_column("call_sessions", sa.Column("notification_recipient", sa.String(320)))
    op.add_column("call_sessions", sa.Column("notification_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("call_sessions", sa.Column("notification_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("call_sessions", sa.Column("notification_lease_token", sa.String(36)))
    op.add_column("call_sessions", sa.Column("notification_sent_at", sa.DateTime(timezone=True)))
    op.add_column("call_sessions", sa.Column("notification_error_code", sa.String(80)))
    op.create_index("ix_call_sessions_notification_status", "call_sessions", ["notification_status"])


def downgrade():
    op.drop_index("ix_call_sessions_notification_status", table_name="call_sessions")
    for name in ("notification_error_code", "notification_sent_at", "notification_lease_token", "notification_next_attempt_at", "notification_attempts", "notification_recipient", "notification_status"):
        op.drop_column("call_sessions", name)
    op.drop_column("agent_configurations", "call_email_recipient")
    op.drop_column("agent_configurations", "call_email_enabled")
