import logging
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger("telefonagent.conversation")


def log_conversation_event(
    event_name: str,
    *,
    session_id: UUID,
    turn_id: str | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    booking_state_before: str | None = None,
    booking_state_after: str | None = None,
    runtime_state_before: str | None = None,
    runtime_state_after: str | None = None,
    response_id: str | None = None,
    duration_ms: int | None = None,
    success: bool | None = None,
    error_code: str | None = None,
) -> None:
    logger.info(
        event_name,
        extra={
            "event_name": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": str(session_id),
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "booking_state_before": booking_state_before,
            "booking_state_after": booking_state_after,
            "runtime_state_before": runtime_state_before,
            "runtime_state_after": runtime_state_after,
            "response_id": response_id,
            "duration_ms": duration_ms,
            "success": success,
            "error_code": error_code,
        },
    )
