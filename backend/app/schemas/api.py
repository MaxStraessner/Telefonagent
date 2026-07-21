from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
    tenant_id: UUID


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

