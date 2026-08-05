"""Add a durable lifecycle for browser Realtime call attempts.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("call_sessions") as batch:
        batch.add_column(sa.Column("call_attempt_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("provider_session_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("provider_request_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failure_phase", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("failure_retryable", sa.Boolean(), nullable=True))
        batch.create_unique_constraint(
            "uq_call_sessions_call_attempt_id", ["call_attempt_id"]
        )

    # Existing browser rows cannot represent a verifiably live provider session:
    # the old application never persisted an end transition. Reconcile them
    # explicitly instead of silently carrying the misleading "active" state.
    op.execute(
        sa.text(
            """
            UPDATE call_sessions
            SET status = 'abandoned',
                ended_at = COALESCE(ended_at, CURRENT_TIMESTAMP),
                failure_phase = 'migration',
                error_code = 'legacy_session_state_reconciled',
                failure_retryable = false
            WHERE channel = 'browser'
              AND status NOT IN ('ended', 'cancelled', 'failed', 'abandoned')
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE call_sessions
            SET status = 'active',
                ended_at = NULL
            WHERE error_code = 'legacy_session_state_reconciled'
              AND failure_phase = 'migration'
            """
        )
    )
    with op.batch_alter_table("call_sessions") as batch:
        batch.drop_constraint(
            "uq_call_sessions_call_attempt_id", type_="unique"
        )
        batch.drop_column("failure_retryable")
        batch.drop_column("http_status")
        batch.drop_column("error_code")
        batch.drop_column("failure_phase")
        batch.drop_column("connected_at")
        batch.drop_column("provider_request_id")
        batch.drop_column("provider_session_id")
        batch.drop_column("call_attempt_id")
