from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext, UserContext, get_tenant_context, get_user_context, require_agent_admin
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import CalendarBooking, CallChannel, CallSession, ToolExecution
from app.schemas.calls import (
    CallAnalysisRetryResponse,
    CallDetail,
    CallListItem,
    CallStatusUpdate,
    TranscriptTurn,
)
from app.services.audit import AuditService
from app.services.call_processing import erase_call_content

router = APIRouter(prefix="/calls", tags=["calls"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "call_not_found", "message": "Anruf nicht gefunden."},
    )


def _transcript(call: CallSession) -> list[TranscriptTurn]:
    turns: list[TranscriptTurn] = []
    for item in call.transcript or []:
        try:
            turns.append(TranscriptTurn.model_validate(item))
        except (TypeError, ValueError):
            continue
    return turns


def _list_item(call: CallSession) -> CallListItem:
    return CallListItem(
        id=call.id,
        started_at=call.started_at or call.created_at,
        caller_name=call.caller_name,
        caller_phone=call.caller_phone,
        category=call.category,
        result=call.result,
        action_required=bool(call.action_required),
        priority=call.priority,
        status=call.inbox_status,
        duration_seconds=call.duration_seconds,
        analysis_status=call.analysis_status,
        operating_mode=call.operating_mode, voice_model=call.voice_model,
        call_status=call.status, documentation_status=call.documentation_status,
        callback_required=(call.analysis_details or {}).get("callback_required"),
    )


def _detail(call: CallSession, db: Session) -> CallDetail:
    return CallDetail(
        **_list_item(call).model_dump(),
        ended_at=call.ended_at,
        external_call_id=call.provider_session_id,
        phone_number_id=call.phone_number_id,
        transcript=_transcript(call),
        summary=call.summary,
        action_items=[str(item) for item in (call.action_items or [])],
        analysis_details=call.analysis_details, analysis_error_code=call.analysis_error_code,
        error_code=call.error_code, content_deleted_at=call.content_deleted_at,
        actions=[{"tool_name": item.tool_name, "status": item.status, "success": item.success, "error_code": item.error_code}
                 for item in db.scalars(select(ToolExecution).where(ToolExecution.tenant_id == call.tenant_id, ToolExecution.call_session_id == call.id).order_by(ToolExecution.created_at))],
        appointments=[{"id": str(item.id), "status": item.status.value, "start_at": item.start_at.isoformat(),
                       "external_event_id": item.external_event_id, "service_name": item.service_name_snapshot}
                      for item in db.scalars(select(CalendarBooking).where(CalendarBooking.tenant_id == call.tenant_id, CalendarBooking.conversation_session_id == call.id))],
    )


def _tenant_call(db: Session, tenant_id: UUID, call_id: UUID, *, for_update: bool = False) -> CallSession:
    statement = select(CallSession).where(
        CallSession.id == call_id,
        CallSession.tenant_id == tenant_id,
        CallSession.channel == CallChannel.telephone,
    )
    call = db.scalar((statement.with_for_update() if for_update else statement).execution_options(populate_existing=True))
    if call is None:
        raise _not_found()
    return call


@router.get("", response_model=list[CallListItem])
def list_calls(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> list[CallListItem]:
    calls = db.scalars(
        select(CallSession)
        .where(
            CallSession.tenant_id == context.id,
            CallSession.channel == CallChannel.telephone,
        )
        .order_by(CallSession.started_at.desc(), CallSession.created_at.desc())
    ).all()
    return [_list_item(call) for call in calls]


@router.get("/{call_id}", response_model=CallDetail, response_model_exclude_unset=True)
def get_call(
    call_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> CallDetail:
    return _detail(_tenant_call(db, context.id, call_id), db)


@router.patch("/{call_id}", response_model=CallDetail)
def update_call_status(
    call_id: UUID,
    payload: CallStatusUpdate,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> CallDetail:
    call = _tenant_call(db, context.id, call_id, for_update=True)
    call.inbox_status = payload.status
    call.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(call)
    return _detail(call, db)


@router.post("/{call_id}/analysis/retry", response_model=CallAnalysisRetryResponse)
def retry_call_analysis(
    call_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CallAnalysisRetryResponse:
    call = _tenant_call(db, context.id, call_id, for_update=True)
    if call.analysis_status != "failed" or call.content_deleted_at or not call.ended_at:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "analysis_retry_not_available",
                "message": "Für diesen Anruf ist keine Wiederholung erforderlich.",
            },
        )
    call.analysis_status = "pending"
    call.analysis_attempts = 0
    call.analysis_next_attempt_at = datetime.now(timezone.utc)
    call.analysis_lease_token = None
    db.commit()
    return CallAnalysisRetryResponse(id=call.id, analysis_status=call.analysis_status)


@router.delete("/{call_id}/content", status_code=204)
def delete_call_content(
    call_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    call = _tenant_call(db, context.id, call_id, for_update=True)
    if call.ended_at is None:
        raise HTTPException(status_code=409, detail={"code": "call_active", "message": "Laufende Gespräche können noch nicht gelöscht werden."})
    erase_call_content(call)
    AuditService(db, settings).record(actor_user_id=user.id, platform_role=user.platform_role,
        tenant_id=context.id, action="call_content_deleted", target_type="call", target_id=call.id)
    db.commit()
