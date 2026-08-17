from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TwilioSyncStatus = Literal["pending", "synced", "blocked", "error"]


class TwilioNumberResponse(BaseModel):
    sid: str
    phone_number: str
    friendly_name: str
    voice_capable: bool
    assigned_company_id: UUID | None = None
    assigned_company_name: str | None = None
    routing_status: TwilioSyncStatus | Literal["available"]


class CompanyTelephonyResponse(BaseModel):
    provider: Literal["twilio"] | None = None
    phone_number: str | None = None
    phone_number_sid: str | None = None
    sync_status: TwilioSyncStatus | None = None
    expected_voice_url: str
    provider_synced_url: str | None = None
    provider_synced_at: datetime | None = None
    error_code: str | None = None


class TwilioAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str = Field(min_length=8, max_length=40)
    transfer: bool = False
