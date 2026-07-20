from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Telefonagent"
    backend_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://telefonagent:telefonagent@database:5432/telefonagent"
    active_tenant_slug: str = "salon-haarkunst-test"
    frontend_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    openai_api_key: str | None = None
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_voice: str = "marin"
    openai_realtime_max_session_minutes: int = 10
    openai_realtime_transcription_enabled: bool = True
    openai_realtime_log_raw_events: bool = False
    openai_safety_identifier_salt: str = "telefonagent-local-installation"
    telephony_configured: bool = False
    calendar_configured: bool = False

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @field_validator("openai_realtime_max_session_minutes", mode="before")
    @classmethod
    def validate_max_session_minutes(cls, value: object) -> int:
        try:
            minutes = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 10
        return minutes if 1 <= minutes <= 60 else 10

    @field_validator("openai_realtime_model", mode="before")
    @classmethod
    def validate_realtime_model(cls, value: object) -> str:
        model = str(value or "").strip()
        return model or "gpt-realtime-2.1"

    @field_validator("openai_realtime_voice", mode="before")
    @classmethod
    def validate_realtime_voice(cls, value: object) -> str:
        voice = str(value or "").strip()
        return voice or "marin"

    @field_validator("openai_realtime_transcription_enabled", mode="before")
    @classmethod
    def validate_transcription_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return True

    @field_validator("openai_realtime_log_raw_events", mode="before")
    @classmethod
    def validate_raw_event_logging(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return False

    @field_validator("openai_safety_identifier_salt", mode="before")
    @classmethod
    def validate_safety_identifier_salt(cls, value: object) -> str:
        salt = str(value or "").strip()
        return salt or "telefonagent-local-installation"


@lru_cache
def get_settings() -> Settings:
    return Settings()
