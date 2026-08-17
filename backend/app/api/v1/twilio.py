from __future__ import annotations

import asyncio
import hmac
import logging
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings, get_settings
from app.db.session import bind_tenant_context, get_db
from app.models import CallChannel, CallSession, Tenant, TenantInboundRoute, TenantStatus
from app.services.agent_runtime import build_runtime_config
from app.services.call_lifecycle import CallLifecycleError, CallLifecycleService
from app.services.conversation_tools import ConversationToolDispatcher
from app.services.tenant_resolution import InboundRouteTenantResolver, TenantResolutionError
from app.services.twilio import (
    ACCOUNT_SID_PATTERN,
    CALL_SID_PATTERN,
    STREAM_SID_PATTERN,
    TwilioServiceError,
    issue_call_token,
    media_url,
    require_twilio_provider,
    stream_status_url,
    validate_e164,
    verify_call_token,
    voice_url,
)
from app.services.twilio_media import TwilioMediaBridge, TwilioMediaError

router = APIRouter(prefix="/twilio", tags=["twilio"])
logger = logging.getLogger(__name__)
TWILIO_SETUP_TIMEOUT_SECONDS = 5


def _xml_response(root: ET.Element) -> Response:
    return Response(
        content=ET.tostring(root, encoding="unicode", short_empty_elements=True),
        media_type="application/xml",
    )


def _reject_call() -> Response:
    root = ET.Element("Response")
    say = ET.SubElement(root, "Say", {"language": "de-DE"})
    say.text = "Dieser Anschluss ist zurzeit nicht erreichbar."
    ET.SubElement(root, "Hangup")
    return _xml_response(root)


def _stream_twiml(settings: Settings, token: str) -> Response:
    root = ET.Element("Response")
    connect = ET.SubElement(root, "Connect")
    stream = ET.SubElement(
        connect,
        "Stream",
        {
            "url": media_url(settings),
            "statusCallback": stream_status_url(settings),
            "statusCallbackMethod": "POST",
        },
    )
    ET.SubElement(stream, "Parameter", {"name": "call_token", "value": token})
    ET.SubElement(root, "Hangup")
    return _xml_response(root)


async def _form_values(request: Request) -> dict[str, str]:
    form = await request.form()
    return {str(key): str(value) for key, value in form.multi_items()}


def _valid_account(settings: Settings, value: str) -> bool:
    return bool(
        settings.twilio_account_sid
        and ACCOUNT_SID_PATTERN.fullmatch(value)
        and hmac.compare_digest(value, settings.twilio_account_sid)
    )


def _validate_signature(
    settings: Settings, *, url: str, params: dict[str, str], signature: str
) -> bool:
    try:
        return require_twilio_provider(settings).validate_request(url, params, signature)
    except TwilioServiceError:
        return False


def _resolve_telephone_call(db: Session, call_sid: str) -> tuple[TenantContext, CallSession] | None:
    if db.bind and db.bind.dialect.name == "postgresql":
        tenant_id = db.scalar(
            text("SELECT resolve_telephone_call_tenant(:call_sid)"),
            {"call_sid": call_sid},
        )
        if tenant_id is None:
            return None
        bind_tenant_context(db, tenant_id)
        statement = select(CallSession, Tenant).join(
            Tenant, Tenant.id == CallSession.tenant_id
        ).where(
            CallSession.tenant_id == tenant_id,
            CallSession.channel == CallChannel.telephone,
            CallSession.provider_session_id == call_sid,
        )
    else:
        statement = select(CallSession, Tenant).join(
            Tenant, Tenant.id == CallSession.tenant_id
        ).where(
            CallSession.channel == CallChannel.telephone,
            CallSession.provider_session_id == call_sid,
        )
    rows = db.execute(statement).all()
    if len(rows) != 1:
        return None
    call, tenant = rows[0]
    return TenantContext(id=tenant.id, tenant=tenant), call


async def _receive_setup_event(
    websocket: WebSocket, *, error_code: str
) -> dict[str, object]:
    try:
        event = await asyncio.wait_for(
            websocket.receive_json(), timeout=TWILIO_SETUP_TIMEOUT_SECONDS
        )
    except WebSocketDisconnect:
        raise
    except (TimeoutError, TypeError, ValueError, RuntimeError) as exc:
        raise TwilioMediaError(error_code) from exc
    if not isinstance(event, dict):
        raise TwilioMediaError(error_code)
    return event


