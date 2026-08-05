"""Add the account control plane and nullable company session context.

Revision ID: 0014
Revises: 0013
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def normalize_email(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _preflight() -> None:
    bind = op.get_bind()
    emails: dict[str, object] = {}
    for user in bind.execute(
        sa.text("SELECT id, email FROM app_users WHERE email IS NOT NULL ORDER BY id")
    ).mappings():
        normalized = normalize_email(user["email"])
        previous = emails.get(normalized)
        if previous is not None:
            raise RuntimeError(
                "Account-Migration abgebrochen: Die normalisierte E-Mail "
                f"{normalized!r} wird von {previous} und {user['id']} verwendet."
            )
        emails[normalized] = user["id"]

    active_tenants = bind.execute(
        sa.text(
            """
            SELECT tenant.id, tenant.slug,
                   count(membership.id) FILTER (
                       WHERE membership.is_active = true
                         AND membership.role = 'owner'
                         AND user_account.is_active = true
                   ) AS owner_count
            FROM tenants tenant
            LEFT JOIN tenant_memberships membership
              ON membership.tenant_id = tenant.id
            LEFT JOIN app_users user_account
              ON user_account.id = membership.user_id
            WHERE tenant.status = 'active'
            GROUP BY tenant.id, tenant.slug
            ORDER BY tenant.slug
            """
        )
    ).mappings().all()
    invalid_tenants: list[str] = []
    for tenant in active_tenants:
        owner_count = tenant["owner_count"]
        if owner_count == 1:
            continue
        explicit_primary_count = bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM initial_app_setup setup
                JOIN tenant_memberships membership
                  ON membership.tenant_id = setup.tenant_id
                 AND membership.user_id = setup.user_id
                JOIN app_users user_account ON user_account.id = membership.user_id
                WHERE setup.completed_at IS NOT NULL
                  AND setup.tenant_id = :tenant_id
                  AND membership.is_active = true
                  AND membership.role = 'owner'
                  AND user_account.is_active = true
                """
            ),
            {"tenant_id": tenant["id"]},
        )
        if owner_count > 1 and explicit_primary_count == 1:
            continue
        invalid_tenants.append(
            f"{tenant['slug']} ({tenant['id']}): {owner_count} aktive Owner, "
            f"{explicit_primary_count} explizite Primärzuordnungen"
        )
    if invalid_tenants:
        inventory = ", ".join(
            invalid_tenants
        )
        raise RuntimeError(
            "Account-Migration abgebrochen: Jedes aktive Unternehmen braucht "
            "einen eindeutig bestimmbaren primären Administrator. Bei mehreren "
            "Alt-Ownern gilt nur die gespeicherte Ersteinrichtung als explizite "
            f"Zuordnung. Inventar: {inventory}"
        )


