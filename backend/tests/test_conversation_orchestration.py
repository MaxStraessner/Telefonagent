import pytest
from sqlalchemy import select

from app.calendar.errors import CalendarError
from app.models import BookingState, CallChannel, CallSession, Tenant
from app.services.conversation_orchestrator import ConversationOrchestrator

VALID_TRANSITIONS = [
    (BookingState.idle, BookingState.catalog_loading),
    (BookingState.catalog_loading, BookingState.ready),
    (BookingState.ready, BookingState.service_selected),
    (BookingState.service_selected, BookingState.date_time_resolving),
    (BookingState.date_time_required, BookingState.date_time_resolving),
    (BookingState.date_time_resolving, BookingState.availability_checking),
    (BookingState.availability_checking, BookingState.slot_available),
    (BookingState.availability_checking, BookingState.slot_unavailable),
    (BookingState.slot_available, BookingState.customer_data_required),
    (BookingState.customer_data_required, BookingState.confirmation_required),
    (BookingState.confirmation_required, BookingState.final_check_running),
    (BookingState.final_check_running, BookingState.booking_running),
    (BookingState.booking_running, BookingState.booking_confirmed),
    (BookingState.booking_confirmed, BookingState.completed),
]


@pytest.mark.parametrize(("before", "after"), VALID_TRANSITIONS)
def test_booking_state_machine_accepts_documented_transitions(db, before, after):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    orchestrator = ConversationOrchestrator(db, tenant.id, call.id, tenant.timezone)
    orchestrator.context.state = before
    orchestrator.transition(after)
    assert orchestrator.context.state == after


def test_booking_state_machine_rejects_skipped_finalization(db):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    orchestrator = ConversationOrchestrator(db, tenant.id, call.id, tenant.timezone)
    with pytest.raises(CalendarError, match="darf nicht direkt"):
        orchestrator.transition(BookingState.booking_confirmed)
