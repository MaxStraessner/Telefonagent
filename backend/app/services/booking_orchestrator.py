import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calendar.errors import CalendarError
from app.core.config import Settings
from app.models import (
    BookingConversation,
    BookingState,
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBookingStatus,
)
from app.schemas.calendar import (
    CalendarBookingCreate,
    FinalizeStoredAppointmentRequest,
    PrepareAppointmentConfirmationRequest,
    PrepareAppointmentConfirmationResponse,
)
from app.services.availability import aware_utc
from app.services.availability_snapshot import AvailabilitySnapshotService
from app.services.booking_confirmation import (
    BookingConfirmationDecision,
    classify_booking_confirmation,
)
from app.services.calendar_booking import BookingServiceResult, CalendarBookingService
from app.services.conversation_orchestrator import ConversationOrchestrator


def _summary_material(
    context: BookingConversation,
    *,
    customer_name: str,
    customer_phone: str,
    customer_email: str,
) -> dict[str, str]:
    if (
        context.service_id is None
        or context.appointment_type_id is None
        or context.selected_slot_start is None
        or context.selected_slot_end is None
    ):
        raise CalendarError(
            "booking_context_incomplete",
            "Leistung und konkreter Termin müssen vor der Bestätigung feststehen.",
        )
    return {
        "service_id": str(context.service_id),
        "appointment_type_id": str(context.appointment_type_id),
        "start": aware_utc(context.selected_slot_start).isoformat(),
        "end": aware_utc(context.selected_slot_end).isoformat(),
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
    }


def _digest(material: dict[str, str]) -> str:
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_key(tenant_id: UUID, context: BookingConversation) -> str:
    if not context.confirmation_digest:
        raise CalendarError("confirmation_required", "Die Buchungszusammenfassung fehlt.")
    material = (
        f"{tenant_id}|{context.call_session_id}|{context.confirmation_version}|"
        f"{context.confirmation_digest}"
    )
    return f"conversation-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


