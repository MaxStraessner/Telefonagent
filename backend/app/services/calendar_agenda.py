from collections import defaultdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.providers import create_calendar_provider
from app.core.config import Settings
from app.models import CalendarBooking, CalendarConnection, CalendarConnectionStatus, ExternalCalendar
from app.schemas.calendar import CalendarAgendaResponse, CalendarEntryResponse
from app.services.availability import aware_utc
from app.services.calendar_connections import valid_access_token


async def calendar_agenda(
    db: Session, settings: Settings, tenant_id: UUID, start: datetime, end: datetime
) -> CalendarAgendaResponse:
    calendars = list(db.scalars(select(ExternalCalendar).where(
        ExternalCalendar.tenant_id == tenant_id,
        ExternalCalendar.is_selected_for_availability.is_(True),
    )))
    local = list(db.scalars(select(CalendarBooking).where(
        CalendarBooking.tenant_id == tenant_id,
        CalendarBooking.start_at < aware_utc(end),
        CalendarBooking.end_at > aware_utc(start),
    ).order_by(CalendarBooking.start_at)))
    entries = [CalendarEntryResponse(
        id=str(item.id),
        kind="platform",
        service_name=item.service_name_snapshot,
        customer_name=item.customer_name,
        start_at=aware_utc(item.start_at),
        end_at=aware_utc(item.end_at),
        duration_minutes=item.duration_minutes_snapshot,
        appointment_format=item.appointment_format_snapshot,
        location=item.location_snapshot,
        status=item.status.value,
        sync_status=item.sync_status,
        source=item.source.value,
        calendar_provider=item.provider.value,
        calendar_id=item.external_calendar_id,
        calendar_name=item.calendar_name_snapshot,
        external_event_id=item.external_event_id,
        buffer_before_minutes=item.buffer_before_minutes_snapshot,
        buffer_after_minutes=item.buffer_after_minutes_snapshot,
        created_at=item.created_at,
    ) for item in local]
    local_external_keys = {
        (item.provider.value, item.external_calendar_id, item.external_event_id)
        for item in local if item.external_event_id
    }
    by_connection: dict[UUID, list[ExternalCalendar]] = defaultdict(list)
    connected_calendar = False
    for calendar in calendars:
        by_connection[calendar.calendar_connection_id].append(calendar)
    for connection_id, selected in by_connection.items():
        connection = db.scalar(select(CalendarConnection).where(
            CalendarConnection.id == connection_id,
            CalendarConnection.tenant_id == tenant_id,
            CalendarConnection.connection_status == CalendarConnectionStatus.connected,
        ))
        if connection is None:
            continue
        connected_calendar = True
        provider = create_calendar_provider(connection.provider, settings)
        token = await valid_access_token(db, connection, settings)
        for calendar in selected:
            for event in await provider.list_events(token, calendar.external_calendar_id, aware_utc(start), aware_utc(end)):
                key = (connection.provider.value, calendar.external_calendar_id, event.event_id)
                if key in local_external_keys:
                    continue
                entries.append(CalendarEntryResponse(
                    id=f"external:{connection.provider.value}:{calendar.id}:{event.event_id}",
                    kind="external",
                    service_name=event.title,
                    customer_name="",
                    start_at=aware_utc(event.start),
                    end_at=aware_utc(event.end),
                    duration_minutes=max(1, int((aware_utc(event.end) - aware_utc(event.start)).total_seconds() // 60)),
                    appointment_format="external",
                    location=event.location,
                    status="external",
                    sync_status="external",
                    source="external_calendar",
                    calendar_provider=connection.provider.value,
                    calendar_id=calendar.external_calendar_id,
                    calendar_name=calendar.calendar_name,
                    external_event_id=event.event_id,
                    buffer_before_minutes=0,
                    buffer_after_minutes=0,
                ))
    entries.sort(key=lambda item: item.start_at)
    return CalendarAgendaResponse(calendar_connected=connected_calendar, entries=entries)
