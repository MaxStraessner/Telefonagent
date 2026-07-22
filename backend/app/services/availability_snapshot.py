from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calendar.providers import BusyInterval
from app.core.config import Settings
from app.models import AvailabilitySnapshot, CalendarAppointmentType, ExternalCalendar, Service
from app.schemas.calendar import AvailableSlotResponse
from app.services.availability import (
    AvailabilityService,
    SlotSigner,
    aware_utc,
    calculate_available_slots,
    merge_busy_intervals,
    spoken_slot,
)

UTC = timezone.utc


def _dump(intervals: list[BusyInterval]) -> list[dict[str, str]]:
    return [{"start": aware_utc(item.start).isoformat(), "end": aware_utc(item.end).isoformat()} for item in intervals]


def _load(intervals: list[dict[str, str]]) -> list[BusyInterval]:
    return [BusyInterval(aware_utc(datetime.fromisoformat(item["start"])), aware_utc(datetime.fromisoformat(item["end"]))) for item in intervals]


class AvailabilitySnapshotService:
    """Per-call, privacy-minimal availability cache used only for preliminary answers."""

    def __init__(self, db: Session, settings: Settings, tenant_id: UUID, session_id: UUID, tenant_timezone: str):
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.availability = AvailabilityService(db, settings, tenant_id, tenant_timezone)

    def get(self) -> AvailabilitySnapshot | None:
        return self.db.scalar(select(AvailabilitySnapshot).where(
            AvailabilitySnapshot.tenant_id == self.tenant_id,
            AvailabilitySnapshot.call_session_id == self.session_id,
        ))

    async def refresh(self, start: datetime | None = None, end: datetime | None = None) -> AvailabilitySnapshot:
        now = datetime.now(UTC)
        horizon_start = aware_utc(start or now)
        horizon_end = aware_utc(end or (horizon_start + timedelta(days=self.settings.availability_snapshot_horizon_days)))
        external = await self.availability.external_busy_intervals(horizon_start, horizon_end)
        local = self.availability.local_busy_intervals(horizon_start, horizon_end)
        configuration, hours = self.availability.load_rules_for_snapshot()
        appointment_types = list(self.db.scalars(select(CalendarAppointmentType).where(
            CalendarAppointmentType.tenant_id == self.tenant_id,
            CalendarAppointmentType.is_active.is_(True),
            Service.is_active.is_(True),
        ).join(Service, Service.id == CalendarAppointmentType.service_id).options(
            selectinload(CalendarAppointmentType.service)
        )))
        calendars = list(self.db.scalars(select(ExternalCalendar).where(
            ExternalCalendar.tenant_id == self.tenant_id,
            (ExternalCalendar.is_selected_for_availability.is_(True))
            | (ExternalCalendar.is_selected_for_booking.is_(True)),
        )))
        snapshot = self.get()
        if snapshot is None:
            snapshot = AvailabilitySnapshot(tenant_id=self.tenant_id, call_session_id=self.session_id)
            self.db.add(snapshot)
        snapshot.timezone = configuration.timezone
        snapshot.horizon_start = horizon_start
        snapshot.horizon_end = horizon_end
        snapshot.generated_at = now
        snapshot.valid_until = now + timedelta(seconds=self.settings.availability_snapshot_ttl_seconds)
        target = next((item for item in calendars if item.is_selected_for_booking), None)
        snapshot.calendar_connection_id = target.calendar_connection_id if target else None
        snapshot.external_calendar_id = target.external_calendar_id if target else None
        snapshot.catalog = [{
            "service_id": str(item.service_id), "service_name": item.service.name,
            "appointment_type_id": str(item.id), "duration_minutes": item.service.duration_minutes,
            "buffer_before_minutes": item.buffer_before_minutes,
            "buffer_after_minutes": item.buffer_after_minutes,
            "appointment_format": item.location_type.value, "location": item.location_text,
        } for item in appointment_types]
        snapshot.business_hours = [
            {"weekday": item.weekday, "start": item.start_time.isoformat(), "end": item.end_time.isoformat()}
            for item in hours if item.is_active
        ]
        snapshot.calendar_ids = [item.external_calendar_id for item in calendars]
        snapshot.availability_status = "ready"
        snapshot.busy_intervals = _dump(external)
        snapshot.local_appointment_intervals = _dump(local)
        snapshot.error_code = None
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    async def ensure(self, start: datetime, end: datetime) -> tuple[AvailabilitySnapshot, bool]:
        snapshot = self.get()
        now = datetime.now(UTC)
        needs_refresh = (
            snapshot is None
            or aware_utc(snapshot.valid_until) <= now
            or aware_utc(snapshot.horizon_start) > aware_utc(start)
            or aware_utc(snapshot.horizon_end) < aware_utc(end)
        )
        if needs_refresh:
            refresh_start = min(aware_utc(start), now)
            refresh_end = max(aware_utc(end), refresh_start + timedelta(days=self.settings.availability_snapshot_horizon_days))
            snapshot = await self.refresh(refresh_start, refresh_end)
        return snapshot, needs_refresh

    async def search(
        self,
        appointment_type_id: UUID,
        search_start: datetime,
        search_end: datetime,
        *,
        preferred_day: date | None = None,
        preferred_time_range: str | None = None,
        maximum_results: int | None = None,
    ) -> tuple[str, list[AvailableSlotResponse], bool]:
        snapshot, refreshed = await self.ensure(search_start, search_end)
        configuration, hours, appointment_type = self.availability.load_rules(appointment_type_id)
        busy = merge_busy_intervals([*_load(snapshot.busy_intervals), *_load(snapshot.local_appointment_intervals)])
        slots = calculate_available_slots(
            configuration, hours, appointment_type, busy, search_start, search_end,
            now=datetime.now(UTC), preferred_day=preferred_day,
            preferred_time_range=preferred_time_range, maximum_results=maximum_results,
        )
        zone = ZoneInfo(configuration.timezone)
        signer = SlotSigner(self.settings)
        result: list[AvailableSlotResponse] = []
        for start, end in slots:
            spoken_date, spoken_time = spoken_slot(start, zone)
            result.append(AvailableSlotResponse(
                slot_id=signer.sign(self.tenant_id, appointment_type.id, start, end),
                start=start.astimezone(zone), end=end.astimezone(zone),
                spoken_date=spoken_date, spoken_time=spoken_time,
            ))
        return configuration.timezone, result, refreshed

    def add_confirmed_booking(self, start: datetime, end: datetime) -> None:
        snapshot = self.get()
        if snapshot is None:
            return
        local = merge_busy_intervals([*_load(snapshot.local_appointment_intervals), BusyInterval(aware_utc(start), aware_utc(end))])
        snapshot.local_appointment_intervals = _dump(local)
        self.db.commit()