class AppointmentBookingOrchestrator:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        tenant_id: UUID,
        tenant_timezone: str,
    ):
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id
        self.tenant_timezone = tenant_timezone

    def _conversation(self, session_id: UUID) -> ConversationOrchestrator:
        return ConversationOrchestrator(
            self.db,
            self.tenant_id,
            session_id,
            self.tenant_timezone,
        )

    def _appointment_type(
        self,
        context: BookingConversation,
    ) -> CalendarAppointmentType:
        if context.appointment_type_id is None or context.service_id is None:
            raise CalendarError(
                "booking_context_incomplete",
                "Leistung und Terminart sind noch nicht vollständig ausgewählt.",
            )
        appointment_type = self.db.scalar(
            select(CalendarAppointmentType)
            .where(
                CalendarAppointmentType.id == context.appointment_type_id,
                CalendarAppointmentType.tenant_id == self.tenant_id,
                CalendarAppointmentType.service_id == context.service_id,
                CalendarAppointmentType.is_active.is_(True),
            )
            .options(selectinload(CalendarAppointmentType.service))
        )
        if (
            appointment_type is None
            or appointment_type.service is None
            or not appointment_type.service.is_active
        ):
            raise CalendarError(
                "invalid_service",
                "Leistung und Terminart sind ungültig oder nicht aktiv.",
            )
        return appointment_type

    async def _recheck_selected_slot(
        self,
        context: BookingConversation,
    ):
        if (
            context.selected_slot_start is None
            or context.selected_slot_end is None
            or context.appointment_type_id is None
        ):
            raise CalendarError(
                "slot_selection_required",
                "Vor der Bestätigung muss ein konkreter Terminvorschlag ausgewählt werden.",
            )
        snapshot = AvailabilitySnapshotService(
            self.db,
            self.settings,
            self.tenant_id,
            context.call_session_id,
            self.tenant_timezone,
        )
        configuration, _hours, _appointment = snapshot.availability.load_rules(
            context.appointment_type_id
        )
        start = aware_utc(context.selected_slot_start)
        end = aware_utc(context.selected_slot_end)
        refresh_start = start - timedelta(minutes=configuration.slot_interval_minutes)
        refresh_end = end + timedelta(minutes=configuration.slot_interval_minutes)
        await snapshot.refresh(refresh_start, refresh_end)
        _timezone_name, slots, _refreshed = await snapshot.search(
            context.appointment_type_id,
            refresh_start,
            refresh_end,
            maximum_results=10,
        )
        return next((slot for slot in slots if aware_utc(slot.start) == start), None)

    async def prepare(
        self,
        payload: PrepareAppointmentConfirmationRequest,
    ) -> PrepareAppointmentConfirmationResponse:
        conversation = self._conversation(payload.session_id)
        context = conversation.context
        self._appointment_type(context)
        customer_name = payload.customer_name.strip()
        customer_phone = (payload.customer_phone or "").strip()
        customer_email = (payload.customer_email or "").strip().casefold()
        candidate_material = _summary_material(
            context,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
        )
        candidate_digest = _digest(candidate_material)
        if (
            context.state == BookingState.awaiting_confirmation
            and context.confirmation_digest == candidate_digest
        ):
            return self._prepared_response(context)
        if context.state == BookingState.awaiting_confirmation:
            conversation.invalidate_confirmation("customer_data_changed")
            conversation.transition(BookingState.slot_rechecking)
        elif context.state == BookingState.slot_available:
            conversation.transition(BookingState.slot_rechecking)
        elif context.state != BookingState.slot_rechecking:
            raise CalendarError(
                "slot_selection_required",
                "Ein ausgewählter Termin muss vor der Zusammenfassung erneut geprüft werden.",
            )

        exact = await self._recheck_selected_slot(context)
        if exact is None:
            context.selected_slot_id = None
            context.selected_slot_start = None
            context.selected_slot_end = None
            conversation.invalidate_confirmation("slot_no_longer_available")
            conversation.transition(
                BookingState.alternatives_available,
                error_code="slot_no_longer_available",
            )
            self.db.commit()
            raise CalendarError(
                "slot_no_longer_available",
                "Der ausgewählte Termin ist nicht mehr verfügbar.",
            )
        context.selected_slot_id = exact.slot_id
        context.selected_slot_start = aware_utc(exact.start)
        context.selected_slot_end = aware_utc(exact.end)
        context.customer_name = customer_name
        context.customer_phone = customer_phone
        context.customer_email = customer_email
        conversation.transition(BookingState.customer_data_required)
        context.confirmation_version += 1
        context.confirmation_digest = candidate_digest
        context.confirmation_classification = None
        context.confirmation_decided_at = None
        context.confirmation_transition_reason = "confirmation_prepared"
        context.booking_confirmed_by_customer = False
        conversation.transition(BookingState.awaiting_confirmation)
        self.db.commit()
        return self._prepared_response(context)

    def _prepared_response(
        self,
        context: BookingConversation,
    ) -> PrepareAppointmentConfirmationResponse:
        if (
            not context.confirmation_digest
            or context.selected_slot_start is None
            or context.service_name is None
        ):
            raise CalendarError(
                "confirmation_required",
                "Die Buchungszusammenfassung ist unvollständig.",
            )
        local_start = aware_utc(context.selected_slot_start).astimezone(
            ZoneInfo(self.tenant_timezone)
        )
        return PrepareAppointmentConfirmationResponse(
            success=True,
            confirmation_version=context.confirmation_version,
            confirmation_digest=context.confirmation_digest,
            state="awaiting_confirmation",
            summary={
                "service": context.service_name,
                "start": local_start.isoformat(),
                "customer_name": context.customer_name,
                "customer_phone": context.customer_phone,
                "customer_email": context.customer_email,
            },
        )

    async def finalize(
        self,
        payload: FinalizeStoredAppointmentRequest,
    ) -> BookingServiceResult:
        conversation = self._conversation(payload.session_id)
        context = conversation.context
        if context.appointment_id is not None:
            existing_booking = self.db.scalar(
                select(CalendarBooking).where(
                    CalendarBooking.id == context.appointment_id,
                    CalendarBooking.tenant_id == self.tenant_id,
                )
            )
            if (
                existing_booking is not None
                and existing_booking.status == CalendarBookingStatus.confirmed
                and payload.confirmation_version == context.confirmation_version
            ):
                return BookingServiceResult(existing_booking)
        if context.state != BookingState.awaiting_confirmation:
            raise CalendarError(
                "confirmation_required",
                "Die aktuelle Buchungszusammenfassung muss zuerst bestätigt werden.",
            )
        if payload.confirmation_version != context.confirmation_version:
            raise CalendarError(
                "stale_confirmation",
                "Die Terminbestätigung ist nicht mehr aktuell.",
            )
        if not context.confirmation_digest:
            raise CalendarError(
                "confirmation_required",
                "Die aktuelle Buchungszusammenfassung fehlt.",
            )

        previous_classification = context.confirmation_classification
        decision = classify_booking_confirmation(payload.confirmation_utterance)
        context.confirmation_classification = decision.value
        context.confirmation_decided_at = datetime.now(timezone.utc)
        context.confirmation_transition_reason = f"confirmation_{decision.value}"
        if decision == BookingConfirmationDecision.unclear:
            self.db.commit()
            code = (
                "confirmation_still_unclear"
                if previous_classification == BookingConfirmationDecision.unclear.value
                else "confirmation_unclear"
            )
            raise CalendarError(
                code,
                "Die Zustimmung zur Buchung war nicht eindeutig.",
            )
        if decision != BookingConfirmationDecision.confirmed:
            context.booking_confirmed_by_customer = False
            conversation.transition(
                BookingState.date_time_resolving,
                error_code=(
                    "booking_change_requested"
                    if decision == BookingConfirmationDecision.change_requested
                    else "booking_declined"
                ),
            )
            self.db.commit()
            if decision == BookingConfirmationDecision.change_requested:
                raise CalendarError(
                    "booking_change_requested",
                    "Vor der Buchung wurde eine Änderung gewünscht.",
                )
            raise CalendarError(
                "booking_declined",
                "Die Kundin oder der Kunde hat die Buchung abgelehnt.",
            )

        appointment_type = self._appointment_type(context)
        if (
            context.selected_slot_id is None
            or context.selected_slot_start is None
            or context.customer_name is None
        ):
            raise CalendarError(
                "booking_context_incomplete",
                "Die bestätigten Buchungsdaten sind unvollständig.",
            )
        context.booking_confirmed_by_customer = True
        conversation.transition(BookingState.final_check_running)
        self.db.commit()

        exact = await self._recheck_selected_slot(context)
        if exact is None:
            context.booking_confirmed_by_customer = False
            conversation.transition(
                BookingState.alternatives_available,
                error_code="slot_no_longer_available",
            )
            self.db.commit()
            raise CalendarError(
                "slot_no_longer_available",
                "Der bestätigte Termin ist inzwischen nicht mehr verfügbar.",
            )
        context.selected_slot_id = exact.slot_id
        key = _idempotency_key(self.tenant_id, context)
        service = CalendarBookingService(
            self.db,
            self.settings,
            self.tenant_id,
            self.tenant_timezone,
        )
        existing = service.existing_for_key(key)
        if existing is not None and existing.status == CalendarBookingStatus.confirmed:
            context.appointment_id = existing.id
            context.external_event_id = existing.external_event_id
            if context.state == BookingState.final_check_running:
                conversation.transition_through(
                    BookingState.booking_running,
                    BookingState.booking_confirmed,
                )
            self.db.commit()
            return BookingServiceResult(existing)

        conversation.transition(BookingState.booking_running)
        self.db.commit()
        try:
            result = await service.create(
                CalendarBookingCreate(
                    slot_id=context.selected_slot_id,
                    appointment_type_id=appointment_type.id,
                    service_id=context.service_id,
                    customer_name=context.customer_name,
                    customer_phone=context.customer_phone or "",
                    customer_email=context.customer_email or "",
                    customer_notes="",
                    idempotency_key=key,
                ),
                tool_call_id=payload.tool_call_id,
                conversation_session_id=payload.session_id,
            )
        except CalendarError as exc:
            target = (
                BookingState.alternatives_available
                if exc.code == "slot_no_longer_available"
                else BookingState.booking_failed
            )
            conversation.transition(target, error_code=exc.code)
            self.db.commit()
            raise
        if result.booking is None:
            target = (
                BookingState.alternatives_available
                if result.error_code == "slot_no_longer_available"
                else BookingState.booking_failed
            )
            conversation.transition(target, error_code=result.error_code)
            self.db.commit()
            return result
        if (
            result.booking.status != CalendarBookingStatus.confirmed
            or not result.booking.external_event_id
        ):
            conversation.transition(
                BookingState.booking_failed,
                error_code="external_confirmation_missing",
            )
            self.db.commit()
            return BookingServiceResult(
                None,
                "external_confirmation_missing",
                "Der Termin wurde extern nicht bestätigt.",
                [],
            )
        context.appointment_id = result.booking.id
        context.external_event_id = result.booking.external_event_id
        conversation.transition(BookingState.booking_confirmed)
        self.db.commit()
        AvailabilitySnapshotService(
            self.db,
            self.settings,
            self.tenant_id,
            payload.session_id,
            self.tenant_timezone,
        ).add_confirmed_booking(
            result.booking.blocked_start_at,
            result.booking.blocked_end_at,
        )
        return result
