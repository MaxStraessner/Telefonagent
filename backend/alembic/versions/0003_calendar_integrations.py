"""Add tenant-aware calendar integrations and booking data.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_account_id", sa.String(length=320), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("connection_status", sa.String(length=40), nullable=False),
        sa.Column("last_successful_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", "provider_account_id", name="uq_calendar_connection_account"),
    )
    op.create_index("ix_calendar_connections_tenant_id", "calendar_connections", ["tenant_id"])
    op.create_index("ix_calendar_connections_created_by_user_id", "calendar_connections", ["created_by_user_id"])
    op.create_index("ix_calendar_connections_provider", "calendar_connections", ["provider"])
    op.create_index("ix_calendar_connections_connection_status", "calendar_connections", ["connection_status"])

    op.create_table(
        "calendar_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_code_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_calendar_oauth_state_hash"),
    )
    op.create_index("ix_calendar_oauth_states_tenant_id", "calendar_oauth_states", ["tenant_id"])
    op.create_index("ix_calendar_oauth_states_user_id", "calendar_oauth_states", ["user_id"])
    op.create_index("ix_calendar_oauth_states_expires_at", "calendar_oauth_states", ["expires_at"])

    op.create_table(
        "external_calendars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("calendar_connection_id", sa.Uuid(), nullable=False),
        sa.Column("external_calendar_id", sa.String(length=1000), nullable=False),
        sa.Column("calendar_name", sa.String(length=300), nullable=False),
        sa.Column("calendar_timezone", sa.String(length=100), nullable=False),
        sa.Column("owner_name", sa.String(length=200), nullable=False),
        sa.Column("access_role", sa.String(length=50), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("is_selected_for_availability", sa.Boolean(), nullable=False),
        sa.Column("is_selected_for_booking", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["calendar_connection_id"], ["calendar_connections.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("calendar_connection_id", "external_calendar_id", name="uq_external_calendar_provider_id"),
    )
    op.create_index("ix_external_calendars_tenant_id", "external_calendars", ["tenant_id"])
    op.create_index("ix_external_calendars_calendar_connection_id", "external_calendars", ["calendar_connection_id"])
    op.create_index(
        "ix_external_calendars_tenant_availability",
        "external_calendars",
        ["tenant_id", "is_selected_for_availability"],
    )
    op.create_index(
        "uq_external_calendars_tenant_booking_target",
        "external_calendars",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_selected_for_booking"),
        sqlite_where=sa.text("is_selected_for_booking = 1"),
    )

    op.create_table(
        "booking_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("slot_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("minimum_notice_minutes", sa.Integer(), nullable=False),
        sa.Column("maximum_booking_horizon_days", sa.Integer(), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=False),
        sa.Column("maximum_suggestions_per_request", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_booking_configuration_tenant"),
    )
    op.create_index("ix_booking_configurations_tenant_id", "booking_configurations", ["tenant_id"])

    op.create_table(
        "calendar_business_hours",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "weekday", "start_time", "end_time", name="uq_calendar_business_hour_window"),
    )
    op.create_index("ix_calendar_business_hours_tenant_id", "calendar_business_hours", ["tenant_id"])
    op.create_index("ix_calendar_business_hours_tenant_weekday", "calendar_business_hours", ["tenant_id", "weekday"])

    op.create_table(
        "calendar_appointment_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=True),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=True),
        sa.Column("location_type", sa.String(length=30), nullable=False),
        sa.Column("location_text", sa.String(length=300), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_calendar_appointment_type_name"),
    )
    op.create_index("ix_calendar_appointment_types_tenant_id", "calendar_appointment_types", ["tenant_id"])

    op.create_table(
        "calendar_bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_id", sa.Uuid(), nullable=False),
        sa.Column("calendar_connection_id", sa.Uuid(), nullable=False),
        sa.Column("external_calendar_id", sa.String(length=1000), nullable=False),
        sa.Column("external_event_id", sa.String(length=1000), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("customer_name", sa.String(length=150), nullable=False),
        sa.Column("customer_phone", sa.String(length=50), nullable=False),
        sa.Column("customer_email", sa.String(length=320), nullable=False),
        sa.Column("customer_notes", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("provider_response_reference", sa.String(length=1000), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["appointment_type_id"], ["calendar_appointment_types.id"]),
        sa.ForeignKeyConstraint(["calendar_connection_id"], ["calendar_connections.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_calendar_booking_idempotency"),
    )
    op.create_index("ix_calendar_bookings_tenant_id", "calendar_bookings", ["tenant_id"])
    op.create_index("ix_calendar_bookings_appointment_type_id", "calendar_bookings", ["appointment_type_id"])
    op.create_index("ix_calendar_bookings_calendar_connection_id", "calendar_bookings", ["calendar_connection_id"])
    op.create_index("ix_calendar_bookings_status", "calendar_bookings", ["status"])
    op.create_index("ix_calendar_bookings_tenant_start", "calendar_bookings", ["tenant_id", "start_at"])


def downgrade() -> None:
    op.drop_table("calendar_bookings")
    op.drop_table("calendar_appointment_types")
    op.drop_table("calendar_business_hours")
    op.drop_table("booking_configurations")
    op.drop_table("external_calendars")
    op.drop_table("calendar_oauth_states")
    op.drop_table("calendar_connections")
