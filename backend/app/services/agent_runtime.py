import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.models import BookingConfiguration
from app.schemas.api import RuntimeManifestResponse, RuntimeRecoveryPolicy
from app.services.agent_configuration import AgentBundle, is_open_now, load_agent_bundle
from app.services.capabilities import active_capabilities, realtime_tools
from app.services.prompt_compiler import SECTION_NAMES, compile_agent_prompt
from app.services.tool_projections import ToolProjectionError, canonical_tools_digest

RUNTIME_MANIFEST_SCHEMA_VERSION = "1"
RECOVERY_POLICY = RuntimeRecoveryPolicy(
    continuation_ack_timeout_ms=4_000,
    recovery_response_timeout_ms=8_000,
    maximum_attempts_per_turn=1,
)
SETTING_TARGETS = {
    "company_name": "prompt",
    "assistant_name": "prompt",
    "assistant_role": "prompt",
    "transparency_notice": "prompt",
    "address_formality": "prompt",
    "language": "session",
    "standard_greeting": "prompt",
    "outside_hours_greeting": "prompt",
    "test_greeting": "prompt",
    "farewell": "prompt",
    "voice": "session",
    "speech_speed": "session",
    "pronunciation_instructions": "prompt",
    "pronunciation_style": "prompt",
    "regional_accent": "prompt",
    "tone": "prompt",
    "custom_style_instructions": "prompt",
    "response_length": "prompt",
    "question_style": "prompt",
    "turn_detection_type": "session",
    "turn_eagerness": "session",
    "vad_threshold": "session",
    "prefix_padding_ms": "session",
    "silence_duration_ms": "session",
    "interruptions_enabled": "session",
    "idle_prompt_enabled": "session",
    "idle_timeout_ms": "session",
    "primary_task": "prompt",
    "off_topic_behavior": "prompt",
    "off_topic_mode": "prompt",
    "uncertainty_behavior": "prompt",
    "uncertainty_modes": "prompt",
    "fallback_message": "prompt",
    "topics": "prompt",
    "custom_rules": "prompt",
    "knowledge": "prompt",
    "capabilities": "tools",
    "booking_configuration": "tools",
    "simple_mode": "ui_only",
}


@dataclass(frozen=True)
class AgentRuntimeConfig:
    bundle: AgentBundle
    prompt: str
    greeting: str
    model: str
    voice: str
    speed: float
    turn_detection: dict[str, object]
    tools: list[dict[str, object]]
    tool_choice: str
    capability_keys: list[str]
    prompt_sections: list[str]
    business_hours_status: str
    timezone: str
    manifest: RuntimeManifestResponse

    @property
    def version(self) -> int:
        return self.bundle.configuration.version


