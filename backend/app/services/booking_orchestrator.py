import hashlib
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calendar.errors import CalendarError
from app.core.config import Settings
from app.models import BookingState, CalendarAppointmentType, CalendarBookingStatus
from app.schemas.calendar import CalendarBookingCreate, FinalizeAppointmentRequest
from app.services.availability import SlotSigner, aware_utc
from app.services.availability_snapshot import AvailabilitySnapshotService
from app.services.booking_confirmation import BookingConfirmationDecision, classify_booking_confirmation
from app.services.calendar_booking import BookingServiceResult, CalendarBookingService
from app.services.conversation_orchestrator import ConversationOrchestrator


def server_idempotency_key(tenant_id: UUID, payload: FinalizeAppointmentRequest) -> str:
    material = "|".join([
        str(tenant_id), str(payload.session_id), str(payload.service_id), str(payload.appointment_type_id),
        aware_utc(payload.start_at).isoformat(), payload.customer_name.strip().casefold(),
        (payload.customer_phone or "").strip(), (payload.customer_email or "").strip().casefold(),
        str(payload.confirmation_version),
    ])
    return f"conversation-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


class AppointmentBookingOrchestrator:
    def __init__(self, db: Session, settings: Settings, tenant_id: UUID, tenant_timezone: str):
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id
        self.tenant_timezone = tenant_timezone

    async def finalize(self, payload: FinalizeAppointmentRequest) -> BookingServiceResult:
        conversation = ConversationOrchestrator(
            self.db, self.tenant_id, payload.session_id, self.tenant_timezone
        )
        appointment_type = self.db.scalar(select(CalendarAppointmentType).where(
            CalendarAppointmentType.id == payload.appointment_type_id,
            CalendarAppointmentType.tenant_id == self.tenant_id,
            CalendarAppointmentType.service_id == payload.service_id,
            CalendarAppointmentType.is_active.is_(True),
        ).options(selectinload(CalendarAppointmentType.service)))
        if appointment_type is None or not appointment_type.service or not appointment_type.service.is_active:
            raise CalendarError("invalid_service", "Leistung und Terminart sind ungültig oder nicht aktiv.")

        key = server_idempotency_key(self.tenant_id, payload)
        existing = CalendarBookingService(
            self.db, self.settings, self.tenant_id, self.tenant_timezone
        ).existing_for_key(key)
        if existing is not None and existing.status == CalendarBookingStatus.confirmed:
            return BookingServiceResult(existing)

        context = conversation.context
        confirmation = classify_booking_confirmation(payload.confirmation_utterance)
        if confirmation != BookingConfirmationDecision.confirmed:
            context.booking_confirmed_by_customer = False
            error_by_decision = {
                BookingConfirmationDecision.rejected: (
                    "booking_declined", "Die Kundin oder der Kunde hat die Buchung abgelehnt."
                ),
                BookingConfirmationDecision.change_requested: (
                    "booking_change_requested", "Vor der Buchung wurde eine Änderung gewünscht."
                ),
                BookingConfirmationDecision.unclear: (
                    "confirmation_unclear", "Die Zustimmung zur Buchung war nicht eindeutig."
                ),
            }
            if confirmation in {BookingConfirmationDecision.rejected, BookingConfirmationDecision.change_requested} and context.state in {
                BookingState.slot_available, BookingState.confirmation_required
            }:
                conversation.transition(BookingState.date_time_required, error_code=error_by_decision[confirmation][0])
            self.db.commit()
            code, message = error_by_decision[confirmation]
            raise CalendarError(code, message)
        if context.service_id not in {None, payload.service_id} or context.appointment_type_id not in {None, payload.appointment_type_id}:
            raise CalendarError("conversation_context_mismatch", "Die bestätigten Buchungsdaten passen nicht zum Gesprächskontext.")
        if payload.confirmation_version < context.confirmation_version:
            raise CalendarError("stale_confirmation", "Die Terminbestätigung ist nicht mehr aktuell.")

        context.service_id = payload.service_id
        context.service_name = appointment_type.service.name
        context.appointment_type_id = payload.appointment_type_id
        context.selected_slot_start = aware_utc(payload.start_at)
        context.selected_slot_end = aware_utc(payload.start_at) + timedelta(minutes=appointment_type.service.duration_minutes)
        context.customer_name = payload.customer_name.strip()
        context.customer_phone = (payload.customer_phone or "").strip()
        context.customer_email = (payload.customer_email or "").strip()
        context.booking_confirmed_by_customer = True
        context.confirmation_version = payload.confirmation_version
        if context.state == BookingState.date_time_required:
            conversation.transition_through(
                BookingState.availability_checking, BookingState.slot_available,
                BookingState.customer_data_required, BookingState.confirmation_required,
            )
        elif context.state == BookingState.slot_available:
            conversation.transition_through(
                BookingState.customer_data_required, BookingState.confirmation_required
            )
        if context.state != BookingState.confirmation_required:
            raise CalendarError("confirmation_required", "Der Termin muss mit vollständigen Daten ausdrücklich bestätigt werden.")
        conversation.transition(BookingState.final_check_running)
        self.db.commit()

        end = context.selected_slot_end
        slot_id = SlotSigner(self.settings).sign(self.tenant_id, payload.appointment_type_id, payload.start_at, end)
        service = CalendarBookingService(self.db, self.settings, self.tenant_id, self.tenant_timezone)
        conversation.transition(BookingState.booking_running)
        self.db.commit()
        try:
            result = await service.create(
                CalendarBookingCreate(
                    slot_id=slot_id, appointment_type_id=payload.appointment_type_id,
                    service_id=payload.service_id, customer_name=payload.customer_name,
                    customer_phone=payload.customer_phone or "", customer_email=payload.customer_email or "",
                    customer_notes="", idempotency_key=key,
                ),
                tool_call_id=payload.tool_call_id,
                conversation_session_id=payload.session_id,
            )
        except CalendarError as exc:
            if exc.code == "slot_no_longer_available":
                conversation.transition(BookingState.slot_unavailable, error_code=exc.code)
            else:
                conversation.transition(BookingState.booking_failed, error_code=exc.code)
            self.db.commit()
            raise
        if result.booking is None:
            target = BookingState.slot_unavailable if result.error_code == "slot_no_longer_available" else BookingState.booking_failed
            conversation.transition(target, error_code=result.error_code)
            self.db.commit()
            return result
        if result.booking.status != CalendarBookingStatus.confirmed or not result.booking.external_event_id:
            conversation.transition(BookingState.booking_failed, error_code="external_confirmation_missing")
            self.db.commit()
            return BookingServiceResult(None, "external_confirmation_missing", "Der Termin wurde extern nicht bestätigt.", [])
        context.appointment_id = result.booking.id
        context.external_event_id = result.booking.external_event_id
        conversation.transition_through(BookingState.booking_confirmed, BookingState.completed)
        self.db.commit()
        AvailabilitySnapshotService(
            self.db, self.settings, self.tenant_id, payload.session_id, self.tenant_timezone
        ).add_confirmed_booking(result.booking.blocked_start_at, result.booking.blocked_end_at)
        return result
