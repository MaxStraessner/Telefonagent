import asyncio
import base64
import hashlib
import hmac
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calendar.errors import CalendarError
from app.calendar.providers import BusyInterval, create_calendar_provider
from app.core.config import Settings
from app.core.encryption import CalendarTokenCipher
from app.models import (
    BookingConfiguration,
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBookingStatus,
    CalendarBusinessHour,
    CalendarConnection,
    CalendarConnectionStatus,
    ExternalCalendar,
)
from app.schemas.calendar import AvailableSlotResponse
from app.services.calendar_configuration import get_or_create_booking_configuration
from app.services.calendar_connections import valid_access_token

UTC = timezone.utc
GERMAN_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
GERMAN_MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def merge_busy_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
    normalized = sorted(
        (BusyInterval(aware_utc(item.start), aware_utc(item.end)) for item in intervals if item.end > item.start),
        key=lambda item: item.start,
    )
    merged: list[BusyInterval] = []
    for interval in normalized:
        if merged and interval.start <= merged[-1].end:
            merged[-1] = BusyInterval(merged[-1].start, max(merged[-1].end, interval.end))
        else:
            merged.append(interval)
    return merged


def valid_local_datetime(day: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    candidate = datetime.combine(day, local_time, tzinfo=zone)
    roundtrip = candidate.astimezone(UTC).astimezone(zone)
    if roundtrip.date() != day or roundtrip.replace(tzinfo=None).time() != local_time:
        return None
    return candidate


def business_intervals(
    hours: list[CalendarBusinessHour], start: datetime, end: datetime, zone: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    by_day: dict[int, list[CalendarBusinessHour]] = defaultdict(list)
    for item in hours:
        if item.is_active:
            by_day[item.weekday].append(item)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    current = local_start.date()
    intervals: list[tuple[datetime, datetime]] = []
    while current <= local_end.date():
        for window in sorted(by_day[current.weekday()], key=lambda item: item.start_time):
            window_start = valid_local_datetime(current, window.start_time, zone)
            window_end = valid_local_datetime(current, window.end_time, zone)
            if window_start and window_end and window_end > window_start:
                clipped_start = max(window_start.astimezone(UTC), start)
                clipped_end = min(window_end.astimezone(UTC), end)
                if clipped_end > clipped_start:
                    intervals.append((clipped_start, clipped_end))
        current += timedelta(days=1)
    return intervals


def ceil_to_grid(value: datetime, interval_minutes: int, zone: ZoneInfo) -> datetime:
    local = value.astimezone(zone)
    minute_of_day = local.hour * 60 + local.minute
    rounded = math.ceil(minute_of_day / interval_minutes) * interval_minutes
    day = local.date() + timedelta(days=rounded // (24 * 60))
    rounded %= 24 * 60
    candidate = valid_local_datetime(day, time(rounded // 60, rounded % 60), zone)
    if candidate is None:
        return ceil_to_grid(value + timedelta(minutes=interval_minutes), interval_minutes, zone)
    return candidate.astimezone(UTC)


def preferred_time_matches(value: datetime, zone: ZoneInfo, preferred: str | None) -> bool:
    if preferred is None:
        return True
    hour = value.astimezone(zone).hour
    return (preferred == "morning" and 6 <= hour < 12) or (
        preferred == "afternoon" and 12 <= hour < 18
    ) or (preferred == "evening" and 18 <= hour < 24)


def overlaps(start: datetime, end: datetime, busy: list[BusyInterval]) -> bool:
    return any(start < item.end and end > item.start for item in busy)


def calculate_available_slots(
    configuration: BookingConfiguration,
    hours: list[CalendarBusinessHour],
    appointment_type: CalendarAppointmentType,
    busy_intervals: list[BusyInterval],
    search_start: datetime,
    search_end: datetime,
    *,
    now: datetime,
    preferred_day: date | None = None,
    preferred_time_range: str | None = None,
    maximum_results: int | None = None,
) -> list[tuple[datetime, datetime]]:
    try:
        zone = ZoneInfo(configuration.timezone)
    except ZoneInfoNotFoundError as exc:
        raise CalendarError("invalid_timezone", "Die konfigurierte Zeitzone ist ungültig.") from exc
    requested_start = aware_utc(search_start)
    requested_end = aware_utc(search_end)
    minimum_start = aware_utc(now) + timedelta(minutes=configuration.minimum_notice_minutes)
    maximum_end = aware_utc(now) + timedelta(days=configuration.maximum_booking_horizon_days)
    effective_start = max(requested_start, minimum_start)
    effective_end = min(requested_end, maximum_end)
    if effective_end <= effective_start:
        return []
    busy = merge_busy_intervals(busy_intervals)
    type_before = timedelta(
        minutes=appointment_type.buffer_before_minutes
        if appointment_type.buffer_before_minutes is not None
        else configuration.buffer_before_minutes
    )
    type_after = timedelta(
        minutes=appointment_type.buffer_after_minutes
        if appointment_type.buffer_after_minutes is not None
        else configuration.buffer_after_minutes
    )
    duration = timedelta(
        minutes=appointment_type.service.duration_minutes if appointment_type.service else appointment_type.duration_minutes
    )
    limit = min(maximum_results or configuration.maximum_suggestions_per_request, configuration.maximum_suggestions_per_request)
    results: list[tuple[datetime, datetime]] = []
    for window_start, window_end in business_intervals(hours, effective_start, effective_end, zone):
        candidate = ceil_to_grid(window_start + type_before, configuration.slot_interval_minutes, zone)
        while candidate + duration + type_after <= window_end:
            candidate_end = candidate + duration
            local_candidate = candidate.astimezone(zone)
            if (
                (preferred_day is None or local_candidate.date() == preferred_day)
                and preferred_time_matches(candidate, zone, preferred_time_range)
                and not overlaps(candidate - type_before, candidate_end + type_after, busy)
            ):
                results.append((candidate, candidate_end))
                if len(results) >= limit:
                    return results
            candidate += timedelta(minutes=configuration.slot_interval_minutes)
    return results


@dataclass(frozen=True)
class SlotClaim:
    tenant_id: UUID
    appointment_type_id: UUID
    start: datetime
    end: datetime


class SlotSigner:
    def __init__(self, settings: Settings):
        self.key = CalendarTokenCipher(settings.calendar_token_encryption_key).signing_key

    def sign(self, tenant_id: UUID, appointment_type_id: UUID, start: datetime, end: datetime) -> str:
        payload = json.dumps(
            {
                "tenant_id": str(tenant_id),
                "appointment_type_id": str(appointment_type_id),
                "start": aware_utc(start).isoformat(),
                "end": aware_utc(end).isoformat(),
                "expires_at": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = base64.urlsafe_b64encode(hmac.new(self.key, encoded.encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
        return f"{encoded}.{signature}"

    def verify(self, value: str) -> SlotClaim:
        try:
            encoded, supplied = value.split(".", 1)
            expected = base64.urlsafe_b64encode(
                hmac.new(self.key, encoded.encode("ascii"), hashlib.sha256).digest()
            ).decode("ascii").rstrip("=")
            if not hmac.compare_digest(expected, supplied):
                raise ValueError("signature")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            if int(payload["expires_at"]) < int(datetime.now(UTC).timestamp()):
                raise ValueError("expired")
            return SlotClaim(
                UUID(payload["tenant_id"]),
                UUID(payload["appointment_type_id"]),
                aware_utc(datetime.fromisoformat(payload["start"])),
                aware_utc(datetime.fromisoformat(payload["end"])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CalendarError("slot_no_longer_available", "Der ausgewählte Terminvorschlag ist ungültig oder abgelaufen.") from exc


def spoken_slot(start: datetime, zone: ZoneInfo) -> tuple[str, str]:
    local = start.astimezone(zone)
    spoken_date = f"{GERMAN_WEEKDAYS[local.weekday()]}, {local.day}. {GERMAN_MONTHS[local.month - 1]}"
    spoken_time = f"{local.hour} Uhr" if local.minute == 0 else f"{local.hour} Uhr {local.minute:02d}"
    return spoken_date, spoken_time


class AvailabilityService:
    def __init__(self, db: Session, settings: Settings, tenant_id: UUID, tenant_timezone: str):
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id
        self.tenant_timezone = tenant_timezone

    def load_rules(
        self, appointment_type_id: UUID
    ) -> tuple[BookingConfiguration, list[CalendarBusinessHour], CalendarAppointmentType]:
        configuration, hours = get_or_create_booking_configuration(
            self.db, self.tenant_id, self.tenant_timezone
        )
        appointment_type = self.db.scalar(
            select(CalendarAppointmentType).where(
                CalendarAppointmentType.id == appointment_type_id,
                CalendarAppointmentType.tenant_id == self.tenant_id,
                CalendarAppointmentType.is_active.is_(True),
            ).options(selectinload(CalendarAppointmentType.service))
        )
        if appointment_type is None:
            raise CalendarError("invalid_appointment_type", "Die Terminart ist ungültig oder nicht aktiv.")
        if not appointment_type.service or not appointment_type.service.is_active:
            raise CalendarError("invalid_service", "Die zugehörige Leistung ist ungültig oder nicht aktiv.")
        return configuration, hours, appointment_type

    def load_rules_for_snapshot(self) -> tuple[BookingConfiguration, list[CalendarBusinessHour]]:
        return get_or_create_booking_configuration(self.db, self.tenant_id, self.tenant_timezone)

    async def external_busy_intervals(self, start: datetime, end: datetime) -> list[BusyInterval]:
        calendars = list(
            self.db.scalars(
                select(ExternalCalendar).where(
                    ExternalCalendar.tenant_id == self.tenant_id,
                    ExternalCalendar.is_selected_for_availability.is_(True),
                )
            )
        )
        if not calendars:
            raise CalendarError("calendar_not_selected", "Es ist kein Kalender für die Verfügbarkeitsprüfung ausgewählt.")
        by_connection: dict[UUID, list[str]] = defaultdict(list)
        for calendar in calendars:
            by_connection[calendar.calendar_connection_id].append(calendar.external_calendar_id)
        intervals: list[BusyInterval] = []
        for connection_id, calendar_ids in by_connection.items():
            connection = self.db.scalar(
                select(CalendarConnection).where(
                    CalendarConnection.id == connection_id,
                    CalendarConnection.tenant_id == self.tenant_id,
                    CalendarConnection.connection_status == CalendarConnectionStatus.connected,
                )
            )
            if connection is None:
                raise CalendarError("calendar_not_connected", "Für diesen Account ist kein funktionsfähiger Kalender verbunden.")
            provider = create_calendar_provider(connection.provider, self.settings)
            access_token = await valid_access_token(self.db, connection, self.settings)
            try:
                async with asyncio.timeout(self.settings.calendar_provider_timeout_seconds):
                    intervals.extend(await provider.get_busy_intervals(access_token, calendar_ids, start, end))
            except TimeoutError as exc:
                raise CalendarError(
                    "provider_timeout", "Die Kalenderprüfung dauert gerade zu lange.", transient=True
                ) from exc
        return merge_busy_intervals(intervals)

    def local_busy_intervals(self, start: datetime, end: datetime) -> list[BusyInterval]:
        local_bookings = list(
            self.db.scalars(
                select(CalendarBooking).where(
                    CalendarBooking.tenant_id == self.tenant_id,
                    CalendarBooking.status.in_([CalendarBookingStatus.pending, CalendarBookingStatus.confirmed]),
                    CalendarBooking.start_at < end,
                    CalendarBooking.end_at > start,
                )
            )
        )
        return merge_busy_intervals([
            BusyInterval(aware_utc(item.blocked_start_at), aware_utc(item.blocked_end_at)) for item in local_bookings
        ])

    async def current_busy_intervals(self, start: datetime, end: datetime) -> list[BusyInterval]:
        external = await self.external_busy_intervals(start, end)
        return merge_busy_intervals([*external, *self.local_busy_intervals(start, end)])

    async def search(
        self,
        appointment_type_id: UUID,
        search_start: datetime,
        search_end: datetime,
        *,
        preferred_day: date | None = None,
        preferred_time_range: str | None = None,
        maximum_results: int | None = None,
        now: datetime | None = None,
    ) -> tuple[str, list[AvailableSlotResponse]]:
        configuration, hours, appointment_type = self.load_rules(appointment_type_id)
        busy = await self.current_busy_intervals(aware_utc(search_start), aware_utc(search_end))
        slots = calculate_available_slots(
            configuration,
            hours,
            appointment_type,
            busy,
            search_start,
            search_end,
            now=now or datetime.now(UTC),
            preferred_day=preferred_day,
            preferred_time_range=preferred_time_range,
            maximum_results=maximum_results,
        )
        zone = ZoneInfo(configuration.timezone)
        signer = SlotSigner(self.settings)
        responses = []
        for start, end in slots:
            spoken_date, spoken_time = spoken_slot(start, zone)
            responses.append(
                AvailableSlotResponse(
                    slot_id=signer.sign(self.tenant_id, appointment_type.id, start, end),
                    start=start.astimezone(zone),
                    end=end.astimezone(zone),
                    spoken_date=spoken_date,
                    spoken_time=spoken_time,
                )
            )
        return configuration.timezone, responses

    async def exact_slot_available(
        self, appointment_type_id: UUID, start: datetime, end: datetime, *, now: datetime | None = None
    ) -> bool:
        configuration, hours, appointment_type = self.load_rules(appointment_type_id)
        if aware_utc(end) - aware_utc(start) != timedelta(minutes=appointment_type.service.duration_minutes):
            return False
        busy = await self.current_busy_intervals(
            aware_utc(start) - timedelta(hours=6), aware_utc(end) + timedelta(hours=6)
        )
        candidates = calculate_available_slots(
            configuration,
            hours,
            appointment_type,
            busy,
            start - timedelta(minutes=configuration.slot_interval_minutes),
            end + timedelta(minutes=configuration.slot_interval_minutes),
            now=now or datetime.now(UTC),
            maximum_results=10,
        )
        return any(candidate_start == aware_utc(start) and candidate_end == aware_utc(end) for candidate_start, candidate_end in candidates)
