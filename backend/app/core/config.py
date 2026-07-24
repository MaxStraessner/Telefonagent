from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Telefonagent"
    backend_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://telefonagent:telefonagent@database:5432/telefonagent"
    migration_database_url: str | None = None
    frontend_url: str = "http://localhost:5173"
    app_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:5173"
    auth_hmac_secret: str = "development-only-change-this-auth-secret"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    session_touch_interval_seconds: int = 300
    auth_rate_limit_window_minutes: int = 15
    auth_username_failure_limit: int = 5
    auth_ip_failure_limit: int = 30
    dev_bootstrap_enabled: bool = False
    dev_bootstrap_username: str = "owner@telefonagent.local"
    dev_bootstrap_password: str | None = None
    allow_development_tenant_fallback: bool = False
    development_tenant_slug: str = "salon-haarkunst-test"
    log_level: str = "INFO"
    openai_api_key: str | None = None
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_voice: str = "marin"
    openai_realtime_max_session_minutes: int = 10
    openai_realtime_max_output_tokens: int = 1024
    openai_realtime_transcription_enabled: bool = True
    openai_realtime_log_raw_events: bool = False
    openai_safety_identifier_salt: str = "telefonagent-local-installation"
    telephony_configured: bool = False
    calendar_configured: bool = False
    calendar_token_encryption_key: str | None = None
    google_calendar_client_id: str | None = None
    google_calendar_client_secret: str | None = None
    google_calendar_redirect_uri: str | None = None
    microsoft_calendar_client_id: str | None = None
    microsoft_calendar_client_secret: str | None = None
    microsoft_calendar_redirect_uri: str | None = None
    microsoft_calendar_tenant: str = "common"
    availability_snapshot_horizon_days: int = 14
    availability_snapshot_ttl_seconds: int = 120
    calendar_provider_timeout_seconds: float = 8.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def session_cookie_name(self) -> str:
        return "__Host-telefonagent_session" if self.is_production else "telefonagent_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-telefonagent_csrf" if self.is_production else "telefonagent_csrf"

    @property
    def allowed_request_origins(self) -> set[str]:
        return {origin.rstrip("/") for origin in self.cors_origin_list}

    @property
    def google_calendar_configured(self) -> bool:
        return bool(
            self.calendar_token_encryption_key
            and self.google_calendar_client_id
            and self.google_calendar_client_secret
            and self.google_calendar_redirect_uri
        )

    @property
    def microsoft_calendar_configured(self) -> bool:
        return bool(
            self.calendar_token_encryption_key
            and self.microsoft_calendar_client_id
            and self.microsoft_calendar_client_secret
            and self.microsoft_calendar_redirect_uri
        )

    @property
    def any_calendar_provider_configured(self) -> bool:
        return self.google_calendar_configured or self.microsoft_calendar_configured

    @field_validator(
        "migration_database_url",
        "calendar_token_encryption_key",
        "google_calendar_client_id",
        "google_calendar_client_secret",
        "google_calendar_redirect_uri",
        "microsoft_calendar_client_id",
        "microsoft_calendar_client_secret",
        "microsoft_calendar_redirect_uri",
        mode="before",
    )
    @classmethod
    def empty_calendar_values_are_none(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("openai_realtime_max_session_minutes", mode="before")
    @classmethod
    def validate_max_session_minutes(cls, value: object) -> int:
        try:
            minutes = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 10
        return minutes if 1 <= minutes <= 60 else 10

    @field_validator("openai_realtime_max_output_tokens", mode="before")
    @classmethod
    def validate_max_output_tokens(cls, value: object) -> int:
        try:
            tokens = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1024
        return tokens if 256 <= tokens <= 4096 else 1024

    @field_validator("openai_realtime_model", mode="before")
    @classmethod
    def validate_realtime_model(cls, value: object) -> str:
        model = str(value or "").strip()
        return model or "gpt-realtime-2.1"

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def validate_openai_api_key(cls, value: object) -> str | None:
        if value is None:
            return None
        key = str(value).strip()
        return key or None

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

    @field_validator("dev_bootstrap_password", mode="before")
    @classmethod
    def empty_bootstrap_password_is_none(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value)
        return normalized if normalized else None

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.is_production:
            if self.migration_database_url == self.database_url:
                raise ValueError(
                    "DATABASE_URL und MIGRATION_DATABASE_URL dürfen in "
                    "Produktion nicht dieselbe Rolle verwenden."
                )
            if len(self.auth_hmac_secret.encode("utf-8")) < 32:
                raise ValueError("AUTH_HMAC_SECRET muss in Produktion mindestens 32 Bytes lang sein.")
            if self.auth_hmac_secret == "development-only-change-this-auth-secret":
                raise ValueError("Der Entwicklungswert für AUTH_HMAC_SECRET ist in Produktion unzulässig.")
            if self.allow_development_tenant_fallback:
                raise ValueError("Der Entwicklungs-Tenant-Fallback ist in Produktion unzulässig.")
            if not self.app_base_url.lower().startswith("https://"):
                raise ValueError("APP_BASE_URL muss in Produktion HTTPS verwenden.")
            if any(not origin.lower().startswith("https://") for origin in self.cors_origin_list):
                raise ValueError("Alle CORS_ORIGINS müssen in Produktion HTTPS verwenden.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
