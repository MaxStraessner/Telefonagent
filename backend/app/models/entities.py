import enum
import uuid
from datetime import datetime

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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