def _json_safe(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        _json_safe(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_values(model, *, excluded: set[str] | None = None) -> dict[str, object]:
    excluded = excluded or set()
    return {
        column.name: _json_safe(getattr(model, column.name))
        for column in model.__table__.columns
        if column.name not in excluded
    }


def _source_digests(
    bundle: AgentBundle,
    booking: BookingConfiguration | None,
    context: TenantContext,
) -> dict[str, str]:
    common_excluded = {"id", "tenant_id", "created_at", "updated_at", "updated_by_user_id"}
    knowledge = {
        "profile": _model_values(bundle.profile, excluded=common_excluded),
        "topics": [_model_values(item, excluded=common_excluded) for item in bundle.topics],
        "rules": [_model_values(item, excluded=common_excluded) for item in bundle.rules],
        "faqs": [_model_values(item, excluded=common_excluded) for item in bundle.faqs],
        "services": [_model_values(item, excluded=common_excluded) for item in bundle.services],
        "business_hours": [_model_values(item, excluded=common_excluded) for item in bundle.business_hours],
    }
    capabilities = [_model_values(item, excluded=common_excluded) for item in bundle.capabilities]
    booking_values = (
        _model_values(booking, excluded=common_excluded) if booking is not None else {"configured": False}
    )
    return {
        "agent_configuration": _digest(
            _model_values(bundle.configuration, excluded=common_excluded)
        ),
        "knowledge": _digest(knowledge),
        "capabilities": _digest(capabilities),
        "booking_configuration": _digest(booking_values),
        "tenant": _digest(
            {
                "id": context.id,
                "name": context.tenant.name,
                "timezone": context.tenant.timezone,
            }
        ),
    }


def _validated_timezone(context: TenantContext) -> str:
    try:
        ZoneInfo(context.tenant.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "invalid_tenant_timezone",
                "message": "Die Unternehmenszeitzone ist ungültig konfiguriert.",
            },
        ) from exc
    return context.tenant.timezone


def build_runtime_config(
    db: Session, context: TenantContext, settings: Settings, *, test_mode: bool,
) -> AgentRuntimeConfig:
    bundle = load_agent_bundle(db, context.id)
    config = bundle.configuration
    timezone_name = _validated_timezone(context)
    business_hours_status = "open" if is_open_now(bundle, timezone_name) else "closed"
    if test_mode:
        greeting = config.test_greeting
    else:
        greeting = config.standard_greeting if is_open_now(bundle, timezone_name) else config.outside_hours_greeting
    if config.turn_detection_type.value == "semantic_vad":
        turn_detection: dict[str, object] = {
            "type": "semantic_vad", "eagerness": config.turn_eagerness.value,
            "create_response": True, "interrupt_response": config.interruptions_enabled,
        }
    else:
        turn_detection = {
            "type": "server_vad", "threshold": config.vad_threshold,
            "prefix_padding_ms": config.prefix_padding_ms,
            "silence_duration_ms": config.silence_duration_ms,
            "create_response": True, "interrupt_response": config.interruptions_enabled,
        }
        if config.idle_prompt_enabled:
            turn_detection["idle_timeout_ms"] = config.idle_timeout_ms
    capabilities = active_capabilities(bundle)
    tools = realtime_tools(bundle)
    try:
        tools_digest = canonical_tools_digest(tools)
    except ToolProjectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "invalid_realtime_tool_contract",
                "message": "Der kanonische Realtime-Toolvertrag ist ungültig konfiguriert.",
            },
        ) from exc
    prompt = compile_agent_prompt(bundle, greeting)
    booking = db.scalar(
        select(BookingConfiguration).where(BookingConfiguration.tenant_id == context.id)
    )
    source_digests = _source_digests(bundle, booking, context)
    tool_names = [str(item.get("name", "")) for item in tools]
    manifest_material = {
        "schema_version": RUNTIME_MANIFEST_SCHEMA_VERSION,
        "tenant_id": context.id,
        "timezone": timezone_name,
        "assistant_name": config.assistant_name,
        "language": config.language,
        "welcome_message": greeting,
        "instructions": prompt,
        "prompt_digest": _digest(prompt),
        "model": settings.openai_realtime_model,
        "voice": config.voice,
        "speed": config.speech_speed,
        "configuration_version": config.version,
        "source_digests": source_digests,
        "capability_keys": [item.key for item in capabilities],
        "tools": tools,
        "tool_names": tool_names,
        "tools_digest": tools_digest,
        "maximum_session_minutes": settings.openai_realtime_max_session_minutes,
        "max_output_tokens": settings.openai_realtime_max_output_tokens,
        "transcription_enabled": settings.openai_realtime_transcription_enabled,
        "raw_event_logging": settings.openai_realtime_log_raw_events,
        "vad": turn_detection,
        "recovery": RECOVERY_POLICY.model_dump(),
        "setting_targets": SETTING_TARGETS,
    }
    manifest = RuntimeManifestResponse(
        digest=_digest(manifest_material),
        **manifest_material,
    )
    return AgentRuntimeConfig(
        bundle=bundle, prompt=prompt, greeting=greeting,
        model=settings.openai_realtime_model, voice=config.voice, speed=config.speech_speed,
        turn_detection=turn_detection, tools=tools,
        tool_choice="auto" if tools else "none", capability_keys=[item.key for item in capabilities],
        prompt_sections=SECTION_NAMES, business_hours_status=business_hours_status,
        timezone=timezone_name, manifest=manifest,
    )
