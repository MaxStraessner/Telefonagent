"""Add prepared catalog and calendar metadata to availability snapshots.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("availability_snapshots") as batch:
        batch.add_column(sa.Column("catalog", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("calendar_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("availability_status", sa.String(length=50), nullable=False, server_default="ready"))


def downgrade() -> None:
    with op.batch_alter_table("availability_snapshots") as batch:
        batch.drop_column("availability_status")
        batch.drop_column("calendar_ids")
        batch.drop_column("catalog")
