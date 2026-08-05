import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, normalize_username, sha256_token
from app.models import (
    AppUser,
    AuditLog,
    Invitation,
    PasswordResetToken,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.repositories.auth import AuthRepository, as_utc
from app.services.mail import MailAdapter, MailDeliveryError, OutboundMail


class AccountTokenInvalidError(Exception):
    pass


@dataclass(frozen=True)
class InvitationPreview:
    email: str
    display_name: str
    company_name: str | None
    role: str
    expires_at: datetime


class AccountLifecycleService:
    password_reset_lifetime = timedelta(minutes=30)
    invitation_lifetime = timedelta(hours=72)

    def __init__(self, db: Session, settings: Settings, mailer: MailAdapter):
        self.db = db
        self.settings = settings
        self.mailer = mailer
        self.repository = AuthRepository(db)

    def request_password_reset(self, identifier: str) -> None:
        normalized = normalize_username(identifier)
        user = self.repository.user_by_login_identifier(normalized)
        if user is None or not user.is_active or not user.email:
            return
        self._set_user_context(user.id)
        if not self._user_has_login_path(user):
            return
        now = self._now()
        for previous in self.db.scalars(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.revoked_at.is_(None),
            )
        ):
            previous.revoked_at = now
        raw_token = secrets.token_urlsafe(32)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=sha256_token(raw_token),
            expires_at=now + self.password_reset_lifetime,
        )
        self.db.add(token)
        self.db.flush()
        link = f"{self.settings.frontend_url.rstrip('/')}/passwort-zuruecksetzen?token={raw_token}"
        try:
            self.mailer.send(
                OutboundMail(
                    recipient=user.email,
                    subject="Passwort für Telefonagent zurücksetzen",
                    text=(
                        "Über diesen Link können Sie Ihr Passwort innerhalb von "
                        f"30 Minuten einmalig zurücksetzen:\n\n{link}"
                    ),
                )
            )
        except MailDeliveryError:
            token.revoked_at = now
        self.db.commit()

    def reset_password(self, raw_token: str, new_password: str) -> None:
        now = self._now()
        token = self.db.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == sha256_token(raw_token))
            .with_for_update()
        )
        if (
            token is None
            or token.used_at is not None
            or token.revoked_at is not None
            or as_utc(token.expires_at) <= now
        ):
            raise AccountTokenInvalidError
        user = self.db.get(AppUser, token.user_id)
        if user is None or not user.is_active:
            token.revoked_at = now
            self.db.commit()
            raise AccountTokenInvalidError
        self._set_user_context(user.id)
        tenant_id = self._first_usable_tenant_id(user)
        self._set_tenant_context(tenant_id)
        if not self._user_has_login_path(user):
            token.revoked_at = now
            self.db.commit()
            raise AccountTokenInvalidError
        token.used_at = now
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.must_change_password = False
        for other in self.db.scalars(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.id != token.id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.revoked_at.is_(None),
            )
        ):
            other.revoked_at = now
        self.repository.revoke_user_sessions(user.id, "password_recovered")
        self.db.add(
            AuditLog(
                actor_user_id=user.id,
                action="auth.password.recovered",
                target_type="app_user",
                target_id=str(user.id),
            )
        )
        self.db.commit()

    def invitation_preview(self, raw_token: str) -> InvitationPreview:
        self._set_invitation_context(raw_token)
        invitation = self._valid_invitation(raw_token, lock=False)
        self._set_tenant_context(invitation.tenant_id)
        tenant = self.db.get(Tenant, invitation.tenant_id) if invitation.tenant_id else None
        role = (
            invitation.tenant_role.value
            if invitation.tenant_role
            else invitation.platform_role.value
        )
        return InvitationPreview(
            email=invitation.email,
            display_name=invitation.display_name,
            company_name=tenant.name if tenant else None,
            role=role,
            expires_at=invitation.expires_at,
        )

    def accept_invitation(self, raw_token: str, password: str) -> None:
        now = self._now()
        self._set_invitation_context(raw_token)
        invitation = self._valid_invitation(raw_token, lock=True)
        self._set_tenant_context(invitation.tenant_id)
        user = self.db.get(AppUser, invitation.user_id) if invitation.user_id else None
        if user is None:
            user = self.repository.user_by_login_identifier(invitation.normalized_email)
        if user is None:
            user = AppUser(
                username=invitation.username,
                normalized_username=normalize_username(invitation.username),
                email=invitation.email,
                normalized_email=invitation.normalized_email,
                display_name=invitation.display_name,
                password_hash=hash_password(password),
                password_changed_at=now,
                is_active=True,
            )
            self.db.add(user)
            self.db.flush()
        else:
            if not user.is_active:
                raise AccountTokenInvalidError
            user.password_hash = hash_password(password)
            user.password_changed_at = now
            user.must_change_password = False
            self.repository.revoke_user_sessions(user.id, "invitation_accepted")

        self._set_user_context(user.id)

        if invitation.tenant_id:
            tenant = self.db.get(Tenant, invitation.tenant_id)
            if tenant is None or tenant.status not in {
                TenantStatus.trial,
                TenantStatus.active,
            }:
                raise AccountTokenInvalidError
            existing_memberships = self.repository.active_memberships(user.id)
            if any(item.tenant_id != tenant.id for item in existing_memberships):
                raise AccountTokenInvalidError
            membership = self.db.scalar(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.user_id == user.id,
                )
            )
            if membership is None:
                is_primary = False
                if invitation.tenant_role == TenantRole.company_admin:
                    primary_exists = self.db.scalar(
                        select(TenantMembership.id).where(
                            TenantMembership.tenant_id == tenant.id,
                            TenantMembership.is_active.is_(True),
                            TenantMembership.is_primary_admin.is_(True),
                        )
                    )
                    is_primary = primary_exists is None
                membership = TenantMembership(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role=invitation.tenant_role or TenantRole.company_user,
                    is_active=True,
                    is_primary_admin=is_primary,
                )
                self.db.add(membership)
            else:
                membership.role = invitation.tenant_role or TenantRole.company_user
                membership.is_active = True
        elif invitation.platform_role == PlatformRole.admin:
            user.platform_role = PlatformRole.admin
            user.is_platform_admin = True
        else:
            raise AccountTokenInvalidError

        invitation.user_id = user.id
        invitation.accepted_at = now
        self.db.add(
            AuditLog(
                actor_user_id=user.id,
                tenant_id=invitation.tenant_id,
                platform_role=user.platform_role.value if user.platform_role else None,
                action="auth.invitation.accepted",
                target_type="invitation",
                target_id=str(invitation.id),
            )
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AccountTokenInvalidError from exc

    def _valid_invitation(self, raw_token: str, *, lock: bool) -> Invitation:
        statement = select(Invitation).where(
            Invitation.token_hash == sha256_token(raw_token)
        )
        if lock:
            statement = statement.with_for_update()
        invitation = self.db.scalar(statement)
        now = self._now()
        if (
            invitation is None
            or invitation.accepted_at is not None
            or invitation.revoked_at is not None
            or invitation.delivery_status != "sent"
            or as_utc(invitation.expires_at) <= now
        ):
            raise AccountTokenInvalidError
        return invitation

    def _user_has_login_path(self, user: AppUser) -> bool:
        if user.platform_role in {PlatformRole.owner, PlatformRole.admin}:
            return True
        return self._first_usable_tenant_id(user) is not None

    def _first_usable_tenant_id(self, user: AppUser):
        memberships = self.repository.active_memberships(user.id)
        for membership in memberships:
            self._set_tenant_context(membership.tenant_id)
            tenant = self.db.get(Tenant, membership.tenant_id)
            if tenant and tenant.status in {TenantStatus.trial, TenantStatus.active}:
                return membership.tenant_id
        return None

    def _set_user_context(self, user_id: object) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )

    def _set_tenant_context(self, tenant_id: object | None) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id) if tenant_id else ""},
            )

    def _set_invitation_context(self, raw_token: str) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text(
                    "SELECT set_config('app.invitation_token_hash', :token_hash, true)"
                ),
                {"token_hash": sha256_token(raw_token)},
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
