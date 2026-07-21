from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VoiceName = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]


class AgentListItem(BaseModel):
    id: UUID | None = None
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=500)


class AgentTopicData(AgentListItem):
    label: str = Field(min_length=1, max_length=150)
    instructions: str = Field(default="", max_length=1000)
    topic_type: Literal["allowed", "forbidden"] = "allowed"


class AgentRuleData(AgentListItem):
    rule_text: str = Field(min_length=1, max_length=1000)


class AgentConfigurationData(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    assistant_name: str = Field(min_length=1, max_length=100)
    assistant_role: str = Field(min_length=1, max_length=200)
    transparency_notice: str = Field(min_length=1, max_length=500)
    address_formality: Literal["formal", "informal"]
    language: Literal["de"] = "de"
    standard_greeting: str = Field(min_length=1, max_length=700)
    outside_hours_greeting: str = Field(min_length=1, max_length=700)
    test_greeting: str = Field(min_length=1, max_length=700)
    farewell: str = Field(min_length=1, max_length=500)
    voice: VoiceName
    speech_speed: float = Field(ge=0.25, le=1.5)
    pronunciation_instructions: str = Field(default="", max_length=1000)
    pronunciation_style: Literal["neutral", "regional", "custom"]
    regional_accent: Literal["", "north_german", "westphalian", "rhineland", "south_german"] = ""
    tone: Literal["professional_binding", "friendly_service", "calm_empathic", "relaxed_personal", "concise_factual", "custom"]
    custom_style_instructions: str = Field(default="", max_length=1000)
    response_length: Literal["very_short", "short", "balanced", "detailed"]
    question_style: Literal["one_at_a_time", "natural"]
    turn_detection_type: Literal["server_vad", "semantic_vad"]
    turn_eagerness: Literal["low", "medium", "high"]
    vad_threshold: float = Field(ge=0.0, le=1.0)
    prefix_padding_ms: int = Field(ge=0, le=2000)
    silence_duration_ms: int = Field(ge=200, le=3000)
    interruptions_enabled: bool
    idle_prompt_enabled: bool
    idle_timeout_ms: int = Field(ge=5000, le=30000)
    primary_task: str = Field(min_length=1, max_length=1500)
    off_topic_behavior: str = Field(min_length=1, max_length=1000)
    off_topic_mode: Literal["strict", "brief_redirect", "limited_smalltalk"]
    uncertainty_behavior: str = Field(min_length=1, max_length=1000)
    uncertainty_modes: list[Literal["acknowledge", "ask_clarifying", "offer_contact"]] = Field(min_length=1, max_length=3)
    fallback_message: str = Field(min_length=1, max_length=700)
    simple_mode: bool
    topics: list[AgentTopicData] = Field(default_factory=list, max_length=30)
    custom_rules: list[AgentRuleData] = Field(default_factory=list, max_length=30)

    @field_validator(
        "company_name", "assistant_name", "assistant_role", "transparency_notice",
        "standard_greeting", "outside_hours_greeting", "test_greeting", "farewell",
        "primary_task", "off_topic_behavior", "uncertainty_behavior", "fallback_message",
    )
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Dieses Feld darf nicht leer sein.")
        return value

    @model_validator(mode="after")
    def validate_conditional_style_fields(self):
        if self.pronunciation_style == "regional" and not self.regional_accent:
            raise ValueError("Für eine regionale Färbung muss eine Region ausgewählt werden.")
        if self.pronunciation_style == "custom" and not self.pronunciation_instructions.strip():
            raise ValueError("Für individuelle Aussprache ist eine Anweisung erforderlich.")
        if self.tone == "custom" and not self.custom_style_instructions.strip():
            raise ValueError("Für einen individuellen Gesprächsstil ist eine Anweisung erforderlich.")
        return self


class AgentConfigurationUpdate(AgentConfigurationData):
    expected_version: int = Field(ge=1)


class AgentConfigurationResponse(AgentConfigurationData):
    tenant_id: UUID
    version: int
    updated_at: datetime
    can_edit: bool
    role: Literal["owner", "admin", "member"]
    model_config = ConfigDict(from_attributes=True)


class KnowledgeProfileData(BaseModel):
    company_description: str = Field(default="", max_length=4000)
    products: str = Field(default="", max_length=4000)
    locations: str = Field(default="", max_length=4000)
    important_notes: str = Field(default="", max_length=4000)
    contact_phone: str = Field(default="", max_length=50)
    contact_email: str = Field(default="", max_length=320)
    website: str = Field(default="", max_length=500)


class FaqData(AgentListItem):
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=1500)


class KnowledgeServiceData(AgentListItem):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1200)
    price_information: str = Field(default="", max_length=150)


class BusinessHoursData(BaseModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closes_at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    is_closed: bool = False

    @model_validator(mode="after")
    def validate_order(self):
        if not self.is_closed and self.opens_at >= self.closes_at:
            raise ValueError("Die Öffnungszeit muss vor der Schließzeit liegen.")
        return self


class AgentKnowledgeData(BaseModel):
    profile: KnowledgeProfileData
    faqs: list[FaqData] = Field(default_factory=list, max_length=50)
    services: list[KnowledgeServiceData] = Field(default_factory=list, max_length=50)
    business_hours: list[BusinessHoursData] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def unique_weekdays(self):
        weekdays = [item.weekday for item in self.business_hours]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("Jeder Wochentag darf nur einmal vorkommen.")
        text_size = sum(len(str(value)) for value in self.profile.model_dump().values())
        text_size += sum(len(item.question) + len(item.answer) for item in self.faqs)
        text_size += sum(len(item.name) + len(item.description) + len(item.price_information) for item in self.services)
        if text_size > 12_000:
            raise ValueError("Das aktive Unternehmenswissen darf insgesamt höchstens 12.000 Zeichen enthalten.")
        return self


class AgentKnowledgeUpdate(AgentKnowledgeData):
    expected_version: int = Field(ge=1)


class AgentKnowledgeResponse(AgentKnowledgeData):
    tenant_id: UUID
    version: int
    can_edit: bool


class CapabilityResponse(BaseModel):
    key: str
    label: str
    description: str
    available: bool
    active: bool
    unavailable_reason: str | None = None


class VoiceOptionResponse(BaseModel):
    value: VoiceName
    label: str
    recommended: bool = False


class AgentCatalogResponse(BaseModel):
    voices: list[VoiceOptionResponse]
    capabilities: list[CapabilityResponse]


class VoicePreviewRequest(BaseModel):
    voice: VoiceName
    speed: float = Field(ge=0.25, le=1.5)
    text: str = Field(min_length=1, max_length=300)
    pronunciation_style: Literal["neutral", "regional", "custom"] = "neutral"
    regional_accent: Literal["", "north_german", "westphalian", "rhineland", "south_german"] = ""
    pronunciation_instructions: str = Field(default="", max_length=1000)


class RuntimeSummaryResponse(BaseModel):
    tenant_id: UUID
    configuration_version: int
    company_name: str
    assistant_name: str
    language: str
    style: str
    business_hours_status: Literal["open", "closed"]
    model: str
    voice: str
    speed: float
    turn_detection: dict[str, object]
    capability_keys: list[str]
    tool_names: list[str]
    greeting: str
    prompt_sections: list[str]


class PromptPreviewResponse(BaseModel):
    configuration_version: int
    prompt: str
    sections: list[str]
