"""Store user-owned Gmail connections and pin queued calls to their settings."""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gmail_notifications",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), primary_key=True),
        sa.Column("generation", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recipient", sa.String(320)),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text()),
        sa.Column("oauth_state_hash", sa.String(64)),
        sa.Column("oauth_session_id", sa.Uuid()),
        sa.Column("encrypted_verifier", sa.Text()),
        sa.Column("oauth_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_gmail_notifications_active_tenant", "gmail_notifications", ["tenant_id"], unique=True,
        postgresql_where=sa.text("enabled"), sqlite_where=sa.text("enabled"),
    )
    op.add_column("call_sessions", sa.Column("notification_gmail_generation", sa.Uuid()))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE gmail_notifications ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE gmail_notifications FORCE ROW LEVEL SECURITY")
        tenant = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        op.execute(f"CREATE POLICY tenant_isolation ON gmail_notifications USING ({tenant}) WITH CHECK ({tenant})")


def downgrade():
    op.drop_column("call_sessions", "notification_gmail_generation")
    op.drop_table("gmail_notifications")