@router.post("/voice")
async def inbound_voice(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    params = await _form_values(request)
    signature = request.headers.get("x-twilio-signature", "")
    if not _validate_signature(
        settings, url=voice_url(settings), params=params, signature=signature
    ):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    account_sid = params.get("AccountSid", "")
    call_sid = params.get("CallSid", "")
    to_number = params.get("To", "")
    if (
        not _valid_account(settings, account_sid)
        or not CALL_SID_PATTERN.fullmatch(call_sid)
    ):
        return _reject_call()
    try:
        to_number = validate_e164(to_number)
        context = InboundRouteTenantResolver(db).resolve("phone_number", to_number)
        runtime = build_runtime_config(db, context, settings, test_mode=False)
        call, _created = CallLifecycleService(
            db, context.id
        ).provision_provider_call(CallChannel.telephone, call_sid, runtime)
        if call.status not in {"provisioned", "connected"}:
            return _reject_call()
        token = issue_call_token(
            settings,
            tenant_id=context.id,
            call_session_id=call.id,
            call_sid=call_sid,
            phone_number=to_number,
        )
        return _stream_twiml(settings, token)
    except (TenantResolutionError, TwilioServiceError):
        db.rollback()
        return _reject_call()
    except Exception:
        db.rollback()
        logger.error("twilio_voice_setup_failed", extra={"event_name": "twilio_voice_setup_failed"})
        return _reject_call()


@router.post("/stream-status", status_code=status.HTTP_204_NO_CONTENT)
async def stream_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    params = await _form_values(request)
    if not _validate_signature(
        settings,
        url=stream_status_url(settings),
        params=params,
        signature=request.headers.get("x-twilio-signature", ""),
    ):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    call_sid = params.get("CallSid", "")
    if not _valid_account(settings, params.get("AccountSid", "")) or not CALL_SID_PATTERN.fullmatch(call_sid):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    resolved = _resolve_telephone_call(db, call_sid)
    if resolved is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    context, call = resolved
    stream_event = params.get("StreamEvent", "")
    call_status = params.get("CallStatus", "")
    if stream_event == "stream-error":
        CallLifecycleService(db, context.id).finish(
            call.id, status="failed", phase="twilio_stream", error_code="twilio_stream_error"
        )
    elif stream_event == "stream-stopped" or call_status in {"completed", "canceled", "failed", "busy", "no-answer"}:
        terminal = "ended" if call_status in {"", "completed"} else "failed"
        CallLifecycleService(db, context.id).finish(
            call.id,
            status=terminal,
            phase="twilio_status_callback",
            error_code=None if terminal == "ended" else "twilio_call_failed",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/media")
async def media_stream(
    websocket: WebSocket,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    if not _validate_signature(
        settings,
        url=media_url(settings),
        params={},
        signature=websocket.headers.get("x-twilio-signature", ""),
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    context: TenantContext | None = None
    call: CallSession | None = None
    try:
        connected_event = await _receive_setup_event(
            websocket, error_code="invalid_connected_event"
        )
        if (
            connected_event.get("event") != "connected"
            or connected_event.get("protocol") != "Call"
            or connected_event.get("version") != "1.0.0"
        ):
            raise TwilioMediaError("invalid_connected_event")

        start_event = await _receive_setup_event(
            websocket, error_code="invalid_start_event"
        )
        if start_event.get("event") != "start":
            raise TwilioMediaError("invalid_start_event")
        start = start_event.get("start")
        if not isinstance(start, dict):
            raise TwilioMediaError("invalid_start_event")
        call_sid = str(start.get("callSid") or "")
        stream_sid = str(start.get("streamSid") or "")
        event_stream_sid = str(start_event.get("streamSid") or "")
        try:
            initial_sequence = int(start_event["sequenceNumber"])
            media_format = start["mediaFormat"]
            tracks = start["tracks"]
            custom_parameters = start["customParameters"]
        except (KeyError, TypeError, ValueError) as exc:
            raise TwilioMediaError("invalid_start_event") from exc
        account_sid = str(start.get("accountSid") or "")
        token = str(custom_parameters.get("call_token") or "") if isinstance(custom_parameters, dict) else ""
        if (
            not _valid_account(settings, account_sid)
            or not CALL_SID_PATTERN.fullmatch(call_sid)
            or not STREAM_SID_PATTERN.fullmatch(stream_sid)
            or event_stream_sid != stream_sid
            or initial_sequence != 1
            or not isinstance(tracks, list)
            or "inbound" not in tracks
            or not isinstance(media_format, dict)
            or media_format.get("encoding") != "audio/x-mulaw"
            or media_format.get("sampleRate") != 8000
            or media_format.get("channels") != 1
            or not token
        ):
            raise TwilioMediaError("invalid_start_event")
        tenant_id, call_id, phone_number = verify_call_token(
            settings, token, call_sid=call_sid
        )
        resolved = _resolve_telephone_call(db, call_sid)
        if resolved is None:
            raise TwilioMediaError("call_not_found")
        context, call = resolved
        if (
            context.id != tenant_id
            or call.id != call_id
            or context.tenant.status != TenantStatus.active
        ):
            raise TwilioMediaError("call_context_mismatch")
        route = db.scalar(select(TenantInboundRoute).where(
            TenantInboundRoute.tenant_id == context.id,
            TenantInboundRoute.route_type == "phone_number",
            TenantInboundRoute.normalized_identifier == phone_number,
            TenantInboundRoute.is_active.is_(True),
        ))
        if route is None:
            raise TwilioMediaError("call_route_mismatch")
        runtime = build_runtime_config(db, context, settings, test_mode=False)
        if runtime.manifest.digest != call.runtime_manifest_digest:
            raise TwilioMediaError("runtime_manifest_changed")
        CallLifecycleService(db, context.id).connect(call.id)
        dispatcher = ConversationToolDispatcher(
            db, settings, context, call.id
        )
        await TwilioMediaBridge(
            websocket,
            settings,
            context,
            runtime,
            stream_sid,
            dispatcher,
            initial_sequence,
        ).run()
        CallLifecycleService(db, context.id).finish(call.id)
    except WebSocketDisconnect:
        if context and call:
            CallLifecycleService(db, context.id).finish(call.id)
    except (TwilioMediaError, TwilioServiceError, CallLifecycleError) as exc:
        db.rollback()
        if context and call:
            CallLifecycleService(db, context.id).finish(
                call.id, status="failed", phase="telephone_bridge", error_code=str(exc)
            )
        log_context = {
            "event_name": "twilio_media_rejected",
            "error_code": str(exc),
        }
        if isinstance(exc, TwilioMediaError):
            log_context.update(exc.log_context())
        logger.info("twilio_media_rejected", extra=log_context)
        await websocket.close(code=1008)
    except Exception:
        db.rollback()
        if context and call:
            CallLifecycleService(db, context.id).finish(
                call.id, status="failed", phase="telephone_bridge", error_code="telephone_bridge_failed"
            )
        logger.error("twilio_media_failed", extra={"event_name": "twilio_media_failed"})
        await websocket.close(code=1011)
