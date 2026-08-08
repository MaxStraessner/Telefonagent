from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password, normalize_username, verify_password
from app.models import (
    AddressFormality,
    AgentConfiguration,
    AgentKnowledgeProfile,
    AppUser,
    AuditLog,
    InitialAppSetup,
    PlatformRole,
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
        platform_owner: bool = False,
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
            or (platform_owner and user.platform_role not in {None, PlatformRole.owner})
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
                normalized_email=normalize_username(email) if email else None,
                display_name=display_name,
                is_active=True,
                platform_role=PlatformRole.owner if platform_owner else None,
                is_platform_admin=platform_owner,
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
                    role=TenantRole.company_admin,
                    is_active=True,
                    is_primary_admin=True,
                )
            )
        elif (
            membership.role != TenantRole.company_admin
            or not membership.is_active
            or not membership.is_primary_admin
        ):
            raise ProvisioningConflictError(
                "Die vorhandene Mitgliedschaft ist kein aktiver primärer Administrator."
            )
        if platform_owner:
            existing_owner = self.db.scalar(
                select(AppUser).where(
                    AppUser.platform_role == PlatformRole.owner,
                    AppUser.id != user.id,
                )
            )
            if existing_owner is not None:
                raise ProvisioningConflictError(
                    "Es existiert bereits ein Plattforminhaber."
                )
            user.platform_role = PlatformRole.owner
            user.is_platform_admin = True
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
                user.platform_role != PlatformRole.admin
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
            normalized_email=normalize_username(email) if email else None,
            display_name=display_name,
            is_active=True,
            platform_role=PlatformRole.admin,
            is_platform_admin=True,
            password_changed_at=datetime.now(timezone.utc),
        )
        self.db.add(user)
        self.db.commit()
        return user

    def promote_platform_owner(
        self,
        *,
        username: str,
        reauth_username: str,
        reauth_password: str,
    ) -> AppUser:
        target = self._user(username)
        if not target.is_active:
            raise ProvisioningConflictError("Der Zielbenutzer ist deaktiviert.")
        current_owner = self.db.scalar(
            select(AppUser).where(AppUser.platform_role == PlatformRole.owner)
        )
        expected_actor = current_owner or target
        if normalize_username(reauth_username) != expected_actor.normalized_username:
            raise ProvisioningConflictError(
                "Die Reauthentifizierung muss durch den aktuellen Plattforminhaber "
                "oder bei der Ersteinrichtung durch den Zielbenutzer erfolgen."
            )
        valid, _ = verify_password(reauth_password, expected_actor.password_hash)
        if not valid:
            raise ProvisioningConflictError("Die Reauthentifizierung ist fehlgeschlagen.")
        actor_role = expected_actor.platform_role
        self._set_user_context(expected_actor.id)
        if current_owner is not None and current_owner.id != target.id:
            current_owner.platform_role = PlatformRole.admin
            current_owner.is_platform_admin = True
            AuthRepository(self.db).revoke_user_sessions(
                current_owner.id, "platform_owner_transferred"
            )
            self.db.flush()
        target.platform_role = PlatformRole.owner
        target.is_platform_admin = True
        AuthRepository(self.db).revoke_user_sessions(
            target.id, "platform_owner_promoted"
        )
        self.db.add(
            AuditLog(
                actor_user_id=expected_actor.id,
                platform_role=(
                    actor_role.value if actor_role else None
                ),
                action="platform.owner.promoted",
                target_type="app_user",
                target_id=str(target.id),
                metadata_after={"username": target.username, "platform_role": "owner"},
            )
        )
        self.db.commit()
        return target

    def set_password(self, username: str, password: str) -> AppUser:
        user = self._user(username)
        user.password_hash = hash_password(password)
        user.password_changed_at = datetime.now(timezone.utc)
        user.must_change_password = False
        AuthRepository(self.db).revoke_user_sessions(user.id, "password_changed_by_operator")
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.is_active.is_(True),
                TenantMembership.role == TenantRole.company_admin,
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
        tenant.status = TenantStatus.suspended
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

    def provision_pending_tenant(
        self,
        *,
        slug: str,
        name: str,
        industry: str,
        timezone_name: str,
        status: TenantStatus = TenantStatus.trial,
    ) -> Tenant:
        if self.db.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
            raise ProvisioningConflictError("Der Unternehmens-Slug ist bereits vergeben.")
        tenant = Tenant(
            id=uuid4(),
            slug=slug,
            name=name,
            industry=industry,
            timezone=timezone_name,
            status=status,
        )
        self._set_tenant_context(tenant.id)
        self.db.add(tenant)
        self.db.flush()
        self._ensure_tenant_baseline(tenant, None)
        return tenant

    def ensure_tenant_baseline(
        self, tenant: Tenant, user: AppUser | None = None
    ) -> None:
        self._ensure_tenant_baseline(tenant, user)

    def _ensure_tenant_baseline(
        self, tenant: Tenant, user: AppUser | None
    ) -> None:
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
                    updated_by_user_id=user.id if user else None,
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
                    contact_email=(user.email if user else tenant.contact_email) or "",
                    website="",
                )
            )

    def _set_tenant_context(self, tenant_id: UUID) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    def _set_user_context(self, user_id: UUID) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )
