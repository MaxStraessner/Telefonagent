import hashlib
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.calendar.errors import CalendarError, CalendarProviderError
from app.calendar.providers import EventData, create_calendar_provider
from app.core.config import Settings
from app.models import (
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBookingSource,
    CalendarBookingStatus,
    CalendarConnection,
    CalendarConnectionStatus,
    ExternalCalendar,
)
from app.schemas.calendar import AvailableSlotResponse, CalendarBookingCreate
from app.services.availability import AvailabilityService, SlotSigner, aware_utc
from app.services.calendar_connections import valid_access_token


@dataclass(frozen=True)
class BookingServiceResult:
    booking: CalendarBooking | None
    error_code: str | None = None
    message: str | None = None
    alternative_slots: list[AvailableSlotResponse] | None = None


def booking_description(
    booking: CalendarBooking, appointment_type: CalendarAppointmentType
) -> str:
    lines = [
        f"Terminart: {appointment_type.name}",
        f"Kunde: {booking.customer_name}",
        f"Telefon: {booking.customer_phone}",
    ]
    if booking.customer_email:
        lines.append(f"E-Mail: {booking.customer_email}")
    lines.extend(["Quelle: Telefonagent", f"Buchungsnummer: {booking.id}"])
    if booking.customer_notes:
        lines.append(f"Notizen: {booking.customer_notes}")
    return "\n".join(lines)


