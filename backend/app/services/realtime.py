import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.models import CallChannel, CallSession
from app.schemas.api import (
    RealtimeAgentConfigResponse,
    RealtimeAttemptFinishRequest,
    RealtimeClientSecretResponse,
    RealtimeSessionBootstrapResponse,
)
from app.services.agent_runtime import AgentRuntimeConfig, build_runtime_config
from app.services.call_lifecycle import CallLifecycleService

logger = logging.getLogger(__name__)
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
CLIENT_SECRET_TTL_SECONDS = 60
STALE_ATTEMPT_GRACE_MINUTES = 2
TERMINAL_STATUSES = frozenset({"ended", "cancelled", "failed", "abandoned"})
NON_TERMINAL_STATUSES = frozenset({"starting", "provisioned", "connected", "active"})
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk|ek)[-_][a-z0-9_-]{8,}\b"
)


@dataclass(frozen=True)
class ClientSecretGrant:
    value: str
    expires_at: int
    provider_session_id: str | None
    provider_request_id: str | None


class RealtimeServiceError(Exception):
    def __init__(
        self,
        *,
        code: str,
        user_message: str,
        response_status: int,
        phase: str,
        http_status: int | None = None,
        provider_request_id: str | None = None,
        retryable: bool = False,
        technical_message: str = "",
    ) -> None:
        super().__init__(technical_message or code)
        self.code = code
        self.user_message = user_message
        self.response_status = response_status
        self.phase = phase
        self.http_status = http_status
        self.provider_request_id = provider_request_id
        self.retryable = retryable
        self.technical_message = redact_technical_message(technical_message)


def redact_technical_message(value: object, *, maximum: int = 500) -> str:
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    message = _SECRET_PATTERN.sub("[REDACTED_CREDENTIAL]", message)
    return message[:maximum]


