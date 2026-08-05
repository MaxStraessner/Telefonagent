from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password, normalize_username
from app.models import (
    AddressFormality,
    AgentConfiguration,
    AgentKnowledgeProfile,
    AppUser,
    InitialAppSetup,
    ResponseLength,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantSettings,
    TenantStatus,
    TurnDetectionType,
    TurnEagerness,
)
from app.repositories.auth import AuthRepository


class ProvisioningConflictError(Exception):
    pass


class ProvisioningNotFoundError(Exception):
    pass


class ProvisioningService:
    def __init__(self, db: Session):
        self.db = db

    def provision_tenant(
        self,
        *,
        slug: str,
        name: str,
        industry: str,
        timezone_name: str,
        username: str,
        display_name: str,
        email: str | None,
        password: str,
        commit: bool = True,
        mark_initial_setup: bool = True,
    ) -> tuple[Tenant, AppUser]:
        normalized = normalize_username(username)
        if not normalized or len(username) > 150 or len(normalized) > 150:
            raise ValueError("Der Benutzername muss 1 bis 150 Zeichen lang sein.")
        tenant = self.db.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is not None and (
            tenant.name != name
            or tenant.industry != industry
            or tenant.timezone != timezone_name
            or tenant.status != TenantStatus.active
        ):
            raise ProvisioningConflictError(
                "Der Tenant-Slug existiert bereits mit abweichenden Stammdaten."
            )
        if tenant is None:
            tenant = Tenant(
                id=uuid4(),
                slug=slug,
                name=name,
                industry=industry,
                timezone=timezone_name,
                status=TenantStatus.active,
            )
            self._set_tenant_context(tenant.id)
            self.db.add(tenant)
            self.db.flush()
            self.db.add(
                TenantSettings(
                    tenant_id=tenant.id,
                    assistant_name="Telefonassistent",
                    default_language="de",
                    welcome_message=f"Guten Tag, Sie sprechen mit {name}. Wie kann ich Ihnen helfen?",
                    presentation_mode_enabled=False,
                    diagnostics_enabled=True,
                )
            )

        user = self.db.scalar(
            select(AppUser).where(AppUser.normalized_username == normalized)
        )
        if user is not None and (
            user.username != username
            or user.email != email
            or user.display_name != display_name
            or not user.is_active
            or user.is_platform_admin
        ):
            raise ProvisioningConflictError(
                "Der Benutzername existiert bereits mit abweichenden Stammdaten."
            )
        if user is None:
            user = AppUser(
                username=username,
                normalized_username=normalized,
                password_hash=hash_password(password),
                email=email,
                display_name=display_name,
                is_active=True,
                is_platform_admin=False,
                password_changed_at=datetime.now(timezone.utc),
            )
            self.db.add(user)
            self.db.flush()

        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        if membership is None:
            other_membership = self.db.scalar(
                select(TenantMembership).where(
                    TenantMembership.user_id == user.id,
                    TenantMembership.tenant_id != tenant.id,
                    TenantMembership.is_active.is_(True),
                )
            )
            if other_membership is not None:
                raise ProvisioningConflictError(
                    "Der Benutzer besitzt bereits eine andere aktive "
                    "Tenant-Mitgliedschaft."
                )
            self.db.add(
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role=TenantRole.owner,
                    is_active=True,
                )
            )
        elif membership.role != TenantRole.owner or not membership.is_active:
            raise ProvisioningConflictError(
                "Die vorhandene Mitgliedschaft besitzt nicht die Owner-Rolle."
            )
        self._ensure_tenant_baseline(tenant, user)
        if mark_initial_setup:
            self.mark_initial_setup_completed(tenant, user)
        if commit:
            self.db.commit()
        return tenant, user

    def create_platform_admin(
        self,
        *,
        username: str,
        display_name: str,
        email: str | None,
        password: str,
    ) -> AppUser:
        normalized = normalize_username(username)
        if not normalized or len(username) > 150 or len(normalized) > 150:
            raise ValueError("Der Benutzername muss 1 bis 150 Zeichen lang sein.")
        user = self.db.scalar(
            select(AppUser).where(AppUser.normalized_username == normalized)
        )
        if user is not None:
            if (
                not user.is_platform_admin
                or user.email != email
                or user.display_name != display_name
            ):
                raise ProvisioningConflictError(
                    "Der Benutzername existiert bereits mit abweichender Berechtigung "
                    "oder abweichenden Stammdaten."
                )
            return user
        user = AppUser(
            username=username,
            normalized_username=normalized,
            password_hash=hash_password(password),
            email=email,
            display_name=display_name,
            is_active=True,
            is_platform_admin=True,
            password_changed_at=datetime.now(timezone.utc),
        )
        self.db.add(user)
        self.db.commit()
        return user

    def set_password(self, username: str, password: str) -> AppUser:
        user = self._user(username)
        user.password_hash = hash_password(password)
        user.password_changed_at = datetime.now(timezone.utc)
        AuthRepository(self.db).revoke_user_sessions(user.id, "password_changed_by_operator")
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.is_active.is_(True),
                TenantMembership.role.in_([TenantRole.owner, TenantRole.admin]),
            )
        )
        if membership is not None:
            tenant = self.db.get(Tenant, membership.tenant_id)
            if tenant is not None:
                self.mark_initial_setup_completed(tenant, user)
        self.db.commit()
        return user

    def deactivate_user(self, username: str) -> AppUser:
        user = self._user(username)
        user.is_active = False
        AuthRepository(self.db).revoke_user_sessions(user.id, "user_deactivated")
        self.db.commit()
        return user

    def deactivate_tenant(self, slug: str) -> Tenant:
        tenant = self.db.scalar(select(Tenant).where(Tenant.slug == slug))
        if tenant is None:
            raise ProvisioningNotFoundError("Tenant wurde nicht gefunden.")
        tenant.status = TenantStatus.inactive
        AuthRepository(self.db).revoke_tenant_sessions(
            tenant.id, "tenant_deactivated"
        )
        self.db.commit()
        return tenant

    def _user(self, username: str) -> AppUser:
        user = self.db.scalar(
            select(AppUser).where(
                AppUser.normalized_username == normalize_username(username)
            )
        )
        if user is None:
            raise ProvisioningNotFoundError("Benutzer wurde nicht gefunden.")
        return user

    def mark_initial_setup_completed(self, tenant: Tenant, user: AppUser) -> None:
        state = self.db.get(InitialAppSetup, 1)
        if state is not None and state.completed_at is None:
            state.completed_at = datetime.now(timezone.utc)
            state.tenant_id = tenant.id
            state.user_id = user.id

    def _ensure_tenant_baseline(self, tenant: Tenant, user: AppUser) -> None:
        settings = self.db.scalar(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        if settings is None:
            settings = TenantSettings(
                tenant_id=tenant.id,
                assistant_name="Telefonassistent",
                default_language="de",
                welcome_message=(
                    f"Guten Tag, Sie sprechen mit dem digitalen Terminassistenten "
                    f"von {tenant.name}. Wie kann ich Ihnen helfen?"
                ),
                presentation_mode_enabled=False,
                diagnostics_enabled=True,
            )
            self.db.add(settings)
        configuration = self.db.scalar(
            select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant.id)
        )
        if configuration is None:
            welcome = settings.welcome_message
            self.db.add(
                AgentConfiguration(
                    tenant_id=tenant.id,
                    company_name=tenant.name,
                    assistant_name=settings.assistant_name,
                    assistant_role="digitaler Terminassistent",
                    transparency_notice="Ich bin ein KI-gestützter Sprachassistent.",
                    address_formality=AddressFormality.formal,
                    language="de",
                    standard_greeting=welcome,
                    outside_hours_greeting="Guten Tag. Wie kann ich Ihnen weiterhelfen?",
                    test_greeting="Willkommen zum Testgespräch. Wie kann ich Ihnen helfen?",
                    farewell="Vielen Dank für Ihren Anruf. Auf Wiederhören!",
                    voice="marin",
                    speech_speed=1.0,
                    pronunciation_instructions="",
                    pronunciation_style="neutral",
                    regional_accent="",
                    tone="friendly_service",
                    custom_style_instructions="",
                    response_length=ResponseLength.short,
                    question_style="one_at_a_time",
                    turn_detection_type=TurnDetectionType.server_vad,
                    turn_eagerness=TurnEagerness.medium,
                    vad_threshold=0.5,
                    prefix_padding_ms=300,
                    silence_duration_ms=600,
                    interruptions_enabled=True,
                    idle_prompt_enabled=False,
                    idle_timeout_ms=10000,
                    primary_task="Unterstütze bei Anfragen und beantworte Fragen zum Unternehmen.",
                    off_topic_behavior="Führe sachfremde Fragen zum Unternehmensthema zurück.",
                    off_topic_mode="brief_redirect",
                    uncertainty_behavior="Sage offen, wenn eine Information nicht hinterlegt ist.",
                    uncertainty_modes=["acknowledge", "ask_clarifying"],
                    fallback_message="Dazu liegt mir keine verlässliche Information vor.",
                    simple_mode=True,
                    version=1,
                    updated_by_user_id=user.id,
                )
            )
        profile = self.db.scalar(
            select(AgentKnowledgeProfile).where(
                AgentKnowledgeProfile.tenant_id == tenant.id
            )
        )
        if profile is None:
            self.db.add(
                AgentKnowledgeProfile(
                    tenant_id=tenant.id,
                    company_description="",
                    products="",
                    locations="",
                    important_notes="",
                    contact_phone="",
                    contact_email=user.email or "",
                    website="",
                )
            )

    def _set_tenant_context(self, tenant_id: UUID) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
