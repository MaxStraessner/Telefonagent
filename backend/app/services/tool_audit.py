from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
        if self.execution is not None:
            self._reject_duplicate()
        self.execution = ToolExecution(
            tenant_id=tenant_id,
            call_session_id=session_id,
            call_id=call_id,
            tool_name=tool_name,
            status="running",
            continuation_mode="agents_sdk",
            booking_state_before=before_booking,
            runtime_state_before=before_runtime,
            runtime_state_after="tool_running",
        )
        db.add(self.execution)
        if self.session:
            self.session.runtime_state = "tool_running"
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            self.execution = db.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == tenant_id,
                    ToolExecution.call_session_id == session_id,
                    ToolExecution.call_id == call_id,
                )
            )
            if self.execution is not None:
                self._reject_duplicate()
            raise exc
        for event_name in ("tool_received", "tool_started"):
            log_conversation_event(
                event_name,
                session_id=session_id,
                tool_call_id=call_id,
                tool_name=tool_name,
                booking_state_before=before_booking,
                runtime_state_before=before_runtime,
                runtime_state_after="tool_running",
            )

    def _reject_duplicate(self) -> None:
        log_conversation_event(
            "tool_duplicate_ignored",
            session_id=self.session_id,
            tool_call_id=self.call_id,
            tool_name=self.tool_name,
            booking_state_before=self.execution.booking_state_before,
            booking_state_after=self.execution.booking_state_after,
            runtime_state_before=self.execution.runtime_state_before,
            runtime_state_after=self.execution.runtime_state_after,
            success=self.execution.success,
            error_code="duplicate_tool_call",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_tool_call",
                "message": "Dieser Werkzeugaufruf wurde bereits verarbeitet.",
            },
        )

    def complete(self, *, success: bool, error_code: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        duration_ms = round((perf_counter() - self.started) * 1000)
        if self.context is not None:
            self.db.refresh(self.context)
            self.execution.booking_state_after = self.context.state.value
        if self.session is not None:
            self.session.runtime_state = "tool_result_ready"
        self.execution.status = "completed" if success else "failed"
        self.execution.success = success
        self.execution.error_code = error_code
        self.execution.duration_ms = duration_ms
        self.execution.completed_at = now
        self.execution.result_sent_at = None
        self.execution.continuation_triggered_at = None
        self.execution.runtime_state_after = "tool_result_ready"
        self.db.commit()
        log_conversation_event(
            "tool_completed",
            session_id=self.session_id,
            tool_call_id=self.call_id,
            tool_name=self.tool_name,
            booking_state_before=self.execution.booking_state_before,
            booking_state_after=self.execution.booking_state_after,
            runtime_state_before="tool_running",
            runtime_state_after="tool_result_ready",
            duration_ms=duration_ms,
            success=success,
            error_code=error_code,
        )
