"""Add redacted runtime manifest diagnostics to call sessions.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch:
        batch.add_column(sa.Column("runtime_manifest_digest", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("runtime_manifest_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("applied_configuration", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("configuration_diff", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("configuration_status", sa.String(length=50), nullable=False, server_default="pending")
        )


def downgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch:
        batch.drop_column("configuration_status")
        batch.drop_column("configuration_diff")
        batch.drop_column("applied_configuration")
        batch.drop_column("runtime_manifest_snapshot")
        batch.drop_column("runtime_manifest_digest")
