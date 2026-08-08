"""Add credentials, memberships, sessions, throttles, and inbound routes.

Revision ID: 0010
Revises: 0009
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def timestamps() -> list[sa.Column]:
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


def upgrade() -> None:
    with op.batch_alter_table("app_users") as batch:
        batch.add_column(sa.Column("username", sa.String(150), nullable=True))
        batch.add_column(
            sa.Column("normalized_username", sa.String(150), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "password_hash",
                sa.String(500),
                nullable=False,
                server_default="!unusable!",
            )
        )
        batch.add_column(
            sa.Column(
                "is_platform_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.alter_column("email", existing_type=sa.String(320), nullable=True)

    bind = op.get_bind()
    users = bind.execute(
        sa.text("SELECT id, email FROM app_users ORDER BY id")
    ).mappings()
    used: set[str] = set()
    for user in users:
        source = user["email"] or f"user-{user['id']}"
        normalized = normalize_username(source)[:150]
        if normalized in used:
            suffix = str(user["id"]).replace("-", "")[:8]
            normalized = f"{normalized[:141]}-{suffix}"
        used.add(normalized)
        bind.execute(
            sa.text(
                "UPDATE app_users "
                "SET username = :username, normalized_username = :normalized "
                "WHERE id = :user_id"
            ),
            {
                "username": source[:150],
                "normalized": normalized,
                "user_id": user["id"],
            },
        )

    with op.batch_alter_table("app_users") as batch:
        batch.alter_column("username", existing_type=sa.String(150), nullable=False)
        batch.alter_column(
            "normalized_username", existing_type=sa.String(150), nullable=False
        )
        batch.create_unique_constraint(
            "uq_app_users_normalized_username", ["normalized_username"]
        )
        batch.create_index(
            "ix_app_users_normalized_username",
            ["normalized_username"],
            unique=True,
        )

    with op.batch_alter_table("tenant_memberships") as batch:
        batch.alter_column(
            "role",
            existing_type=sa.String(length=6),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
        batch.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    op.execute(
        sa.text("UPDATE tenant_memberships SET role = 'employee' WHERE role = 'member'")
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False
        ),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(100), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_tenant_id", "user_sessions", ["tenant_id"])
    op.create_index(
        "ix_user_sessions_idle_expires_at", "user_sessions", ["idle_expires_at"]
    )
    op.create_index(
        "ix_user_sessions_absolute_expires_at",
        "user_sessions",
        ["absolute_expires_at"],
    )
    op.create_index(
        "ix_user_sessions_user_active",
        "user_sessions",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "authentication_rate_limits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "failed_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "window_started_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.UniqueConstraint(
            "scope", "key_hash", name="uq_auth_rate_limit_scope_key"
        ),
    )

    op.create_table(
        "tenant_inbound_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("route_type", sa.String(30), nullable=False),
        sa.Column("normalized_identifier", sa.String(200), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        *timestamps(),
        sa.UniqueConstraint(
            "route_type",
            "normalized_identifier",
            name="uq_tenant_inbound_route",
        ),
    )
    op.create_index(
        "ix_tenant_inbound_routes_tenant_id",
        "tenant_inbound_routes",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_table("tenant_inbound_routes")
    op.drop_table("authentication_rate_limits")
    op.drop_table("user_sessions")
    op.execute(
        sa.text("UPDATE tenant_memberships SET role = 'member' WHERE role = 'employee'")
    )
    with op.batch_alter_table("tenant_memberships") as batch:
        batch.drop_column("is_active")
        batch.alter_column(
            "role",
            existing_type=sa.String(length=20),
            type_=sa.String(length=6),
            existing_nullable=False,
        )
    with op.batch_alter_table("app_users") as batch:
        batch.drop_index("ix_app_users_normalized_username")
        batch.drop_constraint(
            "uq_app_users_normalized_username", type_="unique"
        )
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
        batch.drop_column("password_changed_at")
        batch.drop_column("last_login_at")
        batch.drop_column("is_platform_admin")
        batch.drop_column("password_hash")
        batch.drop_column("normalized_username")
        batch.drop_column("username")
