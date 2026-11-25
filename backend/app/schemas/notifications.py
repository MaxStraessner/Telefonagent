from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.agent_config import AgentConfigurationData

MailSection = Literal["caller", "time", "summary", "concern", "tasks", "transcript", "cost"]
DEFAULT_SECTIONS = list(get_args(MailSection))


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    recipient: str | None = Field(max_length=254)
    sections: list[MailSection] = Field(min_length=1, max_length=7)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str | None) -> str | None:
        return AgentConfigurationData.validate_recipient(value)


class NotificationSettingsResponse(NotificationSettingsUpdate):
    available: bool
    connected: bool
    active_elsewhere: bool