def build_safety_identifier(context: TenantContext, settings: Settings) -> str:
    digest = hmac.new(
        settings.openai_safety_identifier_salt.encode("utf-8"),
        str(context.id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"tenant_{digest[:32]}"


def agent_config(
    context: TenantContext, settings: Settings, db: Session
) -> RealtimeAgentConfigResponse:
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


def _upstream_payload() -> dict[str, object]:
    return {
        "expires_after": {
            "anchor": "created_at",
            "seconds": CLIENT_SECRET_TTL_SECONDS,
        },
    }


def _provider_error(
    response: httpx.Response, call_attempt_id: UUID
) -> RealtimeServiceError:
    provider_type = ""
    provider_code = ""
    provider_param = ""
    provider_message = ""
    try:
        provider_error = response.json().get("error") or {}
        provider_type = str(provider_error.get("type") or "")
        provider_code = str(provider_error.get("code") or "")
        provider_param = str(provider_error.get("param") or "").lower()
        provider_message = str(provider_error.get("message") or "")
    except (AttributeError, TypeError, ValueError):
        provider_message = "Provider returned a non-JSON error response"
    provider_request_id = response.headers.get("x-request-id")
    if "voice" in provider_param:
        code = "realtime_voice_unavailable"
        user_message = (
            "Die konfigurierte Realtime-Stimme ist für dieses "
            "OpenAI-Projekt nicht verfügbar."
        )
    elif "model" in provider_param:
        code = "realtime_model_unavailable"
        user_message = (
            "Das konfigurierte Realtime-Modell ist für dieses "
            "OpenAI-Projekt nicht verfügbar."
        )
    elif response.status_code in {401, 403}:
        code = "realtime_provider_authentication_failed"
        user_message = (
            "Die Sprachverbindung konnte beim Anbieter nicht autorisiert werden."
        )
    elif response.status_code == 429:
        code = "realtime_provider_rate_limited"
        user_message = (
            "Der Realtime-Anbieter ist vorübergehend ausgelastet. "
            "Bitte versuchen Sie es später erneut."
        )
    else:
        code = "realtime_provider_rejected"
        user_message = "Der Anbieter hat die Realtime-Konfiguration abgelehnt."
    technical_message = redact_technical_message(
        provider_message
        or f"provider_type={provider_type or '-'} "
        f"provider_code={provider_code or '-'} "
        f"provider_param={provider_param or '-'}"
    )
    logger.warning(
        "provider_request_failed",
        extra={
            "event_name": "provider_request_failed",
            "call_attempt_id": str(call_attempt_id),
            "phase": "provider_token",
            "provider_status": response.status_code,
            "provider_request_id": provider_request_id,
            "provider_error_type": provider_type or None,
            "provider_error_code": provider_code or None,
            "provider_error_param": provider_param or None,
            "retryable": response.status_code == 429
            or response.status_code >= 500,
            "technical_message": technical_message,
        },
    )
    return RealtimeServiceError(
        code=code,
        user_message=user_message,
        response_status=status.HTTP_502_BAD_GATEWAY,
        phase="provider_token",
        http_status=response.status_code,
        provider_request_id=provider_request_id,
        retryable=response.status_code == 429 or response.status_code >= 500,
        technical_message=technical_message,
    )


async def _request_client_secret(
    context: TenantContext,
    settings: Settings,
    call_attempt_id: UUID,
) -> ClientSecretGrant:
    if not settings.openai_api_key:
        raise RealtimeServiceError(
            code="realtime_not_configured",
            user_message=(
                "Der OpenAI Realtime-Zugang ist serverseitig "
                "noch nicht konfiguriert."
            ),
            response_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            phase="provider_token",
            technical_message="OPENAI_API_KEY is not configured",
        )
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": build_safety_identifier(context, settings),
        "X-Client-Request-Id": str(call_attempt_id),
    }
    logger.info(
        "provider_request_started",
        extra={
            "event_name": "provider_request_started",
            "call_attempt_id": str(call_attempt_id),
            "tenant_id": str(context.id),
            "phase": "provider_token",
            "provider_endpoint": OPENAI_CLIENT_SECRETS_URL,
            "provider_method": "POST",
            "timeout_seconds": 8,
            "session_configuration_source": "agents_sdk",
        },
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0)
        ) as client:
            response = await client.post(
                OPENAI_CLIENT_SECRETS_URL,
                headers=headers,
                json=_upstream_payload(),
            )
    except httpx.TimeoutException as exc:
        technical_message = redact_technical_message(
            f"{type(exc).__name__}: {exc}"
        )
        logger.warning(
            "provider_request_failed",
            extra={
                "event_name": "provider_request_failed",
                "call_attempt_id": str(call_attempt_id),
                "phase": "provider_token",
                "provider_status": None,
                "provider_request_id": None,
                "provider_error_type": type(exc).__name__,
                "retryable": True,
                "technical_message": technical_message,
            },
        )
        raise RealtimeServiceError(
            code="realtime_provider_timeout",
            user_message="Der Realtime-Anbieter antwortet nicht rechtzeitig.",
            response_status=status.HTTP_504_GATEWAY_TIMEOUT,
            phase="provider_token",
            retryable=True,
            technical_message=technical_message,
        ) from exc
    except httpx.HTTPError as exc:
        technical_message = redact_technical_message(
            f"{type(exc).__name__}: {exc}"
        )
        logger.warning(
            "provider_request_failed",
            extra={
                "event_name": "provider_request_failed",
                "call_attempt_id": str(call_attempt_id),
                "phase": "provider_token",
                "provider_status": None,
                "provider_request_id": None,
                "provider_error_type": type(exc).__name__,
                "retryable": True,
                "technical_message": technical_message,
            },
        )
        raise RealtimeServiceError(
            code="realtime_provider_unavailable",
            user_message="Der Realtime-Anbieter ist derzeit nicht erreichbar.",
            response_status=status.HTTP_502_BAD_GATEWAY,
            phase="provider_token",
            retryable=True,
            technical_message=technical_message,
        ) from exc
    if not response.is_success:
        raise _provider_error(response, call_attempt_id)
    provider_request_id = response.headers.get("x-request-id")
    try:
        payload: dict[str, Any] = response.json()
        value, expires_at = payload["value"], payload["expires_at"]
        session_payload = payload.get("session") or {}
        if (
            not isinstance(value, str)
            or not value.startswith("ek_")
            or not isinstance(expires_at, int)
        ):
            raise ValueError("unexpected response fields")
    except (KeyError, TypeError, ValueError) as exc:
        technical_message = redact_technical_message(
            f"Invalid client secret response: {exc}"
        )
        logger.warning(
            "provider_request_failed",
            extra={
                "event_name": "provider_request_failed",
                "call_attempt_id": str(call_attempt_id),
                "phase": "provider_token",
                "provider_status": response.status_code,
                "provider_request_id": provider_request_id,
                "provider_error_type": type(exc).__name__,
                "retryable": False,
                "technical_message": technical_message,
            },
        )
        raise RealtimeServiceError(
            code="realtime_provider_invalid_response",
            user_message=(
                "Der Realtime-Anbieter hat eine ungültige Antwort geliefert."
            ),
            response_status=status.HTTP_502_BAD_GATEWAY,
            phase="provider_token",
            http_status=response.status_code,
            provider_request_id=provider_request_id,
            technical_message=technical_message,
        ) from exc
    provider_session_id = (
        session_payload.get("id") if isinstance(session_payload, dict) else None
    )
    logger.info(
        "provider_request_succeeded",
        extra={
            "event_name": "provider_request_succeeded",
            "call_attempt_id": str(call_attempt_id),
            "phase": "provider_token",
            "provider_status": response.status_code,
            "provider_request_id": provider_request_id,
            "provider_session_id": provider_session_id,
            "expires_at": expires_at,
        },
    )
    return ClientSecretGrant(
        value=value,
        expires_at=expires_at,
        provider_session_id=provider_session_id,
        provider_request_id=provider_request_id,
    )


