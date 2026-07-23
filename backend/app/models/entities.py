import enum
import uuid
from datetime import datetime, time

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TenantStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    inactive = "inactive"


class AppointmentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class AppointmentSource(str, enum.Enum):
    web_test = "web_test"
    voice_agent = "voice_agent"
    manual = "manual"
    external_calendar = "external_calendar"


class CallChannel(str, enum.Enum):
    browser = "browser"
    telephone = "telephone"


class TenantRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class AddressFormality(str, enum.Enum):
    formal = "formal"
    informal = "informal"


class ResponseLength(str, enum.Enum):
    very_short = "very_short"
    short = "short"
    balanced = "balanced"
    detailed = "detailed"


class TurnDetectionType(str, enum.Enum):
    server_vad = "server_vad"
    semantic_vad = "semantic_vad"


class TurnEagerness(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CalendarProviderName(str, enum.Enum):
    google = "google"
    microsoft = "microsoft"


class CalendarConnectionStatus(str, enum.Enum):
    connected = "connected"
    reauthorization_required = "reauthorization_required"
    error = "error"
    disconnected = "disconnected"


class CalendarLocationType(str, enum.Enum):
    phone = "phone"
    onsite = "onsite"
    video = "video"
    custom = "custom"


class CalendarBookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"
    cancelled = "cancelled"


class CalendarBookingSource(str, enum.Enum):
    voice_agent = "voice_agent"
    admin_api = "admin_api"


class BookingState(str, enum.Enum):
    idle = "idle"
    catalog_loading = "catalog_loading"
    ready = "ready"
    service_required = "service_required"
    service_selected = "service_selected"
    date_time_required = "date_time_required"
    date_time_resolving = "date_time_resolving"
    availability_checking = "availability_checking"
    slot_available = "slot_available"
    slot_unavailable = "slot_unavailable"
    customer_data_required = "customer_data_required"
    confirmation_required = "confirmation_required"
    final_check_running = "final_check_running"
    booking_running = "booking_running"
    booking_confirmed = "booking_confirmed"
    booking_failed = "booking_failed"
    completed = "completed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64))
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus, native_enum=False), default=TenantStatus.draft)
    settings: Mapped["TenantSettings | None"] = relationship(back_populates="tenant", uselist=False)
    locations: Mapped[list["Location"]] = relationship(back_populates="tenant")


class AppUser(Base, TimestampMixin):
    __tablename__ = "app_users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TenantMembership(Base, TimestampMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_users.id"), index=True)
    role: Mapped[TenantRole] = mapped_column(Enum(TenantRole, native_enum=False), default=TenantRole.member)
    user: Mapped[AppUser] = relationship()


class TenantSettings(Base, TimestampMixin):
    __tablename__ = "tenant_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    assistant_name: Mapped[str] = mapped_column(String(100))
    default_language: Mapped[str] = mapped_column(String(10), default="de")
    welcome_message: Mapped[str] = mapped_column(Text)
    presentation_mode_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    diagnostics_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant: Mapped[Tenant] = relationship(back_populates="settings")


