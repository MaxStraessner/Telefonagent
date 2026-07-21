from functools import lru_cache
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
    openai_realtime_model: str = "gpt-realtime"
    telephony_configured: bool = False
    calendar_configured: bool = False

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
