from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BookingConversation, CallSession, ToolExecution
from app.services.conversation_events import log_conversation_event


class ToolAudit:
    def __init__(self, db: Session, tenant_id: UUID, session_id: UUID, call_id: str, tool_name: str):
        self.db = db
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.call_id = call_id
        self.tool_name = tool_name
        self.started = perf_counter()
        self.session = db.scalar(
            select(CallSession).where(CallSession.id == session_id, CallSession.tenant_id == tenant_id)
        )
        self.context = db.scalar(
            select(BookingConversation).where(
                BookingConversation.call_session_id == session_id,
                BookingConversation.tenant_id == tenant_id,
            )
        )
        before_runtime = self.session.runtime_state if self.session else None
        before_booking = self.context.state.value if self.context else None
        self.execution = db.scalar(
            select(ToolExecution).where(
                ToolExecution.tenant_id == tenant_id,
                ToolExecution.call_session_id == session_id,
                ToolExecution.call_id == call_id,
            )
        )
        if self.execution is None:
            self.execution = ToolExecution(
                tenant_id=tenant_id,
                call_session_id=session_id,
                call_id=call_id,
                tool_name=tool_name,
                status="running",
                continuation_mode="sdk_automatic",
                booking_state_before=before_booking,
                runtime_state_before=before_runtime,
                runtime_state_after="tool_running",
            )
            db.add(self.execution)
        if self.session:
            self.session.runtime_state = "tool_running"
        db.commit()
        log_conversation_event(
            "tool_started",
            session_id=session_id,
            tool_call_id=call_id,
            tool_name=tool_name,
            booking_state_before=before_booking,
            runtime_state_before=before_runtime,
            runtime_state_after="tool_running",
        )

    def complete(self, *, success: bool, error_code: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        duration_ms = round((perf_counter() - self.started) * 1000)
        if self.context is not None:
            self.db.refresh(self.context)
            self.execution.booking_state_after = self.context.state.value
        if self.session is not None:
            self.session.runtime_state = "continuation_starting"
        self.execution.status = "completed" if success else "failed"
        self.execution.success = success
        self.execution.error_code = error_code
        self.execution.duration_ms = duration_ms
        self.execution.completed_at = now
        self.execution.result_sent_at = now
        self.execution.continuation_triggered_at = now
        self.execution.runtime_state_after = "continuation_starting"
        self.db.commit()
        event_states = (
            ("tool_completed", "tool_running", "tool_result_ready"),
            ("tool_result_sent", "tool_result_ready", "continuation_starting"),
            ("tool_continuation_started", "continuation_starting", "continuation_starting"),
        )
        for event_name, runtime_before, runtime_after in event_states:
            log_conversation_event(
                event_name,
                session_id=self.session_id,
                tool_call_id=self.call_id,
                tool_name=self.tool_name,
                booking_state_before=self.execution.booking_state_before,
                booking_state_after=self.execution.booking_state_after,
                runtime_state_before=runtime_before,
                runtime_state_after=runtime_after,
                duration_ms=duration_ms,
                success=success,
                error_code=error_code,
            )
