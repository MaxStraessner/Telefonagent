"""Link services to appointment types and snapshot confirmed bookings.

Revision ID: 0004
Revises: 0003
"""

import uuid
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    services = sa.Table("services", metadata, autoload_with=connection)
    appointment_types = sa.Table("calendar_appointment_types", metadata, autoload_with=connection)
    bookings = sa.Table("calendar_bookings", metadata, autoload_with=connection)

    with op.batch_alter_table("calendar_appointment_types") as batch:
        batch.add_column(sa.Column("service_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_calendar_appointment_types_service", "services", ["service_id"], ["id"])
        batch.create_index("ix_calendar_appointment_types_service_id", ["service_id"])

    # Reflect the new column. Alembic migrations run once, while the matching lookup
    # additionally prevents duplicate service rows if data was prepared beforehand.
    metadata = sa.MetaData()
    services = sa.Table("services", metadata, autoload_with=connection)
    appointment_types = sa.Table("calendar_appointment_types", metadata, autoload_with=connection)
    rows = connection.execute(sa.select(appointment_types)).mappings().all()
    for item in rows:
        service_id = connection.execute(
            sa.select(services.c.id).where(
                services.c.tenant_id == item["tenant_id"],
                sa.func.lower(services.c.name) == item["name"].strip().lower(),
            )
        ).scalar_one_or_none()
        if service_id is None:
            service_id = uuid.uuid4()
            connection.execute(
                services.insert().values(
                    id=service_id,
                    tenant_id=item["tenant_id"],
                    name=item["name"].strip(),
                    description=item["description"] or "",
                    duration_minutes=item["duration_minutes"],
                    is_active=item["is_active"],
                )
            )
        connection.execute(
            appointment_types.update().where(appointment_types.c.id == item["id"]).values(service_id=service_id)
        )

    with op.batch_alter_table("calendar_appointment_types") as batch:
        batch.alter_column("service_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_constraint("uq_calendar_appointment_type_name", type_="unique")

    with op.batch_alter_table("calendar_bookings") as batch:
        batch.add_column(sa.Column("service_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("sync_status", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("service_name_snapshot", sa.String(length=150), nullable=True))
        batch.add_column(sa.Column("duration_minutes_snapshot", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("buffer_before_minutes_snapshot", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("buffer_after_minutes_snapshot", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("blocked_start_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("blocked_end_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("appointment_format_snapshot", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("location_snapshot", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("calendar_name_snapshot", sa.String(length=300), nullable=True))
        batch.create_foreign_key("fk_calendar_bookings_service", "services", ["service_id"], ["id"])
        batch.create_index("ix_calendar_bookings_service_id", ["service_id"])
        batch.create_index("ix_calendar_bookings_sync_status", ["sync_status"])

    metadata = sa.MetaData()
    appointment_types = sa.Table("calendar_appointment_types", metadata, autoload_with=connection)
    bookings = sa.Table("calendar_bookings", metadata, autoload_with=connection)
    calendars = sa.Table("external_calendars", metadata, autoload_with=connection)
    services = sa.Table("services", metadata, autoload_with=connection)
    joined = connection.execute(
        sa.select(
            bookings.c.id,
            bookings.c.start_at,
            bookings.c.end_at,
            bookings.c.status,
            bookings.c.external_calendar_id,
            appointment_types.c.service_id,
            appointment_types.c.buffer_before_minutes,
            appointment_types.c.buffer_after_minutes,
            appointment_types.c.location_type,
            appointment_types.c.location_text,
            services.c.name,
            services.c.duration_minutes,
            calendars.c.calendar_name,
        )
        .join(appointment_types, appointment_types.c.id == bookings.c.appointment_type_id)
        .join(services, services.c.id == appointment_types.c.service_id)
        .outerjoin(
            calendars,
            sa.and_(
                calendars.c.tenant_id == bookings.c.tenant_id,
                calendars.c.external_calendar_id == bookings.c.external_calendar_id,
            ),
        )
    ).mappings().all()
    for item in joined:
        before = item["buffer_before_minutes"] or 0
        after = item["buffer_after_minutes"] or 0
        sync_status = "synced" if item["status"] == "confirmed" else ("failed" if item["status"] == "failed" else "pending")
        connection.execute(
            bookings.update().where(bookings.c.id == item["id"]).values(
                service_id=item["service_id"],
                sync_status=sync_status,
                service_name_snapshot=item["name"],
                duration_minutes_snapshot=item["duration_minutes"],
                buffer_before_minutes_snapshot=before,
                buffer_after_minutes_snapshot=after,
                blocked_start_at=item["start_at"] - timedelta(minutes=before),
                blocked_end_at=item["end_at"] + timedelta(minutes=after),
                appointment_format_snapshot=item["location_type"],
                location_snapshot=item["location_text"] or "",
                calendar_name_snapshot=item["calendar_name"] or "",
            )
        )

    with op.batch_alter_table("calendar_bookings") as batch:
        for name, column_type in (
            ("service_id", sa.Uuid()),
            ("sync_status", sa.String(length=30)),
            ("service_name_snapshot", sa.String(length=150)),
            ("duration_minutes_snapshot", sa.Integer()),
            ("buffer_before_minutes_snapshot", sa.Integer()),
            ("buffer_after_minutes_snapshot", sa.Integer()),
            ("blocked_start_at", sa.DateTime(timezone=True)),
            ("blocked_end_at", sa.DateTime(timezone=True)),
            ("appointment_format_snapshot", sa.String(length=30)),
            ("location_snapshot", sa.String(length=300)),
            ("calendar_name_snapshot", sa.String(length=300)),
        ):
            batch.alter_column(name, existing_type=column_type, nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("calendar_bookings") as batch:
        batch.drop_index("ix_calendar_bookings_sync_status")
        batch.drop_index("ix_calendar_bookings_service_id")
        batch.drop_constraint("fk_calendar_bookings_service", type_="foreignkey")
        for name in (
            "calendar_name_snapshot", "location_snapshot", "appointment_format_snapshot",
            "blocked_end_at", "blocked_start_at", "buffer_after_minutes_snapshot",
            "buffer_before_minutes_snapshot", "duration_minutes_snapshot", "service_name_snapshot",
            "sync_status", "service_id",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("calendar_appointment_types") as batch:
        batch.create_unique_constraint("uq_calendar_appointment_type_name", ["tenant_id", "name"])
        batch.drop_index("ix_calendar_appointment_types_service_id")
        batch.drop_constraint("fk_calendar_appointment_types_service", type_="foreignkey")
        batch.drop_column("service_id")
