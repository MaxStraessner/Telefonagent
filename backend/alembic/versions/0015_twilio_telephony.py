"""Add tenant Twilio routes and telephone call lookup.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight() -> None:
    bind = op.get_bind()
    duplicate_routes = bind.execute(
        sa.text(
            """
            SELECT tenant_id, route_type, count(*) AS route_count
            FROM tenant_inbound_routes
            GROUP BY tenant_id, route_type
            HAVING count(*) > 1
            """
        )
    ).mappings().all()
    if duplicate_routes:
        inventory = ", ".join(
            f"{row['tenant_id']}:{row['route_type']}={row['route_count']}"
            for row in duplicate_routes
        )
        raise RuntimeError(
            "Twilio-Migration abgebrochen: Pro Tenant und Routentyp darf nur "
            f"eine eingehende Route existieren. Inventar: {inventory}"
        )

    duplicate_provider_sessions = bind.execute(
        sa.text(
            """
            SELECT channel, provider_session_id, count(*) AS session_count
            FROM call_sessions
            WHERE provider_session_id IS NOT NULL
            GROUP BY channel, provider_session_id
            HAVING count(*) > 1
            """
        )
    ).mappings().all()
    if duplicate_provider_sessions:
        inventory = ", ".join(
            f"{row['channel']}:{row['provider_session_id']}={row['session_count']}"
            for row in duplicate_provider_sessions
        )
        raise RuntimeError(
            "Twilio-Migration abgebrochen: Provider-Sitzungen sind nicht "
            f"eindeutig. Inventar: {inventory}"
        )


def upgrade() -> None:
    _preflight()
    with op.batch_alter_table("tenant_inbound_routes") as batch:
        batch.add_column(sa.Column("provider", sa.String(length=30), nullable=True))
        batch.add_column(
            sa.Column("provider_resource_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "provider_sync_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(
            sa.Column("provider_synced_url", sa.String(length=500), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "provider_synced_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column("provider_error_code", sa.String(length=100), nullable=True)
        )
        batch.create_unique_constraint(
            "uq_tenant_inbound_route_tenant_type", ["tenant_id", "route_type"]
        )
        batch.create_unique_constraint(
            "uq_tenant_inbound_route_provider_resource",
            ["provider", "provider_resource_id"],
        )

    with op.batch_alter_table("call_sessions") as batch:
        batch.create_unique_constraint(
            "uq_call_sessions_channel_provider_session",
            ["channel", "provider_session_id"],
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE FUNCTION resolve_telephone_call_tenant(
                    p_provider_session_id text
                ) RETURNS uuid
                LANGUAGE sql
                STABLE
                SECURITY DEFINER
                SET search_path = public, pg_temp
                AS $$
                    SELECT tenant_id
                    FROM call_sessions
                    WHERE channel = 'telephone'
                      AND provider_session_id = p_provider_session_id
                    LIMIT 1
                $$
                """
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("DROP FUNCTION IF EXISTS resolve_telephone_call_tenant(text)")
        )
    with op.batch_alter_table("call_sessions") as batch:
        batch.drop_constraint(
            "uq_call_sessions_channel_provider_session", type_="unique"
        )
    with op.batch_alter_table("tenant_inbound_routes") as batch:
        batch.drop_constraint(
            "uq_tenant_inbound_route_provider_resource", type_="unique"
        )
        batch.drop_constraint(
            "uq_tenant_inbound_route_tenant_type", type_="unique"
        )
        batch.drop_column("provider_error_code")
        batch.drop_column("provider_synced_at")
        batch.drop_column("provider_synced_url")
        batch.drop_column("provider_sync_status")
        batch.drop_column("provider_resource_id")
        batch.drop_column("provider")