def _upgrade_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            CREATE FUNCTION current_actor_is_platform_admin() RETURNS boolean
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
                SELECT EXISTS (
                    SELECT 1
                    FROM app_users
                    WHERE id = NULLIF(current_setting('app.user_id', true), '')::uuid
                      AND is_active = true
                      AND platform_role IN ('owner', 'admin')
                )
            $$
            """
        )
    )

    for table in TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))

    op.execute(sa.text('DROP POLICY tenant_isolation ON "tenants"'))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON tenants
            USING (
                id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR current_actor_is_platform_admin()
            )
            WITH CHECK (
                id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR current_actor_is_platform_admin()
            )
            """
        )
    )
    op.execute(sa.text('DROP POLICY tenant_isolation ON "tenant_memberships"'))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON tenant_memberships
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                OR current_actor_is_platform_admin()
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR current_actor_is_platform_admin()
            )
            """
        )
    )

    for table in ("invitations", "audit_logs"):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        invitation_token_access = (
            " OR token_hash = NULLIF("
            "current_setting('app.invitation_token_hash', true), '')"
            if table == "invitations"
            else ""
        )
        expression = (
            "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
            f"OR current_actor_is_platform_admin(){invitation_token_access})"
        )
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({expression}) WITH CHECK ({expression})"
            )
        )


def upgrade() -> None:
    _preflight()
    bind = op.get_bind()

    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("legal_name", sa.String(200), nullable=True))
        batch.add_column(sa.Column("contact_name", sa.String(150), nullable=True))
        batch.add_column(sa.Column("contact_email", sa.String(320), nullable=True))
        batch.add_column(sa.Column("contact_phone", sa.String(50), nullable=True))
        batch.add_column(
            sa.Column(
                "is_demo", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=8),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    bind.execute(
        sa.text(
            """
            UPDATE tenants
            SET status = CASE status
                WHEN 'draft' THEN 'trial'
                WHEN 'inactive' THEN 'suspended'
                ELSE status
            END
            """
        )
    )

    with op.batch_alter_table("app_users") as batch:
        batch.add_column(sa.Column("normalized_email", sa.String(320), nullable=True))
        batch.add_column(sa.Column("platform_role", sa.String(20), nullable=True))
        batch.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    for user in bind.execute(
        sa.text("SELECT id, email FROM app_users WHERE email IS NOT NULL ORDER BY id")
    ).mappings():
        bind.execute(
            sa.text(
                "UPDATE app_users SET normalized_email = :normalized WHERE id = :id"
            ),
            {"normalized": normalize_email(user["email"]), "id": user["id"]},
        )
    bind.execute(
        sa.text(
            "UPDATE app_users SET platform_role = 'admin' "
            "WHERE is_platform_admin = true"
        )
    )
    with op.batch_alter_table("app_users") as batch:
        batch.create_unique_constraint(
            "uq_app_users_normalized_email", ["normalized_email"]
        )
        batch.create_index(
            "ix_app_users_normalized_email", ["normalized_email"], unique=True
        )
    op.create_index(
        "uq_app_users_single_platform_owner",
        "app_users",
        ["platform_role"],
        unique=True,
        postgresql_where=sa.text("platform_role = 'owner'"),
        sqlite_where=sa.text("platform_role = 'owner'"),
    )

    with op.batch_alter_table("tenant_memberships") as batch:
        batch.add_column(
            sa.Column(
                "is_primary_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.alter_column(
            "role",
            existing_type=sa.String(length=20),
            type_=sa.String(length=30),
            existing_nullable=False,
        )
    bind.execute(
        sa.text(
            """
            UPDATE tenant_memberships AS membership
            SET is_primary_admin = CASE
                    WHEN membership.role <> 'owner' OR membership.is_active = false
                        THEN false
                    WHEN (
                        SELECT count(*)
                        FROM tenant_memberships other
                        JOIN app_users user_account ON user_account.id = other.user_id
                        WHERE other.tenant_id = membership.tenant_id
                          AND other.role = 'owner'
                          AND other.is_active = true
                          AND user_account.is_active = true
                    ) = 1 THEN true
                    WHEN EXISTS (
                        SELECT 1
                        FROM initial_app_setup setup
                        WHERE setup.completed_at IS NOT NULL
                          AND setup.tenant_id = membership.tenant_id
                          AND setup.user_id = membership.user_id
                    ) THEN true
                    ELSE false
                END,
                role = CASE role
                    WHEN 'owner' THEN 'company_admin'
                    WHEN 'admin' THEN 'company_admin'
                    WHEN 'employee' THEN 'company_user'
                    WHEN 'member' THEN 'company_user'
                    ELSE role
                END
            """
        )
    )
    op.create_index(
        "uq_tenant_memberships_active_primary_admin",
        "tenant_memberships",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true AND is_primary_admin = true"),
        sqlite_where=sa.text("is_active = 1 AND is_primary_admin = 1"),
    )

    bind.execute(
        sa.text(
            "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP, "
            "revoke_reason = 'account_schema_upgrade' WHERE revoked_at IS NULL"
        )
    )
    with op.batch_alter_table("user_sessions") as batch:
        batch.drop_index("ix_user_sessions_tenant_id")
        batch.alter_column(
            "tenant_id",
            new_column_name="active_tenant_id",
            existing_type=sa.Uuid(),
            existing_nullable=False,
            nullable=True,
        )
    op.create_index(
        "ix_user_sessions_active_tenant_id",
        "user_sessions",
        ["active_tenant_id"],
        unique=False,
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("app_users.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("username", sa.String(150), nullable=False),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("tenant_role", sa.String(30), nullable=True),
        sa.Column("platform_role", sa.String(20), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_status", sa.String(30), nullable=False, server_default="pending"
        ),
        *timestamp_columns(),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    for column in (
        "tenant_id",
        "user_id",
        "created_by_user_id",
        "normalized_email",
        "token_hash",
        "expires_at",
    ):
        op.create_index(f"ix_invitations_{column}", "invitations", [column])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.UniqueConstraint(
            "token_hash", name="uq_password_reset_tokens_token_hash"
        ),
    )
    for column in ("user_id", "token_hash", "expires_at"):
        op.create_index(
            f"ix_password_reset_tokens_{column}",
            "password_reset_tokens",
            [column],
        )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=True
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("platform_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False, server_default="success"),
        sa.Column("metadata_before", sa.JSON(), nullable=True),
        sa.Column("metadata_after", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    for column in (
        "actor_user_id",
        "tenant_id",
        "action",
        "request_id",
        "created_at",
    ):
        op.create_index(f"ix_audit_logs_{column}", "audit_logs", [column])

    _upgrade_rls()


def _downgrade_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("audit_logs", "invitations"):
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"'))
        op.execute(sa.text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('DROP POLICY tenant_isolation ON "tenant_memberships"'))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON tenant_memberships
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
            )
            """
        )
    )
    op.execute(sa.text('DROP POLICY tenant_isolation ON "tenants"'))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON tenants
            USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )
    )
    for table in TENANT_TABLES:
        op.execute(sa.text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text("DROP FUNCTION current_actor_is_platform_admin()"))