def _attempt_query(context: TenantContext, call_attempt_id: UUID):
    return select(CallSession).where(
        CallSession.tenant_id == context.id,
        CallSession.call_attempt_id == call_attempt_id,
        CallSession.channel == CallChannel.browser,
    )


def _attempt_for_update(
    db: Session, context: TenantContext, call_attempt_id: UUID
) -> CallSession | None:
    return db.scalar(_attempt_query(context, call_attempt_id).with_for_update())


def _attempt_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "realtime_attempt_unavailable",
            "message": "Dieser Gesprächsversuch kann nicht mehr gestartet werden.",
        },
    )


def _create_starting_attempt(
    db: Session, context: TenantContext, call_attempt_id: UUID
) -> CallSession:
    existing = db.scalar(_attempt_query(context, call_attempt_id))
    if existing is not None:
        raise _attempt_conflict()
    now = datetime.now(timezone.utc)
    call_session = CallSession(
        tenant_id=context.id,
        call_attempt_id=call_attempt_id,
        channel=CallChannel.browser,
        status="starting",
        started_at=now,
        configuration_status="pending",
        runtime_state="connecting",
        bootstrap_status="starting",
    )
    db.add(call_session)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _attempt_conflict() from exc
    db.refresh(call_session)
    return call_session


def _record_runtime(
    db: Session,
    call_session: CallSession,
    runtime: AgentRuntimeConfig,
) -> None:
    CallLifecycleService(db, call_session.tenant_id).record_runtime(
        call_session, runtime
    )
    call_session.configuration_status = "sdk_pending"
    db.commit()


def _mark_failed(
    db: Session,
    context: TenantContext,
    call_attempt_id: UUID,
    failure: RealtimeServiceError,
) -> None:
    db.expire_all()
    call_session = _attempt_for_update(db, context, call_attempt_id)
    if call_session is None or call_session.status in TERMINAL_STATUSES:
        db.rollback()
        return
    call_session.status = "failed"
    call_session.ended_at = datetime.now(timezone.utc)
    call_session.failure_phase = failure.phase
    call_session.error_code = failure.code
    call_session.http_status = failure.http_status
    call_session.provider_request_id = failure.provider_request_id
    call_session.failure_retryable = failure.retryable
    call_session.bootstrap_status = "failed"
    call_session.runtime_state = "failed"
    db.commit()


def abandon_stale_attempts(
    db: Session,
    context: TenantContext,
    settings: Settings,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=(
            settings.openai_realtime_max_session_minutes
            + STALE_ATTEMPT_GRACE_MINUTES
        )
    )
    attempts = db.scalars(
        select(CallSession).where(
            CallSession.tenant_id == context.id,
            CallSession.channel == CallChannel.browser,
            CallSession.status.in_(NON_TERMINAL_STATUSES),
            CallSession.created_at < cutoff,
        )
    ).all()
    if not attempts:
        return 0
    ended_at = datetime.now(timezone.utc)
    for attempt in attempts:
        attempt.status = "abandoned"
        attempt.ended_at = ended_at
        attempt.failure_phase = "stale_reconciliation"
        attempt.error_code = "realtime_attempt_expired"
        attempt.failure_retryable = False
        attempt.runtime_state = "abandoned"
    db.commit()
    logger.info(
        "stale_realtime_attempts_abandoned",
        extra={
            "event_name": "stale_realtime_attempts_abandoned",
            "tenant_id": str(context.id),
            "attempt_count": len(attempts),
            "cutoff": cutoff.isoformat(),
        },
    )
    return len(attempts)


