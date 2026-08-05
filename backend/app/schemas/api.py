from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    database: str


class PlatformStatusResponse(BaseModel):
    environment: str
    backend_version: str
    realtime_voice_configured: bool
    telephony_configured: bool
    calendar_configured: bool
    database_connected: bool
    realtime_model: str
    realtime_voice: str


class RealtimeVadResponse(BaseModel):
    type: str
    threshold: float | None = None
    prefix_padding_ms: int | None = None
    silence_duration_ms: int | None = None
    eagerness: str | None = None
    create_response: bool
    interrupt_response: bool


class RealtimeAgentConfigResponse(BaseModel):
    tenant_id: UUID
    tenant_name: str
    assistant_name: str
    language: str
    welcome_message: str
    instructions: str
    model: str
    voice: str
    speed: float
    configuration_version: int
    capability_keys: list[str]
    tool_names: list[str]
    maximum_session_minutes: int
    max_output_tokens: int
    transcription_enabled: bool
    raw_event_logging: bool
    vad: RealtimeVadResponse


class RealtimeClientSecretResponse(BaseModel):
    client_secret: str
    expires_at: int
    session_id: str | None = None
    model: str
    voice: str
    speed: float
    configuration_version: int
    call_session_id: UUID
    call_attempt_id: UUID
    tenant_id: UUID


class RuntimeManifestResponse(BaseModel):
    schema_version: str
    digest: str
    tenant_id: UUID
    timezone: str
    assistant_name: str
    language: str
    welcome_message: str
    instructions: str
    prompt_digest: str
    model: str
    voice: str
    speed: float
    configuration_version: int
    source_digests: dict[str, str]
    capability_keys: list[str]
    tools: list[dict[str, Any]]
    tool_names: list[str]
    tools_digest: str
    maximum_session_minutes: int
    max_output_tokens: int
    transcription_enabled: bool
    raw_event_logging: bool
    vad: RealtimeVadResponse
    setting_targets: dict[str, Literal["prompt", "session", "tools", "ui_only"]]


class RealtimeSessionBootstrapResponse(BaseModel):
    secret: RealtimeClientSecretResponse
    manifest: RuntimeManifestResponse


class RealtimeSessionBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_attempt_id: UUID


class RealtimeAttemptFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ended", "cancelled", "failed", "abandoned"]
    phase: str = Field(min_length=1, max_length=50)
    error_code: str | None = Field(default=None, max_length=100)
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, max_length=255)
    retryable: bool | None = None
    technical_message: str | None = Field(default=None, max_length=500)


class TenantSettingsResponse(ORMModel):
    assistant_name: str
    default_language: str
    welcome_message: str
    presentation_mode_enabled: bool
    diagnostics_enabled: bool


class LocationResponse(ORMModel):
    id: UUID
    name: str
    street: str
    postal_code: str
    city: str
    country_code: str
    timezone: str
    is_primary: bool


class TenantResponse(ORMModel):
    id: UUID
    slug: str
    name: str
    industry: str
    timezone: str
    status: str
    settings: TenantSettingsResponse
    primary_location: LocationResponse | None


class ServiceResponse(ORMModel):
    id: UUID
    name: str
    description: str
    duration_minutes: int
    is_active: bool


class ServiceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=5000)
    duration_minutes: int = Field(ge=5, le=720)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name darf nicht leer sein.")
        return normalized


class StaffResponse(ORMModel):
    id: UUID
    display_name: str
    role_name: str
    is_active: bool


class AppointmentResponse(ORMModel):
    id: UUID
    customer_name: str
    starts_at: datetime
    ends_at: datetime
    status: str
    source: str
    service: ServiceResponse | None
    staff_member: StaffResponse | None

