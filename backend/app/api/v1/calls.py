from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext, UserContext, get_tenant_context, get_user_context
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import CallChannel, CallSession
from app.schemas.calls import (
    CallAnalysisRetryResponse,
    CallDetail,
    CallListItem,
    CallStatusUpdate,
    TranscriptTurn,
)
from app.services.call_analysis import CallAnalysisService

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
    )


def _detail(call: CallSession) -> CallDetail:
    return CallDetail(
        **_list_item(call).model_dump(),
        ended_at=call.ended_at,
        external_call_id=call.provider_session_id,
        phone_number_id=call.phone_number_id,
        transcript=_transcript(call),
        summary=call.summary,
        action_items=[str(item) for item in (call.action_items or [])],
    )


def _tenant_call(db: Session, tenant_id: UUID, call_id: UUID) -> CallSession:
    call = db.scalar(
        select(CallSession).where(
            CallSession.id == call_id,
            CallSession.tenant_id == tenant_id,
            CallSession.channel == CallChannel.telephone,
            CallSession.ended_at.is_not(None),
        )
    )
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
            CallSession.ended_at.is_not(None),
        )
        .order_by(CallSession.started_at.desc(), CallSession.created_at.desc())
    ).all()
    return [_list_item(call) for call in calls]


@router.get("/{call_id}", response_model=CallDetail)
def get_call(
    call_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> CallDetail:
    return _detail(_tenant_call(db, context.id, call_id))


@router.patch("/{call_id}", response_model=CallDetail)
def update_call_status(
    call_id: UUID,
    payload: CallStatusUpdate,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> CallDetail:
    call = _tenant_call(db, context.id, call_id)
    call.inbox_status = payload.status
    call.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(call)
    return _detail(call)


@router.post("/{call_id}/analysis/retry", response_model=CallAnalysisRetryResponse)
async def retry_call_analysis(
    call_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CallAnalysisRetryResponse:
    call = _tenant_call(db, context.id, call_id)
    if call.analysis_status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "analysis_retry_not_available",
                "message": "Für diesen Anruf ist keine Wiederholung erforderlich.",
            },
        )
    call = await CallAnalysisService(db, settings, context.id).analyze(
        call_id, retry=True
    )
    return CallAnalysisRetryResponse(id=call.id, analysis_status=call.analysis_status)
