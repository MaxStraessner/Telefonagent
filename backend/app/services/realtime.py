import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.models import CallChannel, CallSession
from app.schemas.api import RealtimeAgentConfigResponse, RealtimeClientSecretResponse, RealtimeVadResponse
from app.services.agent_runtime import AgentRuntimeConfig, build_runtime_config

logger = logging.getLogger(__name__)
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
CLIENT_SECRET_TTL_SECONDS = 60


def build_safety_identifier(context: TenantContext, settings: Settings) -> str:
    digest = hmac.new(
        settings.openai_safety_identifier_salt.encode("utf-8"),
        str(context.id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"tenant_{digest[:32]}"


def agent_config(context: TenantContext, settings: Settings, db: Session) -> RealtimeAgentConfigResponse:
    runtime = build_runtime_config(db, context, settings, test_mode=True)
    config = runtime.bundle.configuration
    return RealtimeAgentConfigResponse(
        tenant_id=context.id,
        tenant_name=config.company_name,
        assistant_name=config.assistant_name,
        language=config.language,
        welcome_message=runtime.greeting,
        instructions=runtime.prompt,
        model=runtime.model,
        voice=runtime.voice,
        speed=runtime.speed,
        configuration_version=runtime.version,
        capability_keys=runtime.capability_keys,
        tool_names=[str(item.get("name", "")) for item in runtime.tools],
        maximum_session_minutes=settings.openai_realtime_max_session_minutes,
        transcription_enabled=settings.openai_realtime_transcription_enabled,
        raw_event_logging=settings.openai_realtime_log_raw_events,
        vad=RealtimeVadResponse(**runtime.turn_detection),
    )


def _upstream_payload(runtime: AgentRuntimeConfig, settings: Settings) -> dict[str, object]:
    config = runtime.bundle.configuration
    transcription: dict[str, str] | None = None
    if settings.openai_realtime_transcription_enabled:
        transcription = {"model": "gpt-4o-mini-transcribe", "language": config.language}
    return {
        "expires_after": {"anchor": "created_at", "seconds": CLIENT_SECRET_TTL_SECONDS},
        "session": {
            "type": "realtime",
            "model": runtime.model,
            "instructions": runtime.prompt,
            "output_modalities": ["audio"],
            "tools": runtime.tools,
            "tool_choice": runtime.tool_choice,
            "max_output_tokens": 256,
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": transcription,
                    "turn_detection": runtime.turn_detection,
                },
                "output": {"voice": runtime.voice, "speed": runtime.speed},
            },
        },
    }


def _provider_error(response: httpx.Response) -> HTTPException:
    provider_param = ""
    try:
        provider_error = response.json().get("error") or {}
        provider_param = str(provider_error.get("param") or "").lower()
    except (AttributeError, TypeError, ValueError):
        pass
    if "voice" in provider_param:
        code, message = "realtime_voice_unavailable", "Die konfigurierte Realtime-Stimme ist für dieses OpenAI-Projekt nicht verfügbar."
    elif "model" in provider_param:
        code, message = "realtime_model_unavailable", "Das konfigurierte Realtime-Modell ist für dieses OpenAI-Projekt nicht verfügbar."
    elif response.status_code in {401, 403}:
        code, message = "realtime_provider_authentication_failed", "Die Sprachverbindung konnte beim Anbieter nicht autorisiert werden."
    else:
        code, message = "realtime_provider_rejected", "Der Anbieter hat die Realtime-Konfiguration abgelehnt."
    logger.warning("OpenAI Realtime client secret rejected", extra={"provider_status": response.status_code})
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"code": code, "message": message})


async def create_client_secret(
    context: TenantContext, settings: Settings, db: Session,
) -> RealtimeClientSecretResponse:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "realtime_not_configured", "message": "Der OpenAI Realtime-Zugang ist serverseitig noch nicht konfiguriert."},
        )
    runtime = build_runtime_config(db, context, settings, test_mode=True)
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": build_safety_identifier(context, settings),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            response = await client.post(
                OPENAI_CLIENT_SECRETS_URL, headers=headers, json=_upstream_payload(runtime, settings),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "realtime_provider_timeout", "message": "Der Realtime-Anbieter antwortet nicht rechtzeitig."},
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("OpenAI Realtime request failed", extra={"provider_error_type": type(exc).__name__})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "realtime_provider_unavailable", "message": "Der Realtime-Anbieter ist derzeit nicht erreichbar."},
        ) from exc
    if not response.is_success:
        raise _provider_error(response)
    try:
        payload: dict[str, Any] = response.json()
        value, expires_at = payload["value"], payload["expires_at"]
        session_payload = payload.get("session") or {}
        if not isinstance(value, str) or not value.startswith("ek_") or not isinstance(expires_at, int):
            raise ValueError("unexpected response fields")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("OpenAI Realtime returned an invalid client secret response")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "realtime_provider_invalid_response", "message": "Der Realtime-Anbieter hat eine ungültige Antwort geliefert."},
        ) from exc
    call_session = CallSession(
        tenant_id=context.id, channel=CallChannel.browser, status="active",
        started_at=datetime.now(timezone.utc), configuration_version=runtime.version,
    )
    db.add(call_session)
    db.commit()
    db.refresh(call_session)
    return RealtimeClientSecretResponse(
        client_secret=value, expires_at=expires_at,
        session_id=session_payload.get("id") if isinstance(session_payload, dict) else None,
        model=runtime.model, voice=runtime.voice, speed=runtime.speed,
        configuration_version=runtime.version, call_session_id=call_session.id,
        tenant_id=context.id,
    )
