from datetime import datetime
from typing import Literal
from uuid import UUID

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


class ChangePasswordRequest(StrictWriteModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password_policy(cls, value: str) -> str:
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
    role: Literal["owner", "admin", "employee"]
    is_platform_admin: bool


class AuthMeResponse(BaseModel):
    user: AuthUser
    tenant: AuthTenant


class SessionResponse(AuthMeResponse):
    idle_expires_at: datetime
    absolute_expires_at: datetime


class LoginResponse(SessionResponse):
    pass