def downgrade() -> None:
    _downgrade_rls()
    op.drop_table("audit_logs")
    op.drop_table("password_reset_tokens")
    op.drop_table("invitations")

    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM user_sessions WHERE active_tenant_id IS NULL")
    )
    with op.batch_alter_table("user_sessions") as batch:
        batch.drop_index("ix_user_sessions_active_tenant_id")
        batch.alter_column(
            "active_tenant_id",
            new_column_name="tenant_id",
            existing_type=sa.Uuid(),
            existing_nullable=True,
            nullable=False,
        )
    op.create_index(
        "ix_user_sessions_tenant_id", "user_sessions", ["tenant_id"], unique=False
    )

    op.drop_index(
        "uq_tenant_memberships_active_primary_admin",
        table_name="tenant_memberships",
    )
    bind.execute(
        sa.text(
            """
            UPDATE tenant_memberships
            SET role = CASE
                WHEN role = 'company_admin' AND is_primary_admin = true THEN 'owner'
                WHEN role = 'company_admin' THEN 'admin'
                ELSE 'employee'
            END
            """
        )
    )
    with op.batch_alter_table("tenant_memberships") as batch:
        batch.drop_column("is_primary_admin")
        batch.alter_column(
            "role",
            existing_type=sa.String(length=30),
            type_=sa.String(length=20),
            existing_nullable=False,
        )

    op.drop_index("uq_app_users_single_platform_owner", table_name="app_users")
    bind.execute(
        sa.text(
            "UPDATE app_users SET is_platform_admin = true "
            "WHERE platform_role IS NOT NULL"
        )
    )
    with op.batch_alter_table("app_users") as batch:
        batch.drop_index("ix_app_users_normalized_email")
        batch.drop_constraint("uq_app_users_normalized_email", type_="unique")
        batch.drop_column("must_change_password")
        batch.drop_column("platform_role")
        batch.drop_column("normalized_email")

    bind.execute(
        sa.text(
            """
            UPDATE tenants
            SET status = CASE status
                WHEN 'trial' THEN 'draft'
                WHEN 'suspended' THEN 'inactive'
                WHEN 'archived' THEN 'inactive'
                ELSE status
            END
            """
        )
    )
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("is_demo")
        batch.drop_column("contact_phone")
        batch.drop_column("contact_email")
        batch.drop_column("contact_name")
        batch.drop_column("legal_name")
        batch.alter_column(
            "status",
            existing_type=sa.String(length=20),
            type_=sa.String(length=8),
            existing_nullable=False,
        )