class AgentConfiguration(Base, TimestampMixin):
    __tablename__ = "agent_configurations"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_agent_configurations_tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    company_name: Mapped[str] = mapped_column(String(200))
    assistant_name: Mapped[str] = mapped_column(String(100))
    assistant_role: Mapped[str] = mapped_column(String(200))
    transparency_notice: Mapped[str] = mapped_column(Text)
    address_formality: Mapped[AddressFormality] = mapped_column(Enum(AddressFormality, native_enum=False))
    language: Mapped[str] = mapped_column(String(10), default="de")
    standard_greeting: Mapped[str] = mapped_column(Text)
    outside_hours_greeting: Mapped[str] = mapped_column(Text)
    test_greeting: Mapped[str] = mapped_column(Text)
    farewell: Mapped[str] = mapped_column(Text)
    voice: Mapped[str] = mapped_column(String(50), default="marin")
    speech_speed: Mapped[float] = mapped_column(Float, default=1.0)
    pronunciation_instructions: Mapped[str] = mapped_column(Text, default="")
    pronunciation_style: Mapped[str] = mapped_column(String(50), default="neutral")
    regional_accent: Mapped[str] = mapped_column(String(50), default="")
    tone: Mapped[str] = mapped_column(String(100), default="friendly_professional")
    custom_style_instructions: Mapped[str] = mapped_column(Text, default="")
    response_length: Mapped[ResponseLength] = mapped_column(Enum(ResponseLength, native_enum=False))
    question_style: Mapped[str] = mapped_column(String(50), default="one_at_a_time")
    turn_detection_type: Mapped[TurnDetectionType] = mapped_column(Enum(TurnDetectionType, native_enum=False))
    turn_eagerness: Mapped[TurnEagerness] = mapped_column(Enum(TurnEagerness, native_enum=False))
    vad_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    prefix_padding_ms: Mapped[int] = mapped_column(Integer, default=300)
    silence_duration_ms: Mapped[int] = mapped_column(Integer, default=600)
    interruptions_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    idle_prompt_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    idle_timeout_ms: Mapped[int] = mapped_column(Integer, default=10000)
    primary_task: Mapped[str] = mapped_column(Text)
    off_topic_behavior: Mapped[str] = mapped_column(Text)
    off_topic_mode: Mapped[str] = mapped_column(String(50), default="brief_redirect")
    uncertainty_behavior: Mapped[str] = mapped_column(Text)
    uncertainty_modes: Mapped[list] = mapped_column(JSON, default=list)
    fallback_message: Mapped[str] = mapped_column(Text)
    simple_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_users.id"), nullable=True)


class AgentTopic(Base, TimestampMixin):
    __tablename__ = "agent_topics"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    label: Mapped[str] = mapped_column(String(150))
    topic_type: Mapped[str] = mapped_column(String(20), default="allowed")
    instructions: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AgentBehaviorRule(Base, TimestampMixin):
    __tablename__ = "agent_behavior_rules"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    rule_text: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AgentKnowledgeProfile(Base, TimestampMixin):
    __tablename__ = "agent_knowledge_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_agent_knowledge_profiles_tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    company_description: Mapped[str] = mapped_column(Text, default="")
    products: Mapped[str] = mapped_column(Text, default="")
    locations: Mapped[str] = mapped_column(Text, default="")
    important_notes: Mapped[str] = mapped_column(Text, default="")
    contact_phone: Mapped[str] = mapped_column(String(50), default="")
    contact_email: Mapped[str] = mapped_column(String(320), default="")
    website: Mapped[str] = mapped_column(String(500), default="")


