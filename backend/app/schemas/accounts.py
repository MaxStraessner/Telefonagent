from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import normalize_username, validate_new_password

CompanyStatusValue = Literal["trial", "active", "suspended", "archived"]
CompanyRoleValue = Literal["company_admin", "company_user"]


class StrictAccountWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirstCompanyAdmin(StrictAccountWrite):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    delivery: Literal["invitation", "temporary_password"] = "invitation"
    temporary_password: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not normalize_username(value):
            raise ValueError("Benutzername darf nicht leer sein.")
        return value.strip()

    @field_validator("display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Dieses Feld darf nicht leer sein.")
        return value

    @model_validator(mode="after")
    def validate_delivery(self):
        if self.delivery == "temporary_password":
            if not self.temporary_password:
                raise ValueError("Für ein Startpasswort ist ein Passwort erforderlich.")
            self.temporary_password = validate_new_password(self.temporary_password)
        else:
            if self.temporary_password is not None:
                raise ValueError("Einladungen dürfen kein Startpasswort enthalten.")
            if not self.email:
                raise ValueError("Für eine Einladung ist eine E-Mail-Adresse erforderlich.")
        self.email = str(self.email).strip() if self.email else None
        return self


class CompanyCreate(StrictAccountWrite):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    industry: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="Europe/Berlin", max_length=64)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)
    status: Literal["trial", "active"] = "trial"
    is_demo: bool = False
    first_admin: FirstCompanyAdmin

    @field_validator("name", "industry")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Dieses Feld darf nicht leer sein.")
        return value

    @field_validator("legal_name", "contact_name", "contact_email", "contact_phone", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Die Zeitzone ist ungültig.") from exc
        return value


class CompanyUpdate(StrictAccountWrite):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    industry: str = Field(min_length=1, max_length=100)
    timezone: str = Field(max_length=64)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)
    is_demo: bool

    _required = field_validator("name", "industry")(CompanyCreate.required_text.__func__)
    _optional = field_validator(
        "legal_name", "contact_name", "contact_email", "contact_phone", mode="before"
    )(CompanyCreate.optional_text.__func__)
    _timezone = field_validator("timezone")(CompanyCreate.valid_timezone.__func__)


class CompanyOperationalUpdate(StrictAccountWrite):
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)
    timezone: str = Field(max_length=64)

    _optional = field_validator(
        "contact_name", "contact_email", "contact_phone", mode="before"
    )(CompanyCreate.optional_text.__func__)
    _timezone = field_validator("timezone")(CompanyCreate.valid_timezone.__func__)


class CompanyStatusUpdate(StrictAccountWrite):
    status: CompanyStatusValue


class CompanySummary(BaseModel):
    id: UUID
    slug: str
    name: str
    legal_name: str | None
    status: CompanyStatusValue
    is_demo: bool
    active_user_count: int
    has_primary_admin: bool
    onboarding_complete: bool
    created_at: datetime


class CompanyDetail(CompanySummary):
    industry: str
    timezone: str
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    default_language: str


class CompanyUserInvite(StrictAccountWrite):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=320)
    role: CompanyRoleValue = "company_user"

    _username = field_validator("username")(FirstCompanyAdmin.validate_username.__func__)
    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email")(FirstCompanyAdmin.validate_required_text.__func__)


class CompanyUserCreate(StrictAccountWrite):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    role: CompanyRoleValue = "company_user"
    password: str = Field(min_length=1, max_length=128)

    _username = field_validator("username")(FirstCompanyAdmin.validate_username.__func__)
    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email", mode="before")(CompanyCreate.optional_text.__func__)
    _password = field_validator("password")(validate_new_password)


class CompanyUserUpdate(StrictAccountWrite):
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    role: CompanyRoleValue
    is_active: bool

    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email", mode="before")(CompanyCreate.optional_text.__func__)


class CompanyUserResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    email: str | None
    role: CompanyRoleValue
    is_active: bool
    is_primary_admin: bool
    must_change_password: bool
    last_login_at: datetime | None


class PrimaryAdminTransfer(StrictAccountWrite):
    user_id: UUID


class InvitationResponse(BaseModel):
    id: UUID
    email: str
    username: str
    display_name: str
    role: Literal["company_admin", "company_user", "admin"]
    expires_at: datetime
    status: Literal["pending", "sent", "accepted", "revoked", "expired", "failed"]
    created_at: datetime


class PlatformAdminInvite(StrictAccountWrite):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=320)
    current_password: str = Field(min_length=1, max_length=128)

    _username = field_validator("username")(FirstCompanyAdmin.validate_username.__func__)
    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email")(FirstCompanyAdmin.validate_required_text.__func__)


class PlatformAdminCreate(StrictAccountWrite):
    username: str = Field(min_length=1, max_length=150)
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    current_password: str = Field(min_length=1, max_length=128)

    _username = field_validator("username")(FirstCompanyAdmin.validate_username.__func__)
    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email", mode="before")(CompanyCreate.optional_text.__func__)
    _password = field_validator("password")(validate_new_password)


class PlatformAdminUpdate(StrictAccountWrite):
    display_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=320)
    is_active: bool
    current_password: str = Field(min_length=1, max_length=128)

    _display = field_validator("display_name")(FirstCompanyAdmin.validate_required_text.__func__)
    _email = field_validator("email", mode="before")(CompanyCreate.optional_text.__func__)


class PlatformAdminResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    email: str | None
    platform_role: Literal["owner", "admin"]
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None


class AuditLogResponse(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    tenant_id: UUID | None
    platform_role: str | None
    action: str
    target_type: str
    target_id: str | None
    outcome: str
    metadata_before: dict | None
    metadata_after: dict | None
    request_id: str | None
    created_at: datetime


class PlatformDashboardResponse(BaseModel):
    companies_total: int
    companies_trial: int
    companies_active: int
    companies_suspended: int
    companies_archived: int
    active_company_users: int
    pending_invitations: int
