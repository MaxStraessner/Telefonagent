from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.calendar.errors import CalendarError
from app.models import BookingConfiguration, CalendarBusinessHour, ExternalCalendar
from app.schemas.calendar import BookingConfigurationUpdate, CalendarSelectionUpdate


def get_or_create_booking_configuration(
    db: Session, tenant_id: UUID, tenant_timezone: str = "Europe/Berlin"
) -> tuple[BookingConfiguration, list[CalendarBusinessHour]]:
    try:
        ZoneInfo(tenant_timezone)
        timezone_name = tenant_timezone
    except ZoneInfoNotFoundError:
        timezone_name = "Europe/Berlin"
    configuration = db.scalar(
        select(BookingConfiguration).where(BookingConfiguration.tenant_id == tenant_id)
    )
    if configuration is None:
        configuration = BookingConfiguration(
            tenant_id=tenant_id,
            timezone=timezone_name,
            slot_interval_minutes=15,
            minimum_notice_minutes=120,
            maximum_booking_horizon_days=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            maximum_suggestions_per_request=3,
        )
        db.add(configuration)
        for weekday in range(5):
            db.add(
                CalendarBusinessHour(
                    tenant_id=tenant_id,
                    weekday=weekday,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    is_active=True,
                )
            )
        db.commit()
        db.refresh(configuration)
    elif configuration.timezone != timezone_name:
        configuration.timezone = timezone_name
        db.commit()
        db.refresh(configuration)
    hours = list(
        db.scalars(
            select(CalendarBusinessHour)
            .where(CalendarBusinessHour.tenant_id == tenant_id)
            .order_by(CalendarBusinessHour.weekday, CalendarBusinessHour.start_time)
        )
    )
    return configuration, hours


def validate_business_hours(payload: BookingConfigurationUpdate) -> None:
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise CalendarError("invalid_timezone", "Die angegebene Zeitzone ist ungültig.") from exc
    by_day: dict[int, list[tuple[time, time]]] = {}
    for item in payload.business_hours:
        if not item.is_active:
            continue
        by_day.setdefault(item.weekday, []).append((item.start_time, item.end_time))
    for windows in by_day.values():
        windows.sort()
        for previous, current in zip(windows, windows[1:]):
            if current[0] < previous[1]:
                raise CalendarError("invalid_business_hours", "Geschäftszeitfenster dürfen sich nicht überschneiden.")


def update_booking_configuration(
    db: Session, tenant_id: UUID, tenant_timezone: str, payload: BookingConfigurationUpdate
) -> tuple[BookingConfiguration, list[CalendarBusinessHour]]:
    validate_business_hours(payload)
    configuration, _ = get_or_create_booking_configuration(db, tenant_id, tenant_timezone)
    configuration.timezone = tenant_timezone
    for field in (
        "slot_interval_minutes",
        "minimum_notice_minutes",
        "maximum_booking_horizon_days",
        "buffer_before_minutes",
        "buffer_after_minutes",
        "maximum_suggestions_per_request",
    ):
        setattr(configuration, field, getattr(payload, field))
    db.execute(delete(CalendarBusinessHour).where(CalendarBusinessHour.tenant_id == tenant_id))
    db.flush()
    for item in payload.business_hours:
        db.add(CalendarBusinessHour(tenant_id=tenant_id, **item.model_dump()))
    db.commit()
    db.refresh(configuration)
    return get_or_create_booking_configuration(db, tenant_id, tenant_timezone)


def update_calendar_selection(
    db: Session, tenant_id: UUID, payload: CalendarSelectionUpdate
) -> list[ExternalCalendar]:
    requested_ids = [item.calendar_id for item in payload.calendars]
    calendars = list(
        db.scalars(
            select(ExternalCalendar).where(
                ExternalCalendar.tenant_id == tenant_id,
                ExternalCalendar.id.in_(requested_ids),
            )
        )
    )
    if len(calendars) != len(set(requested_ids)):
        raise CalendarError("tenant_access_denied", "Mindestens ein Kalender gehört nicht zu diesem Account.")
    booking_targets = [item for item in payload.calendars if item.is_selected_for_booking]
    if len(booking_targets) != 1:
        raise CalendarError("calendar_not_selected", "Genau ein Zielkalender muss ausgewählt sein.")
    by_id = {item.id: item for item in calendars}
    target = by_id[booking_targets[0].calendar_id]
    if not target.can_write:
        raise CalendarError("booking_calendar_not_writable", "Der Zielkalender besitzt keine Schreibberechtigung.")
    if not any(item.is_selected_for_availability for item in payload.calendars):
        raise CalendarError("calendar_not_selected", "Mindestens ein Kalender muss die Verfügbarkeit blockieren.")
    all_tenant_calendars = list(
        db.scalars(select(ExternalCalendar).where(ExternalCalendar.tenant_id == tenant_id))
    )
    selection = {item.calendar_id: item for item in payload.calendars}
    for calendar in all_tenant_calendars:
        calendar.is_selected_for_booking = False
    db.flush()
    for calendar in all_tenant_calendars:
        selected = selection.get(calendar.id)
        calendar.is_selected_for_availability = bool(selected and selected.is_selected_for_availability)
        calendar.is_selected_for_booking = bool(selected and selected.is_selected_for_booking)
    db.commit()
    return all_tenant_calendars
