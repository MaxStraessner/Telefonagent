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
from app.schemas.api import (
    AppliedRealtimeConfiguration,
    AppliedRealtimeConfigurationRequest,
    RealtimeAgentConfigResponse,
    RealtimeClientSecretResponse,
    RealtimeSessionBootstrapResponse,
    RuntimeConfigurationDiffResponse,
)
from app.services.agent_runtime import AgentRuntimeConfig, build_runtime_config
from app.services.tool_projections import outbound_wire_tools, outbound_wire_tools_digest

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
    manifest = runtime.manifest
    return RealtimeAgentConfigResponse(
        tenant_id=manifest.tenant_id,
        tenant_name=runtime.bundle.configuration.company_name,
        assistant_name=manifest.assistant_name,
        language=manifest.language,
        welcome_message=manifest.welcome_message,
        instructions=manifest.instructions,
        model=manifest.model,
        voice=manifest.voice,
        speed=manifest.speed,
        configuration_version=manifest.configuration_version,
        capability_keys=manifest.capability_keys,
        tool_names=manifest.tool_names,
        maximum_session_minutes=manifest.maximum_session_minutes,
        max_output_tokens=manifest.max_output_tokens,
        transcription_enabled=manifest.transcription_enabled,
        raw_event_logging=manifest.raw_event_logging,
        vad=manifest.vad,
    )


def _upstream_payload(runtime: AgentRuntimeConfig, settings: Settings) -> dict[str, object]:
    manifest = runtime.manifest
    provider_tools = outbound_wire_tools(runtime.tools)
    transcription: dict[str, str] | None = None
    if manifest.transcription_enabled:
        transcription = {"model": "gpt-4o-mini-transcribe", "language": manifest.language}
    return {
        "expires_after": {"anchor": "created_at", "seconds": CLIENT_SECRET_TTL_SECONDS},
        "session": {
            "type": "realtime",
            "model": manifest.model,
            "instructions": manifest.instructions,
            "output_modalities": ["audio"],
            "tools": provider_tools,
            "tool_choice": runtime.tool_choice,
            "parallel_tool_calls": False,
            "max_output_tokens": manifest.max_output_tokens,
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": transcription,
                    "turn_detection": manifest.vad.model_dump(exclude_none=True),
                },
                "output": {"voice": manifest.voice, "speed": manifest.speed},
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


async def _request_client_secret(
    context: TenantContext,
    settings: Settings,
    runtime: AgentRuntimeConfig,
) -> tuple[str, int, str | None]:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "realtime_not_configured", "message": "Der OpenAI Realtime-Zugang ist serverseitig noch nicht konfiguriert."},
        )
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
    return (
        value,
        expires_at,
        session_payload.get("id") if isinstance(session_payload, dict) else None,
    )


def expected_applied_configuration(runtime: AgentRuntimeConfig) -> AppliedRealtimeConfiguration:
    manifest = runtime.manifest
    return AppliedRealtimeConfiguration(
        model=manifest.model,
        voice=manifest.voice,
        speed=manifest.speed,
        language=manifest.language,
        prompt_digest=manifest.prompt_digest,
        tool_names=manifest.tool_names,
        tools_digest=outbound_wire_tools_digest(runtime.tools),
        vad=manifest.vad.model_dump(exclude_none=True),
    )


def _record_call_session(
    db: Session,
    context: TenantContext,
    runtime: AgentRuntimeConfig,
) -> CallSession:
    expected = expected_applied_configuration(runtime)
    outbound_digest = outbound_wire_tools_digest(runtime.tools)
    call_session = CallSession(
        tenant_id=context.id,
        channel=CallChannel.browser,
        status="active",
        started_at=datetime.now(timezone.utc),
        configuration_version=runtime.version,
        runtime_manifest_digest=runtime.manifest.digest,
        runtime_manifest_snapshot={
            **expected.model_dump(mode="json"),
            "canonical_tools_digest": runtime.manifest.tools_digest,
            "outbound_wire_tools_digest": outbound_digest,
        },
        configuration_status="pending",
    )
    db.add(call_session)
    db.commit()
    db.refresh(call_session)
    logger.info(
        "realtime_session_configuration_activated",
        extra={
            "event_name": "realtime_session_configuration_activated",
            "session_id": str(call_session.id),
            "tenant_id": str(context.id),
            "configuration_version": runtime.version,
            "model": runtime.model,
            "voice": runtime.voice,
            "speed": runtime.speed,
            "language": runtime.bundle.configuration.language,
            "tool_names": [str(item.get("name", "")) for item in runtime.tools],
            "canonical_tools_digest": runtime.manifest.tools_digest,
            "outbound_wire_tools_digest": outbound_digest,
            "acknowledged_tools_digest": None,
            "tool_projection_stage": "bootstrap",
            "prompt_sections": runtime.prompt_sections,
            "standard_german_instruction_active": "Standarddeutsch" in runtime.prompt,
        },
    )
    return call_session