class AgentFaq(Base, TimestampMixin):
    __tablename__ = "agent_faqs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AgentKnowledgeService(Base, TimestampMixin):
    __tablename__ = "agent_knowledge_services"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    price_information: Mapped[str] = mapped_column(String(150), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AgentBusinessHours(Base, TimestampMixin):
    __tablename__ = "agent_business_hours"
    __table_args__ = (UniqueConstraint("tenant_id", "weekday", name="uq_agent_business_hours_day"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    opens_at: Mapped[str] = mapped_column(String(5), default="09:00")
    closes_at: Mapped[str] = mapped_column(String(5), default="18:00")
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentCapability(Base, TimestampMixin):
    __tablename__ = "agent_capabilities"
    __table_args__ = (UniqueConstraint("tenant_id", "capability_key", name="uq_agent_capability"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    capability_key: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentConfigurationAudit(Base):
    __tablename__ = "agent_configuration_audits"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_users.id"), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CalendarConnection(Base, TimestampMixin):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_account_id", name="uq_calendar_connection_account"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_users.id"), index=True)
    provider: Mapped[CalendarProviderName] = mapped_column(Enum(CalendarProviderName, native_enum=False), index=True)
    provider_account_id: Mapped[str] = mapped_column(String(320))
    account_email: Mapped[str] = mapped_column(String(320), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_scopes: Mapped[list] = mapped_column(JSON, default=list)
    connection_status: Mapped[CalendarConnectionStatus] = mapped_column(
        Enum(CalendarConnectionStatus, native_enum=False), default=CalendarConnectionStatus.connected, index=True
    )
    last_successful_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarOAuthState(Base):
    __tablename__ = "calendar_oauth_states"
    __table_args__ = (UniqueConstraint("state_hash", name="uq_calendar_oauth_state_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_users.id"), index=True)
    provider: Mapped[CalendarProviderName] = mapped_column(Enum(CalendarProviderName, native_enum=False))
    state_hash: Mapped[str] = mapped_column(String(64))
    encrypted_code_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExternalCalendar(Base, TimestampMixin):
    __tablename__ = "external_calendars"
    __table_args__ = (
        UniqueConstraint("calendar_connection_id", "external_calendar_id", name="uq_external_calendar_provider_id"),
        Index("ix_external_calendars_tenant_availability", "tenant_id", "is_selected_for_availability"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    calendar_connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_connections.id"), index=True)
    external_calendar_id: Mapped[str] = mapped_column(String(1000))
    calendar_name: Mapped[str] = mapped_column(String(300))
    calendar_timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    access_role: Mapped[str] = mapped_column(String(50), default="reader")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selected_for_availability: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selected_for_booking: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookingConfiguration(Base, TimestampMixin):
    __tablename__ = "booking_configurations"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_booking_configuration_tenant"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Berlin")
    slot_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    minimum_notice_minutes: Mapped[int] = mapped_column(Integer, default=120)
    maximum_booking_horizon_days: Mapped[int] = mapped_column(Integer, default=60)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=0)
    maximum_suggestions_per_request: Mapped[int] = mapped_column(Integer, default=3)


class CalendarBusinessHour(Base, TimestampMixin):
    __tablename__ = "calendar_business_hours"
    __table_args__ = (
        UniqueConstraint("tenant_id", "weekday", "start_time", "end_time", name="uq_calendar_business_hour_window"),
        Index("ix_calendar_business_hours_tenant_weekday", "tenant_id", "weekday"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CalendarAppointmentType(Base, TimestampMixin):
    __tablename__ = "calendar_appointment_types"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_calendar_appointment_type_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    buffer_before_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buffer_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_type: Mapped[CalendarLocationType] = mapped_column(
        Enum(CalendarLocationType, native_enum=False), default=CalendarLocationType.phone
    )
    location_text: Mapped[str] = mapped_column(String(300), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    service: Mapped["Service"] = relationship()


class CalendarBooking(Base, TimestampMixin):
    __tablename__ = "calendar_bookings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_calendar_booking_idempotency"),
        Index("ix_calendar_bookings_tenant_start", "tenant_id", "start_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"), index=True)
    appointment_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_appointment_types.id"), index=True)
    calendar_connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_connections.id"), index=True)
    external_calendar_id: Mapped[str] = mapped_column(String(1000))
    external_event_id: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider: Mapped[CalendarProviderName] = mapped_column(Enum(CalendarProviderName, native_enum=False))
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone: Mapped[str] = mapped_column(String(50))
    customer_email: Mapped[str] = mapped_column(String(320), default="")
    customer_notes: Mapped[str] = mapped_column(Text, default="")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(100))
    status: Mapped[CalendarBookingStatus] = mapped_column(
        Enum(CalendarBookingStatus, native_enum=False), default=CalendarBookingStatus.pending, index=True
    )
    source: Mapped[CalendarBookingSource] = mapped_column(Enum(CalendarBookingSource, native_enum=False))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    conversation_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("call_sessions.id"), nullable=True, index=True
    )
    provider_response_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    service_name_snapshot: Mapped[str] = mapped_column(String(150))
    duration_minutes_snapshot: Mapped[int] = mapped_column(Integer)
    buffer_before_minutes_snapshot: Mapped[int] = mapped_column(Integer)
    buffer_after_minutes_snapshot: Mapped[int] = mapped_column(Integer)
    blocked_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    appointment_format_snapshot: Mapped[str] = mapped_column(String(30), default="phone")
    location_snapshot: Mapped[str] = mapped_column(String(300), default="")
    calendar_name_snapshot: Mapped[str] = mapped_column(String(300), default="")
    service: Mapped["Service"] = relationship()
    appointment_type: Mapped[CalendarAppointmentType] = relationship()


class Location(Base, TimestampMixin):
    __tablename__ = "locations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    street: Mapped[str] = mapped_column(String(200), default="")
    postal_code: Mapped[str] = mapped_column(String(20), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    country_code: Mapped[str] = mapped_column(String(2), default="DE")
    timezone: Mapped[str] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    tenant: Mapped[Tenant] = relationship(back_populates="locations")


class Service(Base, TimestampMixin):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_services_tenant_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StaffMember(Base, TimestampMixin):
    __tablename__ = "staff_members"
    __table_args__ = (UniqueConstraint("tenant_id", "display_name", name="uq_staff_tenant_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(150))
    role_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointments_tenant_starts", "tenant_id", "starts_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    staff_member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_members.id"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone: Mapped[str] = mapped_column(String(50), default="")
    customer_email: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus, native_enum=False))
    source: Mapped[AppointmentSource] = mapped_column(Enum(AppointmentSource, native_enum=False))
    service: Mapped[Service | None] = relationship()
    staff_member: Mapped[StaffMember | None] = relationship()


class CallSession(Base):
    __tablename__ = "call_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[CallChannel] = mapped_column(Enum(CallChannel, native_enum=False))
    status: Mapped[str] = mapped_column(String(50))
    configuration_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_manifest_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    applied_configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    configuration_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    configuration_status: Mapped[str] = mapped_column(String(50), default="pending")
    runtime_state: Mapped[str] = mapped_column(String(50), default="idle")
    bootstrap_status: Mapped[str] = mapped_column(String(50), default="not_started")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("call_session_id", "call_id", name="uq_tool_execution_session_call"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    continuation_mode: Mapped[str] = mapped_column(String(50), default="sdk_automatic")
    result_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    continuation_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    continuation_response_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    booking_state_before: Mapped[str | None] = mapped_column(String(50), nullable=True)
    booking_state_after: Mapped[str | None] = mapped_column(String(50), nullable=True)
    runtime_state_before: Mapped[str | None] = mapped_column(String(50), nullable=True)
    runtime_state_after: Mapped[str | None] = mapped_column(String(50), nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookingConversation(Base, TimestampMixin):
    __tablename__ = "booking_conversations"
    __table_args__ = (UniqueConstraint("call_session_id", name="uq_booking_conversation_session"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    state: Mapped[BookingState] = mapped_column(
        Enum(BookingState, native_enum=False), default=BookingState.idle, index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    appointment_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calendar_appointment_types.id"), nullable=True
    )
    requested_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    datetime_resolution_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    datetime_resolution_version: Mapped[int] = mapped_column(Integer, default=0)
    datetime_explicit_year: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_slot_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_slot_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Berlin")
    customer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    booking_confirmed_by_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_version: Mapped[int] = mapped_column(Integer, default=0)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calendar_bookings.id"), nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AvailabilitySnapshot(Base):
    __tablename__ = "availability_snapshots"
    __table_args__ = (UniqueConstraint("call_session_id", name="uq_availability_snapshot_session"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    calendar_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calendar_connections.id"), nullable=True
    )
    external_calendar_id: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100))
    horizon_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    catalog: Mapped[list] = mapped_column(JSON, default=list)
    business_hours: Mapped[list] = mapped_column(JSON, default=list)
    calendar_ids: Mapped[list] = mapped_column(JSON, default=list)
    availability_status: Mapped[str] = mapped_column(String(50), default="ready")
    busy_intervals: Mapped[list] = mapped_column(JSON, default=list)
    local_appointment_intervals: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

