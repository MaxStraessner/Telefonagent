"""Add the one-time in-app setup state.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "initial_app_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    bind = op.get_bind()
    owner = bind.execute(
        sa.text(
            """
            SELECT membership.tenant_id, membership.user_id
            FROM tenant_memberships membership
            JOIN app_users user_account ON user_account.id = membership.user_id
            JOIN tenants tenant ON tenant.id = membership.tenant_id
            WHERE membership.is_active = true
              AND membership.role IN ('owner', 'admin')
              AND user_account.is_active = true
              AND user_account.password_hash <> '!unusable!'
              AND tenant.status = 'active'
            ORDER BY membership.created_at, membership.id
            LIMIT 1
            """
        )
    ).mappings().first()
    values: dict[str, object] = {"id": 1}
    if owner is not None:
        values.update(
            completed_at=bind.execute(sa.text("SELECT CURRENT_TIMESTAMP")).scalar(),
            tenant_id=owner["tenant_id"],
            user_id=owner["user_id"],
        )
    op.bulk_insert(
        sa.table(
            "initial_app_setup",
            sa.column("id", sa.Integer()),
            sa.column("completed_at", sa.DateTime(timezone=True)),
            sa.column("tenant_id", sa.Uuid()),
            sa.column("user_id", sa.Uuid()),
        ),
        [values],
    )


def downgrade() -> None:
    op.drop_table("initial_app_setup")