async def create_session_bootstrap(
    context: TenantContext,
    settings: Settings,
    db: Session,
) -> RealtimeSessionBootstrapResponse:
    runtime = build_runtime_config(db, context, settings, test_mode=True)
    value, expires_at, provider_session_id = await _request_client_secret(context, settings, runtime)
    call_session = _record_call_session(db, context, runtime)
    secret = RealtimeClientSecretResponse(
        client_secret=value,
        expires_at=expires_at,
        session_id=provider_session_id,
        model=runtime.model,
        voice=runtime.voice,
        speed=runtime.speed,
        configuration_version=runtime.version,
        call_session_id=call_session.id,
        tenant_id=context.id,
    )
    return RealtimeSessionBootstrapResponse(secret=secret, manifest=runtime.manifest)


async def create_client_secret(
    context: TenantContext, settings: Settings, db: Session,
) -> RealtimeClientSecretResponse:
    return (await create_session_bootstrap(context, settings, db)).secret


def _configuration_diff(
    expected: dict[str, Any],
    applied: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    differences: dict[str, dict[str, Any]] = {}
    unobserved: list[str] = []
    for field, expected_value in expected.items():
        if field not in applied or applied[field] is None:
            unobserved.append(field)
            continue
        actual_value = applied[field]
        if field == "tool_names":
            expected_value = sorted(expected_value)
            actual_value = sorted(actual_value)
        if expected_value != actual_value:
            differences[field] = {"expected": expected_value, "actual": actual_value}
    return differences, unobserved


def apply_session_configuration(
    db: Session,
    context: TenantContext,
    session_id,
    payload: AppliedRealtimeConfigurationRequest,
) -> RuntimeConfigurationDiffResponse:
    call_session = db.get(CallSession, session_id)
    if call_session is None or call_session.tenant_id != context.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "realtime_session_not_found", "message": "Die Realtime-Sitzung wurde nicht gefunden."},
        )
    expected = call_session.runtime_manifest_snapshot or {}
    applied = payload.applied.model_dump(mode="json", exclude_none=True)
    comparison_expected = {
        key: value
        for key, value in expected.items()
        if key not in {"canonical_tools_digest", "outbound_wire_tools_digest"}
    }
    differences, unobserved = _configuration_diff(comparison_expected, applied)
    if payload.manifest_digest != call_session.runtime_manifest_digest:
        differences["manifest_digest"] = {
            "expected": call_session.runtime_manifest_digest,
            "actual": payload.manifest_digest,
        }
    call_session.applied_configuration = applied
    call_session.configuration_diff = differences
    call_session.configuration_status = "mismatch" if differences else "applied"
    db.commit()
    logger.info(
        "realtime_session_configuration_acknowledged",
        extra={
            "event_name": "realtime_session_configuration_acknowledged",
            "session_id": str(call_session.id),
            "configuration_version": call_session.configuration_version,
            "canonical_tools_digest": expected.get("canonical_tools_digest"),
            "outbound_wire_tools_digest": expected.get("outbound_wire_tools_digest"),
            "acknowledged_tools_digest": applied.get("tools_digest"),
            "tool_projection_stage": "acknowledged",
            "ack_status": call_session.configuration_status,
            "difference_keys": sorted(differences),
        },
    )
    return RuntimeConfigurationDiffResponse(
        session_id=call_session.id,
        status=call_session.configuration_status,
        manifest_digest=call_session.runtime_manifest_digest or payload.manifest_digest,
        expected=AppliedRealtimeConfiguration.model_validate(expected),
        applied=payload.applied,
        differences=differences,
        unobserved=unobserved,
    )


def session_configuration_diff(
    db: Session,
    context: TenantContext,
    session_id,
) -> RuntimeConfigurationDiffResponse:
    call_session = db.get(CallSession, session_id)
    if call_session is None or call_session.tenant_id != context.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "realtime_session_not_found", "message": "Die Realtime-Sitzung wurde nicht gefunden."},
        )
    expected = AppliedRealtimeConfiguration.model_validate(call_session.runtime_manifest_snapshot or {})
    applied = (
        AppliedRealtimeConfiguration.model_validate(call_session.applied_configuration)
        if call_session.applied_configuration
        else None
    )
    return RuntimeConfigurationDiffResponse(
        session_id=call_session.id,
        status=call_session.configuration_status,
        manifest_digest=call_session.runtime_manifest_digest or "",
        expected=expected,
        applied=applied,
        differences=call_session.configuration_diff or {},
        unobserved=[] if applied is None else [
            key for key, value in expected.model_dump().items()
            if value is not None and getattr(applied, key) is None
        ],
    )
