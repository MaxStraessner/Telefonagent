"""Store normalized booking date and time resolutions.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("booking_conversations") as batch:
        batch.add_column(sa.Column("datetime_resolution_status", sa.String(length=50), nullable=True))
        batch.add_column(
            sa.Column(
                "datetime_resolution_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "datetime_explicit_year",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("booking_conversations") as batch:
        batch.drop_column("datetime_explicit_year")
        batch.drop_column("datetime_resolution_version")
        batch.drop_column("datetime_resolution_status")
