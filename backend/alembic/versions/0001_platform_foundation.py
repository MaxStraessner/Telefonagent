"""Create multi-tenant platform foundation.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum("draft", "active", "inactive", name="tenantstatus", native_enum=False), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("assistant_name", sa.String(100), nullable=False),
        sa.Column("default_language", sa.String(10), nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=False),
        sa.Column("presentation_mode_enabled", sa.Boolean(), nullable=False),
        sa.Column("diagnostics_enabled", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"])

    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("street", sa.String(200), nullable=False),
        sa.Column("postal_code", sa.String(20), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_locations_tenant_id", "locations", ["tenant_id"])

    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "name", name="uq_services_tenant_name"),
    )
    op.create_index("ix_services_tenant_id", "services", ["tenant_id"])

    op.create_table(
        "staff_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("role_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "display_name", name="uq_staff_tenant_name"),
    )
    op.create_index("ix_staff_members_tenant_id", "staff_members", ["tenant_id"])

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id"), nullable=True),
        sa.Column("staff_member_id", sa.Uuid(), sa.ForeignKey("staff_members.id"), nullable=True),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("customer_phone", sa.String(50), nullable=False),
        sa.Column("customer_email", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Enum("pending", "confirmed", "cancelled", "completed", name="appointmentstatus", native_enum=False), nullable=False),
        sa.Column("source", sa.Enum("web_test", "voice_agent", "manual", "external_calendar", name="appointmentsource", native_enum=False), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index("ix_appointments_tenant_starts", "appointments", ["tenant_id", "starts_at"])

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("channel", sa.Enum("browser", "telephone", name="callchannel", native_enum=False), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_call_sessions_tenant_id", "call_sessions", ["tenant_id"])

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("call_session_id", sa.Uuid(), sa.ForeignKey("call_sessions.id"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tool_executions_tenant_id", "tool_executions", ["tenant_id"])
    op.create_index("ix_tool_executions_call_session_id", "tool_executions", ["call_session_id"])


def downgrade() -> None:
    for table in ("tool_executions", "call_sessions", "appointments", "staff_members", "services", "locations", "tenant_settings", "tenants"):
        op.drop_table(table)

