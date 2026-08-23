"""Add persistent tenant call history and analysis fields.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_inbound_routes") as batch:
        batch.create_unique_constraint(
            "uq_tenant_inbound_routes_tenant_id_id", ["tenant_id", "id"]
        )

    with op.batch_alter_table("call_sessions") as batch:
        batch.add_column(sa.Column("phone_number_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("caller_phone", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("caller_name", sa.String(length=150), nullable=True))
        batch.add_column(sa.Column("duration_seconds", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "inbox_status",
                sa.String(length=20),
                nullable=False,
                server_default="done",
            )
        )
        batch.add_column(
            sa.Column(
                "transcript", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            )
        )
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("category", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("result", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "action_required", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "action_items", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            )
        )
        batch.add_column(
            sa.Column(
                "priority", sa.String(length=20), nullable=False, server_default="normal"
            )
        )
        batch.add_column(
            sa.Column(
                "analysis_status",
                sa.String(length=20),
                nullable=False,
                server_default="not_requested",
            )
        )
        batch.add_column(
            sa.Column("analysis_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("analysis_error_code", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.create_foreign_key(
            "fk_call_sessions_phone_number_tenant",
            "tenant_inbound_routes",
            ["tenant_id", "phone_number_id"],
            ["tenant_id", "id"],
        )
        batch.create_index(
            "ix_call_sessions_tenant_started_at", ["tenant_id", "started_at"]
        )
        batch.create_index("ix_call_sessions_inbox_status", ["inbox_status"])
        batch.create_index("ix_call_sessions_analysis_status", ["analysis_status"])

    op.execute(
        sa.text(
            "UPDATE call_sessions SET analysis_status = 'pending', inbox_status = 'open' "
            "WHERE channel = 'telephone' AND ended_at IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch:
        batch.drop_index("ix_call_sessions_analysis_status")
        batch.drop_index("ix_call_sessions_inbox_status")
        batch.drop_index("ix_call_sessions_tenant_started_at")
        batch.drop_constraint("fk_call_sessions_phone_number_tenant", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("analyzed_at")
        batch.drop_column("analysis_error_code")
        batch.drop_column("analysis_attempts")
        batch.drop_column("analysis_status")
        batch.drop_column("priority")
        batch.drop_column("action_items")
        batch.drop_column("action_required")
        batch.drop_column("result")
        batch.drop_column("category")
        batch.drop_column("summary")
        batch.drop_column("transcript")
        batch.drop_column("inbox_status")
        batch.drop_column("duration_seconds")
        batch.drop_column("caller_name")
        batch.drop_column("caller_phone")
        batch.drop_column("phone_number_id")

    with op.batch_alter_table("tenant_inbound_routes") as batch:
        batch.drop_constraint(
            "uq_tenant_inbound_routes_tenant_id_id", type_="unique"
        )