async def create_session_bootstrap(
    context: TenantContext,
    settings: Settings,
    db: Session,
    call_attempt_id: UUID,
) -> RealtimeSessionBootstrapResponse:
    abandon_stale_attempts(db, context, settings)
    call_session = _create_starting_attempt(db, context, call_attempt_id)
    logger.info(
        "session_bootstrap_started",
        extra={
            "event_name": "session_bootstrap_started",
            "call_attempt_id": str(call_attempt_id),
            "session_id": str(call_session.id),
            "tenant_id": str(context.id),
        },
    )
    try:
        runtime = build_runtime_config(db, context, settings, test_mode=True)
        logger.info(
            "tenant_configuration_loaded",
            extra={
                "event_name": "tenant_configuration_loaded",
                "call_attempt_id": str(call_attempt_id),
                "tenant_id": str(context.id),
                "configuration_version": runtime.version,
            },
        )
        _record_runtime(db, call_session, runtime)
        manifest = runtime.manifest
        logger.info(
            "runtime_manifest_created",
            extra={
                "event_name": "runtime_manifest_created",
                "call_attempt_id": str(call_attempt_id),
                "tenant_id": str(context.id),
                "manifest_digest": manifest.digest,
                "prompt_digest": manifest.prompt_digest,
                "tools_digest": manifest.tools_digest,
                "tool_names": manifest.tool_names,
                "model": manifest.model,
                "voice": manifest.voice,
            },
        )
        grant = await _request_client_secret(
            context, settings, call_attempt_id
        )
    except RealtimeServiceError as exc:
        _mark_failed(db, context, call_attempt_id, exc)
        raise HTTPException(
            status_code=exc.response_status,
            detail={"code": exc.code, "message": exc.user_message},
        ) from exc
    except Exception:
        failure = RealtimeServiceError(
            code="realtime_runtime_configuration_failed",
            user_message=(
                "Die Realtime-Konfiguration konnte nicht vorbereitet werden."
            ),
            response_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            phase="runtime_manifest",
            technical_message="Runtime manifest construction failed",
        )
        _mark_failed(db, context, call_attempt_id, failure)
        logger.exception(
            "session_bootstrap_failed",
            extra={
                "event_name": "session_bootstrap_failed",
                "call_attempt_id": str(call_attempt_id),
                "phase": failure.phase,
                "error_code": failure.code,
            },
        )
        raise

    db.expire_all()
    current = _attempt_for_update(db, context, call_attempt_id)
    if current is None or current.status in TERMINAL_STATUSES:
        db.rollback()
        logger.info(
            "session_bootstrap_discarded",
            extra={
                "event_name": "session_bootstrap_discarded",
                "call_attempt_id": str(call_attempt_id),
                "reason": "attempt_terminal_before_provider_response",
            },
        )
        raise _attempt_conflict()
    current.status = "provisioned"
    current.provider_session_id = grant.provider_session_id
    current.provider_request_id = grant.provider_request_id
    current.bootstrap_status = "completed"
    db.commit()
    db.refresh(current)
    logger.info(
        "session_bootstrap_succeeded",
        extra={
            "event_name": "session_bootstrap_succeeded",
            "call_attempt_id": str(call_attempt_id),
            "session_id": str(current.id),
            "tenant_id": str(context.id),
            "provider_session_id": grant.provider_session_id,
            "provider_request_id": grant.provider_request_id,
            "configuration_version": runtime.version,
        },
    )
    secret = RealtimeClientSecretResponse(
        client_secret=grant.value,
        expires_at=grant.expires_at,
        session_id=grant.provider_session_id,
        model=runtime.model,
        voice=runtime.voice,
        speed=runtime.speed,
        configuration_version=runtime.version,
        call_session_id=current.id,
        call_attempt_id=call_attempt_id,
        tenant_id=context.id,
    )
    return RealtimeSessionBootstrapResponse(
        secret=secret, manifest=runtime.manifest
    )


