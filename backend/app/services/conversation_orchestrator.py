from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.errors import CalendarError
from app.models import BookingConversation, BookingState, CallSession
from app.services.conversation_events import log_conversation_event

ALLOWED_TRANSITIONS: dict[BookingState, set[BookingState]] = {
    BookingState.idle: {BookingState.catalog_loading},
    BookingState.catalog_loading: {BookingState.ready, BookingState.service_required},
    BookingState.ready: {BookingState.service_required, BookingState.service_selected},
    BookingState.service_required: {BookingState.service_selected},
    BookingState.service_selected: {BookingState.date_time_required, BookingState.date_time_resolving},
    BookingState.date_time_required: {BookingState.date_time_resolving},
    BookingState.date_time_resolving: {BookingState.availability_checking},
    BookingState.availability_checking: {BookingState.slot_available, BookingState.slot_unavailable},
    BookingState.slot_available: {BookingState.customer_data_required, BookingState.date_time_required},
    BookingState.slot_unavailable: {BookingState.date_time_required, BookingState.availability_checking},
    BookingState.customer_data_required: {BookingState.confirmation_required},
    BookingState.confirmation_required: {BookingState.final_check_running, BookingState.date_time_required},
    BookingState.final_check_running: {BookingState.booking_running, BookingState.slot_unavailable, BookingState.booking_failed},
    BookingState.booking_running: {BookingState.booking_confirmed, BookingState.booking_failed, BookingState.slot_unavailable},
    BookingState.booking_confirmed: {BookingState.completed},
    BookingState.booking_failed: {BookingState.confirmation_required, BookingState.date_time_required},
    BookingState.completed: {BookingState.service_required, BookingState.service_selected},
}


class ConversationOrchestrator:
    def __init__(self, db: Session, tenant_id: UUID, session_id: UUID, timezone_name: str):
        self.db = db
        self.tenant_id = tenant_id
        self.session = db.scalar(
            select(CallSession).where(CallSession.id == session_id, CallSession.tenant_id == tenant_id)
        )
        if self.session is None:
            raise CalendarError("invalid_conversation_session", "Die Gesprächssitzung ist ungültig.")
        self.context = db.scalar(
            select(BookingConversation).where(
                BookingConversation.call_session_id == session_id,
                BookingConversation.tenant_id == tenant_id,
            )
        )
        if self.context is None:
            self.context = BookingConversation(
                tenant_id=tenant_id,
                call_session_id=session_id,
                state=BookingState.idle,
                timezone=timezone_name,
            )
            db.add(self.context)
            db.flush()

    def transition(self, target: BookingState, *, error_code: str | None = None) -> None:
        before = self.context.state
        if target == before:
            return
        if target not in ALLOWED_TRANSITIONS.get(before, set()):
            log_conversation_event(
                "booking_state_changed",
                session_id=self.context.call_session_id,
                booking_state_before=before.value,
                booking_state_after=target.value,
                success=False,
                error_code="invalid_booking_state_transition",
            )
            raise CalendarError(
                "invalid_booking_state_transition",
                f"Der Buchungsschritt {before.value} darf nicht direkt zu {target.value} wechseln.",
            )
        self.context.state = target
        self.context.last_error_code = error_code
        log_conversation_event(
            "booking_state_changed",
            session_id=self.context.call_session_id,
            booking_state_before=before.value,
            booking_state_after=target.value,
            success=error_code is None,
            error_code=error_code,
        )

    def transition_through(self, *targets: BookingState) -> None:
        for target in targets:
            self.transition(target)

    def select_service(self, service_id: UUID, service_name: str, appointment_type_id: UUID | None = None) -> None:
        if self.context.state == BookingState.idle:
            self.transition_through(BookingState.catalog_loading, BookingState.service_required, BookingState.service_selected)
        elif self.context.state == BookingState.catalog_loading:
            self.transition_through(BookingState.service_required, BookingState.service_selected)
        elif self.context.state in {BookingState.ready, BookingState.service_required, BookingState.completed}:
            self.transition(BookingState.service_selected)
        elif self.context.state != BookingState.service_selected:
            raise CalendarError("invalid_booking_state_transition", "Die Leistung kann in diesem Buchungsschritt nicht geändert werden.")
        self.context.service_id = service_id
        self.context.service_name = service_name
        self.context.appointment_type_id = appointment_type_id
        self.transition(BookingState.date_time_resolving)

    def bootstrap_started(self) -> None:
        self.session.bootstrap_status = "running"
        if self.context.state == BookingState.idle:
            self.transition(BookingState.catalog_loading)

    def bootstrap_completed(self, *, catalog_available: bool) -> None:
        self.session.bootstrap_status = "completed"
        self.transition(BookingState.ready if catalog_available else BookingState.service_required)

    def commit(self) -> BookingConversation:
        self.db.commit()
        self.db.refresh(self.context)
        return self.context
