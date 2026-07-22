import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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

logger = logging.getLogger(__name__)


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
        f"Leistung: {booking.service_name_snapshot}",
        f"Kunde: {booking.customer_name}",
        f"Telefon: {booking.customer_phone}",
    ]
    if booking.customer_email:
        lines.append(f"E-Mail: {booking.customer_email}")
    lines.extend([
        f"Terminformat: {booking.appointment_format_snapshot}",
        f"Ort: {booking.location_snapshot or 'Nicht angegeben'}",
        "Quelle: KI Telefonagent",
        f"Interne Termin ID: {booking.id}",
    ])
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
        configuration, _hours, configured_type = self.availability.load_rules(payload.appointment_type_id)
        if payload.service_id is not None and payload.service_id != configured_type.service_id:
            raise CalendarError("invalid_service", "Leistung und Terminart gehören nicht zusammen.")
        before = configured_type.buffer_before_minutes
        if before is None:
            before = configuration.buffer_before_minutes
        after = configured_type.buffer_after_minutes
        if after is None:
            after = configuration.buffer_after_minutes
        blocked_start = claim.start - timedelta(minutes=before)
        blocked_end = claim.end + timedelta(minutes=after)
        local_conflict = self.db.scalar(
            select(CalendarBooking.id).where(
                CalendarBooking.tenant_id == self.tenant_id,
                CalendarBooking.status.in_([CalendarBookingStatus.pending, CalendarBookingStatus.confirmed]),
                CalendarBooking.blocked_start_at < blocked_end,
                CalendarBooking.blocked_end_at > blocked_start,
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
            service_id=configured_type.service_id,
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
            timezone=configuration.timezone,
            status=CalendarBookingStatus.pending,
            source=source,
            idempotency_key=payload.idempotency_key,
            sync_status="pending",
            service_name_snapshot=configured_type.service.name,
            duration_minutes_snapshot=configured_type.service.duration_minutes,
            buffer_before_minutes_snapshot=before,
            buffer_after_minutes_snapshot=after,
            blocked_start_at=blocked_start,
            blocked_end_at=blocked_end,
            appointment_format_snapshot=configured_type.location_type.value,
            location_snapshot=configured_type.location_text,
            calendar_name_snapshot=target.calendar_name,
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
            title=f"{booking.service_name_snapshot} · {booking.customer_name}",
            description=booking_description(booking, appointment_type),
            start=aware_utc(booking.start_at).astimezone(zone),
            end=aware_utc(booking.end_at).astimezone(zone),
            timezone=booking.timezone,
            booking_id=str(booking.id),
            idempotency_key=booking.idempotency_key,
            location=booking.location_snapshot,
        )
        try:
            created = await provider.create_event(token, target.external_calendar_id, event)
        except CalendarProviderError as exc:
            booking.status = CalendarBookingStatus.failed
            booking.sync_status = "failed"
            booking.failure_code = "calendar_event_creation_failed"
            self.db.commit()
            logger.warning(
                "calendar_booking_failed appointmentId=%s tenantId=%s serviceId=%s appointmentTypeId=%s "
                "calendarProvider=%s calendarId=%s syncStatus=failed externalEventCreated=false errorCode=%s",
                booking.id, self.tenant_id, booking.service_id, booking.appointment_type_id,
                booking.provider.value, booking.external_calendar_id, booking.failure_code,
            )
            raise CalendarError(
                "calendar_event_creation_failed",
                "Der Termin konnte beim Kalenderanbieter nicht verbindlich erstellt werden.",
                transient=exc.transient,
            ) from exc
        booking.external_event_id = created.event_id
        booking.provider_response_reference = created.reference
        booking.status = CalendarBookingStatus.confirmed
        booking.sync_status = "synced"
        booking.failure_code = None
        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            divergent = self.db.get(CalendarBooking, booking.id)
            if divergent is not None:
                divergent.external_event_id = created.event_id
                divergent.provider_response_reference = created.reference
                divergent.status = CalendarBookingStatus.pending
                divergent.sync_status = "needs_reconciliation"
                divergent.failure_code = "local_confirmation_failed"
                self.db.commit()
            logger.exception(
                "calendar_booking_diverged appointmentId=%s tenantId=%s serviceId=%s appointmentTypeId=%s "
                "calendarProvider=%s calendarId=%s syncStatus=needs_reconciliation externalEventCreated=true "
                "errorCode=local_confirmation_failed",
                booking.id, self.tenant_id, booking.service_id, booking.appointment_type_id,
                booking.provider.value, booking.external_calendar_id,
            )
            raise CalendarError(
                "local_confirmation_failed",
                "Der Kalendereintrag wurde erstellt, die lokale Bestätigung muss jedoch geprüft werden.",
            ) from exc
        self.db.refresh(booking)
        logger.info(
            "calendar_booking_confirmed appointmentId=%s tenantId=%s serviceId=%s appointmentTypeId=%s "
            "calendarProvider=%s calendarId=%s syncStatus=synced externalEventCreated=true errorCode=none",
            booking.id, self.tenant_id, booking.service_id, booking.appointment_type_id,
            booking.provider.value, booking.external_calendar_id,
        )
        return BookingServiceResult(booking)
