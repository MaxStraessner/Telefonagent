import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.schemas.api import (
    RealtimeAgentConfigResponse,
    RealtimeClientSecretResponse,
    RealtimeVadResponse,
)

logger = logging.getLogger(__name__)
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
CLIENT_SECRET_TTL_SECONDS = 60


@dataclass(frozen=True)
class RealtimeVadSettings:
    type: str = "server_vad"
    threshold: float = 0.5
    prefix_padding_ms: int = 300
    silence_duration_ms: int = 600
    create_response: bool = True
    interrupt_response: bool = True

    def api_payload(self) -> dict[str, object]:
        return {
            "type": self.type,
            "threshold": self.threshold,
            "prefix_padding_ms": self.prefix_padding_ms,
            "silence_duration_ms": self.silence_duration_ms,
            "create_response": self.create_response,
            "interrupt_response": self.interrupt_response,
        }


REALTIME_VAD = RealtimeVadSettings()


def _tenant_settings(context: TenantContext):
    tenant_settings = context.tenant.settings
    if tenant_settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "tenant_realtime_configuration_missing",
                "message": "Für den aktiven Mandanten fehlt die Sprachagent-Konfiguration.",
            },
        )
    return tenant_settings


def build_agent_instructions(context: TenantContext) -> str:
    tenant_settings = _tenant_settings(context)
    return "\n".join(
        [
            f"Du bist {tenant_settings.assistant_name}, der freundliche digitale Terminassistent von {context.tenant.name}.",
            "Du bist ausschließlich für Terminangelegenheiten zuständig und sprichst ausschließlich Deutsch.",
            "Führe ein natürliches, ruhiges Gespräch. Antworte überwiegend in höchstens zwei kurzen Sätzen und stelle nur eine Frage auf einmal.",
            "Erfrage bei einem Terminwunsch schrittweise gewünschte Leistung, Tag, Tageszeit und optional einen Mitarbeiterwunsch.",
            "Diese Testversion hat keine Werkzeuge und darf keine Termine buchen, ändern oder verbindlich zusagen.",
            "Erkläre transparent, dass die verbindliche Prüfung freier Termine im nächsten Entwicklungsschritt ergänzt wird.",
            "Erfinde keine Verfügbarkeiten, Leistungen, Preise oder Kundendaten und behaupte nie, auf einen Kalender zuzugreifen.",
            "Gib keine politische, medizinische, juristische oder private Beratung und täusche keine Telefonverbindung vor; dies ist ein Browser-Testgespräch.",
            f"Lehne sachfremde Fragen knapp ab und verweise zurück auf Terminangelegenheiten von {context.tenant.name}.",
            f"Begrüßung: {tenant_settings.welcome_message}",
        ]
    )


def build_safety_identifier(context: TenantContext, settings: Settings) -> str:
    digest = hmac.new(
        settings.openai_safety_identifier_salt.encode("utf-8"),
        str(context.id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"tenant_{digest[:32]}"


def agent_config(context: TenantContext, settings: Settings) -> RealtimeAgentConfigResponse:
    tenant_settings = _tenant_settings(context)
    return RealtimeAgentConfigResponse(
        tenant_id=context.id,
        tenant_name=context.tenant.name,
        assistant_name=tenant_settings.assistant_name,
        language=tenant_settings.default_language,
        welcome_message=tenant_settings.welcome_message,
        instructions=build_agent_instructions(context),
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        maximum_session_minutes=settings.openai_realtime_max_session_minutes,
        transcription_enabled=settings.openai_realtime_transcription_enabled,
        raw_event_logging=settings.openai_realtime_log_raw_events,
        vad=RealtimeVadResponse(**REALTIME_VAD.api_payload()),
    )


def _upstream_payload(context: TenantContext, settings: Settings) -> dict[str, object]:
    tenant_settings = _tenant_settings(context)
    transcription: dict[str, str] | None = None
    if settings.openai_realtime_transcription_enabled:
        transcription = {
            "model": "gpt-4o-mini-transcribe",
            "language": tenant_settings.default_language,
        }
    return {
        "expires_after": {"anchor": "created_at", "seconds": CLIENT_SECRET_TTL_SECONDS},
        "session": {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "instructions": build_agent_instructions(context),
            "output_modalities": ["audio"],
            "tools": [],
            "tool_choice": "none",
            "max_output_tokens": 256,
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": transcription,
                    "turn_detection": REALTIME_VAD.api_payload(),
                },
                "output": {"voice": settings.openai_realtime_voice},
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
        code = "realtime_voice_unavailable"
        message = "Die konfigurierte Realtime-Stimme ist für dieses OpenAI-Projekt nicht verfügbar."
    elif "model" in provider_param:
        code = "realtime_model_unavailable"
        message = "Das konfigurierte Realtime-Modell ist für dieses OpenAI-Projekt nicht verfügbar."
    elif response.status_code in {401, 403}:
        code = "realtime_provider_authentication_failed"
        message = "Die Sprachverbindung konnte beim Anbieter nicht autorisiert werden."
    else:
        code = "realtime_provider_rejected"
        message = "Der Anbieter hat die Realtime-Konfiguration abgelehnt."
    logger.warning("OpenAI Realtime client secret rejected", extra={"provider_status": response.status_code})
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"code": code, "message": message})


async def create_client_secret(
    context: TenantContext, settings: Settings
) -> RealtimeClientSecretResponse:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "realtime_not_configured",
                "message": "Der OpenAI Realtime-Zugang ist serverseitig noch nicht konfiguriert.",
            },
        )

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": build_safety_identifier(context, settings),
    }
    try:
        timeout = httpx.Timeout(8.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENAI_CLIENT_SECRETS_URL,
                headers=headers,
                json=_upstream_payload(context, settings),
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
        value = payload["value"]
        expires_at = payload["expires_at"]
        session_payload = payload.get("session") or {}
        if not isinstance(value, str) or not value.startswith("ek_") or not isinstance(expires_at, int):
            raise ValueError("unexpected response fields")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("OpenAI Realtime returned an invalid client secret response")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "realtime_provider_invalid_response", "message": "Der Realtime-Anbieter hat eine ungültige Antwort geliefert."},
        ) from exc

    return RealtimeClientSecretResponse(
        client_secret=value,
        expires_at=expires_at,
        session_id=session_payload.get("id") if isinstance(session_payload, dict) else None,
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        tenant_id=context.id,
    )
