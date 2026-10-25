from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CallInboxStatus = Literal["open", "done"]
CallPriority = Literal["normal", "important", "urgent"]
CallCategory = Literal[
    "appointment",
    "callback",
    "price_request",
    "general_question",
    "complaint",
    "new_customer",
    "existing_customer",
    "other",
]
CallAnalysisStatus = Literal[
    "not_requested", "pending", "processing", "completed", "failed"
]


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["agent", "customer"]
    text: str = Field(min_length=1, max_length=20_000)
    segment_id: str | None = None
    event_id: str | None = None
    previous_item_id: str | None = None
    state: Literal["provisional", "confirmed", "interrupted"] = "confirmed"
    received_at: datetime | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.split())


class CallAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller_name: str | None = Field(default=None, max_length=150)
    category: CallCategory
    summary: str = Field(max_length=4_000)
    result: str = Field(max_length=4_000)
    action_required: bool
    action_items: list[str] = Field(max_length=20)
    priority: CallPriority
    main_concern: str | None = Field(default=None, max_length=1000)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=320)
    callback_required: bool | None = None
    appointment_request: str | None = Field(default=None, max_length=1000)

    @field_validator("caller_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("summary", "result")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("action_items")
    @classmethod
    def normalize_action_items(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split())[:500] for value in values]
        return [value for value in normalized if value]


class CallListItem(BaseModel):
    id: UUID
    started_at: datetime
    caller_name: str | None
    caller_phone: str | None
    category: CallCategory | None
    result: str | None
    action_required: bool
    priority: CallPriority
    status: CallInboxStatus
    duration_seconds: int | None
    analysis_status: CallAnalysisStatus
    operating_mode: str | None = None
    voice_model: str | None = None
    call_status: str = "unknown"
    callback_required: bool | None = None
    documentation_status: str = "legacy"


class CallDetail(CallListItem):
    ended_at: datetime | None
    external_call_id: str | None
    phone_number_id: UUID | None
    transcript: list[TranscriptTurn]
    summary: str | None
    action_items: list[str]
    analysis_details: dict | None = None
    analysis_error_code: str | None = None
    error_code: str | None = None
    actions: list[dict] = Field(default_factory=list)
    appointments: list[dict] = Field(default_factory=list)
    content_deleted_at: datetime | None = None


class CallStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CallInboxStatus


class CallAnalysisRetryResponse(BaseModel):
    id: UUID
    analysis_status: CallAnalysisStatus