async def create_client_secret(
    context: TenantContext, settings: Settings, db: Session
) -> RealtimeClientSecretResponse:
    return (
        await create_session_bootstrap(
            context, settings, db, call_attempt_id=uuid4()
        )
    ).secret


def mark_attempt_connected(
    db: Session, context: TenantContext, call_attempt_id: UUID
) -> None:
    call_session = _attempt_for_update(db, context, call_attempt_id)
    if call_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "realtime_attempt_not_found",
                "message": "Der Gesprächsversuch wurde nicht gefunden.",
            },
        )
    if call_session.status == "connected":
        db.rollback()
        return
    if call_session.status != "provisioned":
        db.rollback()
        raise _attempt_conflict()
    call_session.status = "connected"
    call_session.connected_at = datetime.now(timezone.utc)
    call_session.configuration_status = "sdk_managed"
    call_session.runtime_state = "idle"
    db.commit()
    logger.info(
        "call_connected",
        extra={
            "event_name": "call_connected",
            "call_attempt_id": str(call_attempt_id),
            "session_id": str(call_session.id),
            "tenant_id": str(context.id),
        },
    )


def finish_attempt(
    db: Session,
    context: TenantContext,
    call_attempt_id: UUID,
    payload: RealtimeAttemptFinishRequest,
) -> None:
    logger.info(
        "session_cleanup_started",
        extra={
            "event_name": "session_cleanup_started",
            "call_attempt_id": str(call_attempt_id),
            "tenant_id": str(context.id),
            "terminal_status": payload.status,
            "phase": payload.phase,
        },
    )
    call_session = _attempt_for_update(db, context, call_attempt_id)
    created_terminal = call_session is None
    if call_session is None:
        call_session = CallSession(
            tenant_id=context.id,
            call_attempt_id=call_attempt_id,
            channel=CallChannel.browser,
            status=payload.status,
            ended_at=datetime.now(timezone.utc),
            failure_phase=payload.phase,
            error_code=payload.error_code,
            http_status=payload.http_status,
            provider_request_id=payload.provider_request_id,
            failure_retryable=payload.retryable,
            configuration_status="not_started",
            runtime_state=payload.status,
            bootstrap_status="not_started",
        )
        db.add(call_session)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            existing = _attempt_for_update(db, context, call_attempt_id)
            if existing is None:
                logger.warning(
                    "session_cleanup_failed",
                    extra={
                        "event_name": "session_cleanup_failed",
                        "call_attempt_id": str(call_attempt_id),
                        "tenant_id": str(context.id),
                        "error_code": "realtime_attempt_not_found",
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "realtime_attempt_not_found",
                        "message": "Der Gesprächsversuch wurde nicht gefunden.",
                    },
                ) from exc
            call_session = existing
            created_terminal = False
    idempotent = not created_terminal and call_session.status in TERMINAL_STATUSES
    if created_terminal:
        pass
    elif idempotent:
        db.rollback()
    else:
        call_session.status = payload.status
        call_session.ended_at = datetime.now(timezone.utc)
        call_session.failure_phase = payload.phase
        call_session.error_code = payload.error_code
        call_session.http_status = payload.http_status
        if payload.provider_request_id:
            call_session.provider_request_id = payload.provider_request_id
        call_session.failure_retryable = payload.retryable
        call_session.runtime_state = payload.status
        if call_session.bootstrap_status == "starting":
            call_session.bootstrap_status = payload.status
        db.commit()
    technical_message = redact_technical_message(payload.technical_message)
    logger_method = logger.warning if payload.status == "failed" else logger.info
    logger_method(
        "session_state_reset",
        extra={
            "event_name": "session_state_reset",
            "call_attempt_id": str(call_attempt_id),
            "session_id": str(call_session.id),
            "tenant_id": str(context.id),
            "terminal_status": payload.status,
            "phase": payload.phase,
            "error_code": payload.error_code,
            "http_status": payload.http_status,
            "provider_request_id": payload.provider_request_id,
            "retryable": payload.retryable,
            "technical_message": technical_message or None,
            "idempotent": idempotent,
        },
    )
    logger.info(
        "session_cleanup_completed",
        extra={
            "event_name": "session_cleanup_completed",
            "call_attempt_id": str(call_attempt_id),
            "session_id": str(call_session.id),
            "tenant_id": str(context.id),
            "terminal_status": call_session.status,
            "idempotent": idempotent,
        },
    )
