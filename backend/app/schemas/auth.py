from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import normalize_username, validate_new_password


class StrictWriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictWriteModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def username_must_not_be_blank(cls, value: str) -> str:
        if not normalize_username(value):
            raise ValueError("Benutzername darf nicht leer sein.")
        return value


class InitialSetupRequest(StrictWriteModel):
    setup_code: str = Field(min_length=1, max_length=256)
    company_name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=150)
    username: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    password: str

    @field_validator("company_name", "industry", "display_name")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Dieses Feld darf nicht leer sein.")
        return normalized

    @field_validator("username")
    @classmethod
    def initial_setup_username_must_not_be_blank(cls, value: str) -> str:
        if not normalize_username(value):
            raise ValueError("Benutzername darf nicht leer sein.")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_optional_email(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Die Zeitzone ist ungültig.") from exc
        return value

    @field_validator("password")
    @classmethod
    def initial_setup_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class ChangePasswordRequest(StrictWriteModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class ContextSelectionRequest(StrictWriteModel):
    company_id: UUID


class ForgotPasswordRequest(StrictWriteModel):
    identifier: str = Field(min_length=1, max_length=320)


class ResetPasswordRequest(StrictWriteModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def reset_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class InvitationAcceptRequest(StrictWriteModel):
    password: str

    @field_validator("password")
    @classmethod
    def invitation_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class InvitationPreviewResponse(BaseModel):
    email: str
    display_name: str
    company_name: str | None
    role: Literal["company_admin", "company_user", "admin"]
    expires_at: datetime


class ManagedUserWrite(StrictWriteModel):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    role: Literal["company_admin", "company_user", "admin", "employee"]
    password: str

    @field_validator("username")
    @classmethod
    def managed_username_must_not_be_blank(cls, value: str) -> str:
        if not normalize_username(value):
            raise ValueError("Benutzername darf nicht leer sein.")
        return value

    @field_validator("display_name")
    @classmethod
    def managed_display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name darf nicht leer sein.")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def managed_optional_email(cls, value: object) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("password")
    @classmethod
    def managed_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class ManagedUserUpdate(StrictWriteModel):
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    role: Literal["company_admin", "company_user", "admin", "employee"]
    is_active: bool

    @field_validator("display_name")
    @classmethod
    def updated_display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name darf nicht leer sein.")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def updated_optional_email(cls, value: object) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class ManagedUserPasswordReset(StrictWriteModel):
    password: str

    @field_validator("password")
    @classmethod
    def reset_password_policy(cls, value: str) -> str:
        return validate_new_password(value)


class AuthTenant(BaseModel):
    id: UUID
    slug: str
    name: str


class AuthUser(BaseModel):
    id: UUID
    username: str
    email: str | None
    display_name: str
    role: Literal["company_admin", "company_user"] | None
    platform_role: Literal["owner", "admin"] | None
    is_platform_admin: bool
    must_change_password: bool


class AuthMembership(BaseModel):
    tenant_id: UUID
    role: Literal["company_admin", "company_user"]
    is_primary_admin: bool


class ManagedUser(AuthUser):
    is_active: bool


class AuthMeResponse(BaseModel):
    user: AuthUser
    tenant: AuthTenant | None
    active_company: AuthTenant | None
    membership: AuthMembership | None
    permissions: list[str]
    mode: Literal["platform", "company"]


class SessionResponse(AuthMeResponse):
    idle_expires_at: datetime
    absolute_expires_at: datetime


class LoginResponse(SessionResponse):
    pass


class InitialSetupStatusResponse(BaseModel):
    available: bool


class InitialSetupResponse(SessionResponse):
    pass
