"""Add PostgreSQL tenant integrity constraints and row-level security.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RELATIONS = (
    ("external_calendars", "calendar_connection_id", "calendar_connections"),
    ("calendar_appointment_types", "service_id", "services"),
    ("calendar_bookings", "service_id", "services"),
    ("calendar_bookings", "appointment_type_id", "calendar_appointment_types"),
    ("calendar_bookings", "calendar_connection_id", "calendar_connections"),
    ("calendar_bookings", "conversation_session_id", "call_sessions"),
    ("appointments", "service_id", "services"),
    ("appointments", "staff_member_id", "staff_members"),
    ("tool_executions", "call_session_id", "call_sessions"),
    ("booking_conversations", "call_session_id", "call_sessions"),
    ("booking_conversations", "service_id", "services"),
    (
        "booking_conversations",
        "appointment_type_id",
        "calendar_appointment_types",
    ),
    ("booking_conversations", "appointment_id", "calendar_bookings"),
    ("availability_snapshots", "call_session_id", "call_sessions"),
    (
        "availability_snapshots",
        "calendar_connection_id",
        "calendar_connections",
    ),
)

MEMBERSHIP_RELATIONS = (
    ("agent_configuration_audits", "changed_by_user_id"),
    ("calendar_connections", "created_by_user_id"),
    ("calendar_oauth_states", "user_id"),
)

TENANT_TABLES = (
    "tenants",
    "tenant_settings",
    "tenant_memberships",
    "tenant_inbound_routes",
    "agent_configurations",
    "agent_topics",
    "agent_behavior_rules",
    "agent_knowledge_profiles",
    "agent_faqs",
    "agent_knowledge_services",
    "agent_business_hours",
    "agent_capabilities",
    "agent_configuration_audits",
    "calendar_connections",
    "calendar_oauth_states",
    "external_calendars",
    "booking_configurations",
    "calendar_business_hours",
    "calendar_appointment_types",
    "calendar_bookings",
    "locations",
    "services",
    "staff_members",
    "appointments",
    "call_sessions",
    "tool_executions",
    "booking_conversations",
    "availability_snapshots",
)


def _constraint_name(table: str, column: str) -> str:
    return f"fk_{table}_{column}_tenant"


def _preflight() -> None:
    bind = op.get_bind()
    for table, column, target in RELATIONS:
        mismatch = bind.scalar(
            sa.text(
                f"SELECT count(*) FROM {table} child "
                f"JOIN {target} parent ON parent.id = child.{column} "
                "WHERE child.tenant_id <> parent.tenant_id"
            )
        )
        if mismatch:
            raise RuntimeError(
                f"Tenant-Konflikt: {table}.{column} enthält {mismatch} "
                f"mandantenfremde Verknüpfungen."
            )
    for table, column in MEMBERSHIP_RELATIONS:
        mismatch = bind.scalar(
            sa.text(
                f"SELECT count(*) FROM {table} child "
                "LEFT JOIN tenant_memberships membership "
                f"ON membership.user_id = child.{column} "
                "AND membership.tenant_id = child.tenant_id "
                f"WHERE child.{column} IS NOT NULL AND membership.id IS NULL"
            )
        )
        if mismatch:
            raise RuntimeError(
                f"Tenant-Konflikt: {table}.{column} besitzt {mismatch} "
                "Ersteller ohne Tenant-Mitgliedschaft."
            )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()

    referenced_tables = sorted({target for _, _, target in RELATIONS})
    for table in referenced_tables:
        op.create_unique_constraint(f"uq_{table}_tenant_id_id", table, ["tenant_id", "id"])

    for table, column, target in RELATIONS:
        op.create_foreign_key(
            _constraint_name(table, column),
            table,
            target,
            ["tenant_id", column],
            ["tenant_id", "id"],
        )

    for table, column in MEMBERSHIP_RELATIONS:
        op.create_foreign_key(
            _constraint_name(table, column),
            table,
            "tenant_memberships",
            ["tenant_id", column],
            ["tenant_id", "user_id"],
        )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION resolve_calendar_oauth_tenant(
                p_state_hash text,
                p_provider text
            ) RETURNS uuid
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
                SELECT tenant_id
                FROM calendar_oauth_states
                WHERE state_hash = p_state_hash
                  AND provider = p_provider
                  AND consumed_at IS NULL
                  AND expires_at > now()
                LIMIT 1
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION resolve_inbound_route_tenant(
                p_route_type text,
                p_normalized_identifier text
            ) RETURNS uuid
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
                SELECT route.tenant_id
                FROM tenant_inbound_routes route
                JOIN tenants tenant ON tenant.id = route.tenant_id
                WHERE route.route_type = p_route_type
                  AND route.normalized_identifier = p_normalized_identifier
                  AND route.is_active = true
                  AND tenant.status = 'active'
                LIMIT 1
            $$
            """
        )
    )

    for table in TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        if table == "tenants":
            expression = (
                "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
            )
        elif table == "tenant_memberships":
            expression = (
                "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
                "OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
            )
        else:
            expression = (
                "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
            )
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({expression}) WITH CHECK ({expression})"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(TENANT_TABLES):
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"'))
        op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    op.execute(sa.text("DROP FUNCTION IF EXISTS resolve_calendar_oauth_tenant(text, text)"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS resolve_inbound_route_tenant(text, text)"))

    for table, column in reversed(MEMBERSHIP_RELATIONS):
        op.drop_constraint(_constraint_name(table, column), table, type_="foreignkey")
    for table, column, _target in reversed(RELATIONS):
        op.drop_constraint(_constraint_name(table, column), table, type_="foreignkey")
    referenced_tables = sorted({target for _, _, target in RELATIONS}, reverse=True)
    for table in referenced_tables:
        op.drop_constraint(f"uq_{table}_tenant_id_id", table, type_="unique")