class CalendarBookingService:
    def __init__(self, db: Session, settings: Settings, tenant_id: UUID, tenant_timezone: str):
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id
        self.tenant_timezone = tenant_timezone
        self.availability = AvailabilityService(db, settings, tenant_id, tenant_timezone)

    def existing_for_key(self, idempotency_key: str) -> CalendarBooking | None:
        return self.db.scalar(
            select(CalendarBooking).where(
                CalendarBooking.tenant_id == self.tenant_id,
                CalendarBooking.idempotency_key == idempotency_key,
            )
        )

    def lock_slot(self, start, end) -> None:
        """Serialize competing bookings for the same tenant and slot on PostgreSQL."""
        if self.db.get_bind().dialect.name != "postgresql":
            return
        lock_material = f"{self.tenant_id}:{start.isoformat()}:{end.isoformat()}".encode("utf-8")
        lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    async def create(
        self, payload: CalendarBookingCreate, *, source: CalendarBookingSource = CalendarBookingSource.voice_agent
    ) -> BookingServiceResult:
        existing = self.existing_for_key(payload.idempotency_key)
        if existing is not None:
            if existing.status == CalendarBookingStatus.confirmed:
                return BookingServiceResult(existing)
            return BookingServiceResult(
                None,
                "duplicate_booking",
                "Für diese Idempotenzkennung existiert bereits ein Buchungsversuch.",
                [],
            )
        claim = SlotSigner(self.settings).verify(payload.slot_id)
        if claim.tenant_id != self.tenant_id or claim.appointment_type_id != payload.appointment_type_id:
            raise CalendarError("tenant_access_denied", "Der Terminvorschlag gehört nicht zu diesem Account.")
        still_available = await self.availability.exact_slot_available(
            payload.appointment_type_id, claim.start, claim.end
        )
        if not still_available:
            _timezone_name, alternatives = await self.availability.search(
                payload.appointment_type_id,
                claim.start,
                claim.start + timedelta(days=7),
                maximum_results=3,
            )
            return BookingServiceResult(
                None,
                "slot_no_longer_available",
                "Der ausgewählte Termin ist inzwischen belegt.",
                alternatives,
            )
        self.lock_slot(claim.start, claim.end)
        existing = self.existing_for_key(payload.idempotency_key)
        if existing is not None:
            if existing.status == CalendarBookingStatus.confirmed:
                return BookingServiceResult(existing)
            return BookingServiceResult(None, "duplicate_booking", "Diese Buchung wird bereits verarbeitet.", [])
        local_conflict = self.db.scalar(
            select(CalendarBooking.id).where(
                CalendarBooking.tenant_id == self.tenant_id,
                CalendarBooking.status.in_([CalendarBookingStatus.pending, CalendarBookingStatus.confirmed]),
                CalendarBooking.start_at < claim.end,
                CalendarBooking.end_at > claim.start,
            ).limit(1)
        )
        if local_conflict is not None:
            self.db.rollback()
            _timezone_name, alternatives = await self.availability.search(
                payload.appointment_type_id,
                claim.start,
                claim.start + timedelta(days=7),
                maximum_results=3,
            )
            return BookingServiceResult(
                None,
                "slot_no_longer_available",
                "Der ausgewählte Termin ist inzwischen belegt.",
                alternatives,
            )
        appointment_type = self.db.scalar(
            select(CalendarAppointmentType).where(
                CalendarAppointmentType.id == payload.appointment_type_id,
                CalendarAppointmentType.tenant_id == self.tenant_id,
                CalendarAppointmentType.is_active.is_(True),
            )
        )
        if appointment_type is None:
            raise CalendarError("invalid_appointment_type", "Die Terminart ist ungültig oder nicht aktiv.")
        target = self.db.scalar(
            select(ExternalCalendar).where(
                ExternalCalendar.tenant_id == self.tenant_id,
                ExternalCalendar.is_selected_for_booking.is_(True),
            )
        )
        if target is None:
            raise CalendarError("calendar_not_selected", "Es ist kein Zielkalender für Buchungen ausgewählt.")
        if not target.can_write:
            raise CalendarError("booking_calendar_not_writable", "Der Zielkalender besitzt keine Schreibberechtigung.")
        connection = self.db.scalar(
            select(CalendarConnection).where(
                CalendarConnection.id == target.calendar_connection_id,
                CalendarConnection.tenant_id == self.tenant_id,
                CalendarConnection.connection_status == CalendarConnectionStatus.connected,
            )
        )
        if connection is None:
            raise CalendarError("calendar_not_connected", "Für diesen Account ist kein funktionsfähiger Kalender verbunden.")
        booking = CalendarBooking(
            tenant_id=self.tenant_id,
            appointment_type_id=appointment_type.id,
            calendar_connection_id=connection.id,
            external_calendar_id=target.external_calendar_id,
            provider=connection.provider,
            customer_name=payload.customer_name,
            customer_phone=payload.customer_phone,
            customer_email=payload.customer_email.strip(),
            customer_notes=payload.customer_notes.strip(),
            start_at=claim.start,
            end_at=claim.end,
            timezone=self.availability.load_rules(appointment_type.id)[0].timezone,
            status=CalendarBookingStatus.pending,
            source=source,
            idempotency_key=payload.idempotency_key,
        )
        self.db.add(booking)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.existing_for_key(payload.idempotency_key)
            if existing and existing.status == CalendarBookingStatus.confirmed:
                return BookingServiceResult(existing)
            return BookingServiceResult(None, "duplicate_booking", "Diese Buchung wird bereits verarbeitet.", [])
        self.db.refresh(booking)
        provider = create_calendar_provider(connection.provider, self.settings)
        token = await valid_access_token(self.db, connection, self.settings)
        zone = ZoneInfo(booking.timezone)
        event = EventData(
            title=f"Kundentermin: {booking.customer_name}",
            description=booking_description(booking, appointment_type),
            start=aware_utc(booking.start_at).astimezone(zone),
            end=aware_utc(booking.end_at).astimezone(zone),
            timezone=booking.timezone,
            booking_id=str(booking.id),
            idempotency_key=booking.idempotency_key,
        )
        try:
            created = await provider.create_event(token, target.external_calendar_id, event)
        except CalendarProviderError as exc:
            booking.status = CalendarBookingStatus.failed
            booking.failure_code = "calendar_event_creation_failed"
            self.db.commit()
            raise CalendarError(
                "calendar_event_creation_failed",
                "Der Termin konnte beim Kalenderanbieter nicht verbindlich erstellt werden.",
                transient=exc.transient,
            ) from exc
        booking.external_event_id = created.event_id
        booking.provider_response_reference = created.reference
        booking.status = CalendarBookingStatus.confirmed
        booking.failure_code = None
        self.db.commit()
        self.db.refresh(booking)
        return BookingServiceResult(booking)
