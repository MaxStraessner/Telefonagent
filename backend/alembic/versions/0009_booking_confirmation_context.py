"""Bind booking confirmations to a server-side context.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("booking_conversations") as batch:
        batch.add_column(sa.Column("selected_slot_id", sa.String(length=4000), nullable=True))
        batch.add_column(
            sa.Column("offered_slot_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("confirmation_digest", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("confirmation_classification", sa.String(length=50), nullable=True)
        )
        batch.add_column(
            sa.Column("confirmation_decided_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "confirmation_transition_reason",
                sa.String(length=100),
                nullable=True,
            )
        )
    op.execute(
        sa.text(
            "UPDATE booking_conversations "
            "SET state = 'date_time_resolving', "
            "booking_confirmed_by_customer = false "
            "WHERE state IN ('date_time_required', 'confirmation_required')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("booking_conversations") as batch:
        batch.drop_column("confirmation_transition_reason")
        batch.drop_column("confirmation_decided_at")
        batch.drop_column("confirmation_classification")
        batch.drop_column("confirmation_digest")
        batch.drop_column("offered_slot_ids")
        batch.drop_column("selected_slot_id")
