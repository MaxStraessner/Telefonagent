"""Add deterministic conversation orchestration and availability snapshots.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch:
        batch.add_column(sa.Column("runtime_state", sa.String(length=50), nullable=False, server_default="idle"))
        batch.add_column(sa.Column("bootstrap_status", sa.String(length=50), nullable=False, server_default="not_started"))

    with op.batch_alter_table("calendar_bookings") as batch:
        batch.add_column(sa.Column("tool_call_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("conversation_session_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_calendar_bookings_conversation_session", "call_sessions", ["conversation_session_id"], ["id"]
        )
        batch.create_index("ix_calendar_bookings_conversation_session_id", ["conversation_session_id"])

    with op.batch_alter_table("tool_executions") as batch:
        batch.add_column(sa.Column("call_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("turn_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("continuation_mode", sa.String(length=50), nullable=False, server_default="sdk_automatic"))
        batch.add_column(sa.Column("result_sent_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("continuation_triggered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("continuation_response_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("booking_state_before", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("booking_state_after", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("runtime_state_before", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("runtime_state_after", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("success", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_tool_execution_session_call", ["call_session_id", "call_id"])

    op.create_table(
        "booking_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("call_session_id", sa.Uuid(), sa.ForeignKey("call_sessions.id"), nullable=False),
        sa.Column("state", sa.String(length=22), nullable=False, server_default="idle"),
        sa.Column("service_id", sa.Uuid(), sa.ForeignKey("services.id"), nullable=True),
        sa.Column("service_name", sa.String(length=150), nullable=True),
        sa.Column("appointment_type_id", sa.Uuid(), sa.ForeignKey("calendar_appointment_types.id"), nullable=True),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_slot_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_slot_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False, server_default="Europe/Berlin"),
        sa.Column("customer_name", sa.String(length=150), nullable=True),
        sa.Column("customer_phone", sa.String(length=50), nullable=True),
        sa.Column("customer_email", sa.String(length=320), nullable=True),
        sa.Column("booking_confirmed_by_customer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmation_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("appointment_id", sa.Uuid(), sa.ForeignKey("calendar_bookings.id"), nullable=True),
        sa.Column("external_event_id", sa.String(length=1000), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("call_session_id", name="uq_booking_conversation_session"),
    )
    op.create_index("ix_booking_conversations_tenant_id", "booking_conversations", ["tenant_id"])
    op.create_index("ix_booking_conversations_call_session_id", "booking_conversations", ["call_session_id"])
    op.create_index("ix_booking_conversations_state", "booking_conversations", ["state"])

    op.create_table(
        "availability_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("call_session_id", sa.Uuid(), sa.ForeignKey("call_sessions.id"), nullable=False),
        sa.Column("calendar_connection_id", sa.Uuid(), sa.ForeignKey("calendar_connections.id"), nullable=True),
        sa.Column("external_calendar_id", sa.String(length=1000), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("horizon_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_hours", sa.JSON(), nullable=False),
        sa.Column("busy_intervals", sa.JSON(), nullable=False),
        sa.Column("local_appointment_intervals", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.UniqueConstraint("call_session_id", name="uq_availability_snapshot_session"),
    )
    op.create_index("ix_availability_snapshots_tenant_id", "availability_snapshots", ["tenant_id"])
    op.create_index("ix_availability_snapshots_call_session_id", "availability_snapshots", ["call_session_id"])
    op.create_index("ix_availability_snapshots_valid_until", "availability_snapshots", ["valid_until"])


def downgrade() -> None:
    op.drop_table("availability_snapshots")
    op.drop_table("booking_conversations")
    with op.batch_alter_table("tool_executions") as batch:
        batch.drop_constraint("uq_tool_execution_session_call", type_="unique")
        for name in (
            "completed_at", "error_code", "success", "runtime_state_after", "runtime_state_before",
            "booking_state_after", "booking_state_before", "continuation_response_id",
            "continuation_triggered_at", "result_sent_at", "continuation_mode", "turn_id", "call_id",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("calendar_bookings") as batch:
        batch.drop_index("ix_calendar_bookings_conversation_session_id")
        batch.drop_constraint("fk_calendar_bookings_conversation_session", type_="foreignkey")
        batch.drop_column("conversation_session_id")
        batch.drop_column("tool_call_id")
    with op.batch_alter_table("call_sessions") as batch:
        batch.drop_column("bootstrap_status")
        batch.drop_column("runtime_state")
