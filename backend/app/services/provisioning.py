from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, normalize_username
from app.models import (
    AppUser,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantSettings,
    TenantStatus,
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
                slug=slug,
                name=name,
                industry=industry,
                timezone=timezone_name,
                status=TenantStatus.active,
            )
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
