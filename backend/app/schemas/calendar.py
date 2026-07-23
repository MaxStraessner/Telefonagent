from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProviderName = Literal["google", "microsoft"]


class ProviderConfigurationResponse(BaseModel):
    provider: ProviderName
    label: str
    configured: bool
    missing_configuration: list[str]


class ExternalCalendarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID = Field(validation_alias="calendar_connection_id")
    external_calendar_id: str
    calendar_name: str
    calendar_timezone: str
    owner_name: str
    access_role: str
    is_primary: bool
    can_write: bool
    is_selected_for_availability: bool
    is_selected_for_booking: bool
    last_seen_at: datetime


class CalendarConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: ProviderName
    account_email: str
    display_name: str
    connection_status: Literal["connected", "reauthorization_required", "error", "disconnected"]
    last_successful_request_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    calendars: list[ExternalCalendarResponse] = []


class CalendarConnectionsOverview(BaseModel):
    providers: list[ProviderConfigurationResponse]
    connections: list[CalendarConnectionResponse]


class OAuthStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class ConnectionTestResponse(BaseModel):
    success: bool
    calendars_found: int
    availability_calendars_read: int
    checked_from: datetime
    checked_until: datetime


class CalendarSelectionItem(BaseModel):
    calendar_id: UUID
    is_selected_for_availability: bool
    is_selected_for_booking: bool


class CalendarSelectionUpdate(BaseModel):
    calendars: list[CalendarSelectionItem] = Field(min_length=1)


class BusinessHourInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    is_active: bool = True

    @model_validator(mode="after")
    def end_is_after_start(self):
        if self.is_active and self.end_time <= self.start_time:
            raise ValueError("Die Endzeit muss nach der Startzeit liegen.")
        return self


class BookingConfigurationUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)
    slot_interval_minutes: int = Field(ge=5, le=120)
    minimum_notice_minutes: int = Field(ge=0, le=43_200)
    maximum_booking_horizon_days: int = Field(ge=1, le=730)
    buffer_before_minutes: int = Field(ge=0, le=240)
    buffer_after_minutes: int = Field(ge=0, le=240)
    maximum_suggestions_per_request: int = Field(ge=1, le=10)
    business_hours: list[BusinessHourInput]


class BookingConfigurationResponse(BookingConfigurationUpdate):
    id: UUID
    tenant_id: UUID
    updated_at: datetime


class AppointmentTypeWrite(BaseModel):
    service_id: UUID
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=240)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=240)
    location_type: Literal["phone", "onsite", "video", "custom"] = "phone"
    location_text: str = Field(default="", max_length=300)
    is_active: bool = True

class AppointmentTypeResponse(AppointmentTypeWrite):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    description: str
    duration_minutes: int
    service_name: str
    created_at: datetime
    updated_at: datetime


class AvailabilitySearchRequest(BaseModel):
    appointment_type_id: UUID
    search_start: datetime
    search_end: datetime
    preferred_day: date | None = None
    preferred_time_range: Literal["morning", "afternoon", "evening"] | None = None
    maximum_results: int | None = Field(default=None, ge=1, le=10)


class AgentAvailabilityRequest(BaseModel):
    appointment_type_id: UUID
    preferred_date: date | None = None
    preferred_time_of_day: Literal["morning", "afternoon", "evening"] | None = None
    search_days: int = Field(default=7, ge=1, le=30)


class AvailableSlotResponse(BaseModel):
    slot_id: str
    start: datetime
    end: datetime
    spoken_date: str
    spoken_time: str


class AvailabilityResponse(BaseModel):
    success: bool = True
    timezone: str
    slots: list[AvailableSlotResponse]


class CalendarBookingCreate(BaseModel):
    slot_id: str = Field(min_length=20, max_length=4000)
    appointment_type_id: UUID
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str = Field(default="", max_length=50)
    customer_email: str = Field(default="", max_length=320)
    customer_notes: str = Field(default="", max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=200)
    service_id: UUID | None = None

    @field_validator("customer_name", "idempotency_key")
    @classmethod
    def strip_required_booking_values(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Pflichtfeld darf nicht leer sein.")
        return normalized


class CalendarBookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool
    booking_id: UUID | None = None
    status: Literal["pending", "confirmed", "failed", "cancelled"] | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone: str | None = None
    error_code: str | None = None
    message: str | None = None
    alternative_slots: list[AvailableSlotResponse] = []
    external_event_id: str | None = None
    calendar_name: str | None = None
    service_name: str | None = None


class BookingDetailResponse(BaseModel):
    id: UUID
    appointment_type_id: UUID
    customer_name: str
    customer_phone: str
    customer_email: str
    customer_notes: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: Literal["pending", "confirmed", "failed", "cancelled"]
    source: Literal["voice_agent", "admin_api"]
    created_at: datetime


class ExactAvailabilityRequest(BaseModel):
    service_id: UUID
    appointment_type_id: UUID
    requested_start: datetime
    timezone: str = Field(min_length=1, max_length=100)


class ExactAvailabilityResponse(BaseModel):
    available: bool
    appointment_start: datetime
    appointment_end: datetime
    blocked_start: datetime
    blocked_end: datetime
    slot_id: str | None = None
    reason: str | None = None
    alternatives: list[AvailableSlotResponse] = []


class AgentAppointmentCreate(BaseModel):
    service_id: UUID
    appointment_type_id: UUID
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str | None = Field(default=None, max_length=50)
    customer_email: str | None = Field(default=None, max_length=320)
    start_at: datetime
    timezone: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=200)
    confirmed: bool


class CalendarEntryResponse(BaseModel):
    id: str
    kind: Literal["platform", "external"]
    service_name: str
    customer_name: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    appointment_format: str
    location: str
    status: str
    sync_status: str
    source: str
    calendar_provider: str
    calendar_id: str
    calendar_name: str
    external_event_id: str | None
    buffer_before_minutes: int
    buffer_after_minutes: int
    created_at: datetime | None = None


class CalendarAgendaResponse(BaseModel):
    calendar_connected: bool
    entries: list[CalendarEntryResponse]


class ConversationToolRequest(BaseModel):
    session_id: UUID
    tool_call_id: str = Field(min_length=1, max_length=200)


class ConversationBootstrapRequest(BaseModel):
    session_id: UUID


class ConversationBootstrapResponse(BaseModel):
    success: bool
    state: str
    snapshot_status: Literal["ready", "unavailable"]
    error_code: str | None = None


class ListBookableServicesRequest(ConversationToolRequest):
    pass


class ResolveServiceRequest(ConversationToolRequest):
    service_name: str = Field(min_length=1, max_length=150)


class ResolveBookingDateTimeRequest(ConversationToolRequest):
    expression: str = Field(min_length=1, max_length=200)


class ResolveBookingDateTimeResponse(BaseModel):
    status: Literal[
        "concrete",
        "search_window",
        "clarification_required",
        "past",
        "out_of_horizon",
        "invalid",
    ]
    timezone: str
    start: datetime | None = None
    end: datetime | None = None
    speech: str | None = None
    reason: str | None = None
    explicit_year: bool
    resolution_version: int


class SnapshotAvailabilityRequest(ConversationToolRequest):
    service_id: UUID
    appointment_type_id: UUID
    requested_start: datetime
    timezone: str = Field(min_length=1, max_length=100)


class AlternativeSlotsRequest(ConversationToolRequest):
    service_id: UUID
    appointment_type_id: UUID
    search_start: datetime
    search_days: int = Field(default=7, ge=1, le=30)
    preferred_day: date | None = None
    preferred_time_of_day: Literal["morning", "afternoon", "evening"] | None = None
    maximum_results: int = Field(default=3, ge=1, le=10)


class SnapshotAvailabilityResponse(ExactAvailabilityResponse):
    source: Literal["snapshot", "targeted_refresh"]
    preliminary: bool = True


class FinalizeAppointmentRequest(ConversationToolRequest):
    service_id: UUID
    appointment_type_id: UUID
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str | None = Field(default=None, max_length=50)
    customer_email: str | None = Field(default=None, max_length=320)
    start_at: datetime
    timezone: str = Field(min_length=1, max_length=100)
    confirmation_version: int = Field(ge=1)
    confirmation_utterance: str = Field(min_length=1, max_length=300)
    confirmed: Literal[True]
