import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, normalize_username, sha256_token, verify_password
from app.models import (
    AppUser,
    AuditLog,
    Invitation,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.repositories.auth import AuthRepository, as_utc
from app.schemas.accounts import (
    CompanyCreate,
    CompanyOperationalUpdate,
    CompanyUpdate,
    CompanyUserInvite,
    CompanyUserUpdate,
    PlatformAdminInvite,
    PlatformAdminUpdate,
)
from app.services.audit import AuditService
from app.services.mail import MailAdapter, MailDeliveryError, OutboundMail
from app.services.provisioning import ProvisioningConflictError, ProvisioningService


class AccountManagementError(Exception):
    pass


class AccountConflictError(AccountManagementError):
    pass


class AccountNotFoundError(AccountManagementError):
    pass


class AccountInvariantError(AccountManagementError):
    pass


class AccountReauthenticationError(AccountManagementError):
    pass


class AccountDeliveryError(AccountManagementError):
    pass


class AccountManagementService:
    invitation_lifetime = timedelta(hours=72)

    def __init__(
        self,
        db: Session,
        settings: Settings,
        mailer: MailAdapter,
        *,
        actor: AppUser,
        request_id: str | None = None,
        client_ip: str | None = None,
    ):
        self.db = db
        self.settings = settings
        self.mailer = mailer
        self.actor = actor
        self.request_id = request_id
        self.client_ip = client_ip
        self.repository = AuthRepository(db)
        self.audit = AuditService(db, settings)

    def create_company(self, payload: CompanyCreate) -> Tenant:
        try:
            tenant = ProvisioningService(self.db).provision_pending_tenant(
                slug=payload.slug,
                name=payload.name,
                industry=payload.industry,
                timezone_name=payload.timezone,
                status=TenantStatus(payload.status),
            )
        except ProvisioningConflictError as exc:
            raise AccountConflictError(str(exc)) from exc
        tenant.legal_name = payload.legal_name
        tenant.contact_name = payload.contact_name
        tenant.contact_email = payload.contact_email
        tenant.contact_phone = payload.contact_phone
        tenant.is_demo = payload.is_demo
        self._set_tenant_context(tenant.id)

        if payload.first_admin.delivery == "temporary_password":
            admin = self._new_user(
                username=payload.first_admin.username,
                display_name=payload.first_admin.display_name,
                email=payload.first_admin.email,
                password=payload.first_admin.temporary_password or "",
                must_change_password=True,
            )
            self.db.add(
                TenantMembership(
                    tenant_id=tenant.id,
                    user=admin,
                    role=TenantRole.company_admin,
                    is_active=True,
                    is_primary_admin=True,
                )
            )
            ProvisioningService(self.db).ensure_tenant_baseline(tenant, admin)
        else:
            self.issue_company_invitation(
                tenant,
                CompanyUserInvite(
                    username=payload.first_admin.username,
                    display_name=payload.first_admin.display_name,
                    email=payload.first_admin.email,
                    role="company_admin",
                ),
                commit=False,
            )

        self._record(
            tenant.id,
            "platform.company.created",
            "tenant",
            tenant.id,
            after={
                "slug": tenant.slug,
                "name": tenant.name,
                "legal_name": tenant.legal_name,
                "status": tenant.status.value,
                "is_demo": tenant.is_demo,
            },
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AccountConflictError(
                "Unternehmen, Benutzername oder E-Mail-Adresse ist bereits vergeben."
            ) from exc
        return tenant

    def update_company(self, tenant: Tenant, payload: CompanyUpdate) -> Tenant:
        before = self._company_snapshot(tenant)
        tenant.name = payload.name
        tenant.legal_name = payload.legal_name
        tenant.industry = payload.industry
        tenant.timezone = payload.timezone
        tenant.contact_name = payload.contact_name
        tenant.contact_email = payload.contact_email
        tenant.contact_phone = payload.contact_phone
        tenant.is_demo = payload.is_demo
        self._record(
            tenant.id,
            "platform.company.updated",
            "tenant",
            tenant.id,
            before=before,
            after=self._company_snapshot(tenant),
        )
        self.db.commit()
        return tenant

    def update_company_operational(
        self, tenant: Tenant, payload: CompanyOperationalUpdate
    ) -> Tenant:
        before = self._company_snapshot(tenant)
        tenant.timezone = payload.timezone
        tenant.contact_name = payload.contact_name
        tenant.contact_email = payload.contact_email
        tenant.contact_phone = payload.contact_phone
        self._record(
            tenant.id,
            "company.profile.updated",
            "tenant",
            tenant.id,
            before=before,
            after=self._company_snapshot(tenant),
        )
        self.db.commit()
        return tenant

    def set_company_status(self, tenant: Tenant, target: TenantStatus) -> Tenant:
        if target == TenantStatus.active:
            primary_admin = self.db.scalar(
                select(TenantMembership.id)
                .join(AppUser, AppUser.id == TenantMembership.user_id)
                .where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.is_primary_admin.is_(True),
                    TenantMembership.role == TenantRole.company_admin,
                    TenantMembership.is_active.is_(True),
                    AppUser.is_active.is_(True),
                )
            )
            if primary_admin is None:
                raise AccountInvariantError(
                    "Ein aktives Unternehmen benötigt einen aktiven primären Administrator."
                )
        before = {"status": tenant.status.value}
        tenant.status = target
        if target in {TenantStatus.suspended, TenantStatus.archived}:
            self.repository.revoke_tenant_sessions(
                tenant.id, f"tenant_{target.value}_by_platform"
            )
        self._record(
            tenant.id,
            "platform.company.status_changed",
            "tenant",
            tenant.id,
            before=before,
            after={"status": target.value},
        )
        self.db.commit()
        return tenant

    def issue_company_invitation(
        self,
        tenant: Tenant,
        payload: CompanyUserInvite,
        *,
        commit: bool = True,
    ) -> Invitation:
        invitation = self._issue_invitation(
            tenant=tenant,
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            tenant_role=TenantRole(payload.role),
        )
        self._record(
            tenant.id,
            "company.invitation.created",
            "invitation",
            invitation.id,
            after={
                "email": invitation.email,
                "role": invitation.tenant_role.value,
                "delivery_status": invitation.delivery_status,
            },
        )
        if commit:
            self.db.commit()
        return invitation

    def issue_platform_admin_invitation(
        self, payload: PlatformAdminInvite
    ) -> Invitation:
        self._verify_actor_password(payload.current_password)
        invitation = self._issue_invitation(
            tenant=None,
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            platform_role=PlatformRole.admin,
        )
        self._record(
            None,
            "platform.admin.invitation.created",
            "invitation",
            invitation.id,
            after={
                "email": invitation.email,
                "platform_role": "admin",
                "delivery_status": invitation.delivery_status,
            },
        )
        self.db.commit()
        return invitation

    def revoke_invitation(self, invitation: Invitation) -> Invitation:
        if invitation.accepted_at is not None:
            raise AccountInvariantError("Eine angenommene Einladung kann nicht widerrufen werden.")
        if invitation.revoked_at is None:
            invitation.revoked_at = self._now()
        self._record(
            invitation.tenant_id,
            "invitation.revoked",
            "invitation",
            invitation.id,
            after={"email": invitation.email, "delivery_status": "revoked"},
        )
        self.db.commit()
        return invitation

    def update_company_user(
        self,
        tenant: Tenant,
        target_user: AppUser,
        membership: TenantMembership,
        payload: CompanyUserUpdate,
        *,
        actor_membership: TenantMembership | None,
    ) -> TenantMembership:
        if target_user.platform_role == PlatformRole.owner:
            raise AccountInvariantError("Der Plattforminhaber ist über APIs unveränderlich.")
        requested_role = TenantRole(payload.role)
        changing_role = requested_role != membership.role
        deactivating = not payload.is_active and membership.is_active
        if target_user.id == self.actor.id and deactivating:
            raise AccountInvariantError("Das eigene Konto kann hier nicht deaktiviert werden.")
        if membership.is_primary_admin and (deactivating or requested_role != TenantRole.company_admin):
            raise AccountInvariantError(
                "Übertragen Sie zuerst die primäre Administratorverantwortung."
            )
        if changing_role and self.actor.platform_role is None and not (
            actor_membership and actor_membership.is_primary_admin
        ):
            raise AccountInvariantError(
                "Nur der primäre Unternehmensadministrator darf Rollen ändern."
            )
        if (deactivating or requested_role != TenantRole.company_admin) and membership.role == TenantRole.company_admin:
            self._ensure_another_active_admin(tenant.id, membership.id)

        before = self._membership_snapshot(target_user, membership)
        target_user.display_name = payload.display_name
        target_user.email = payload.email
        target_user.normalized_email = normalize_username(payload.email) if payload.email else None
        target_user.is_active = payload.is_active
        membership.role = requested_role
        membership.is_active = payload.is_active
        if deactivating or changing_role:
            self.repository.revoke_user_sessions(target_user.id, "account_permissions_changed")
        self._record(
            tenant.id,
            "company.user.updated",
            "app_user",
            target_user.id,
            before=before,
            after=self._membership_snapshot(target_user, membership),
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AccountConflictError("Diese E-Mail-Adresse ist bereits vergeben.") from exc
        return membership

    def transfer_primary_admin(
        self,
        tenant: Tenant,
        target: TenantMembership,
        *,
        actor_membership: TenantMembership | None,
    ) -> TenantMembership:
        if self.actor.platform_role is None and not (
            actor_membership and actor_membership.is_primary_admin
        ):
            raise AccountInvariantError(
                "Nur der aktuelle primäre Administrator darf die Verantwortung übertragen."
            )
        if not target.is_active or target.role != TenantRole.company_admin:
            raise AccountInvariantError(
                "Der neue primäre Administrator muss ein aktiver Unternehmensadministrator sein."
            )
        current = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.is_active.is_(True),
                TenantMembership.is_primary_admin.is_(True),
            )
        )
        if current and current.id == target.id:
            return target
        if current:
            current.is_primary_admin = False
            self.db.flush()
        target.is_primary_admin = True
        self._record(
            tenant.id,
            "company.primary_admin.transferred",
            "tenant_membership",
            target.id,
            before={"is_primary_admin": False},
            after={"is_primary_admin": True, "role": target.role.value},
        )
        self.db.commit()
        return target

    def update_platform_admin(
        self, target: AppUser, payload: PlatformAdminUpdate
    ) -> AppUser:
        self._verify_actor_password(payload.current_password)
        if target.platform_role == PlatformRole.owner:
            raise AccountInvariantError("Der Plattforminhaber ist über APIs unveränderlich.")
        if target.platform_role != PlatformRole.admin:
            raise AccountNotFoundError("Plattformadministrator nicht gefunden.")
        if target.id == self.actor.id:
            raise AccountInvariantError("Das eigene Plattformkonto kann hier nicht geändert werden.")
        before = self._platform_user_snapshot(target)
        target.display_name = payload.display_name
        target.email = payload.email
        target.normalized_email = normalize_username(payload.email) if payload.email else None
        target.is_active = payload.is_active
        target.is_platform_admin = True
        self.repository.revoke_user_sessions(target.id, "platform_admin_changed")
        self._record(
            None,
            "platform.admin.updated",
            "app_user",
            target.id,
            before=before,
            after=self._platform_user_snapshot(target),
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AccountConflictError("Diese E-Mail-Adresse ist bereits vergeben.") from exc
        return target

    def invitation_status(self, invitation: Invitation) -> str:
        if invitation.accepted_at is not None:
            return "accepted"
        if invitation.revoked_at is not None:
            return "revoked"
        if invitation.delivery_status == "failed":
            return "failed"
        if as_utc(invitation.expires_at) <= self._now():
            return "expired"
        return invitation.delivery_status

    def _issue_invitation(
        self,
        *,
        tenant: Tenant | None,
        username: str,
        display_name: str,
        email: str,
        tenant_role: TenantRole | None = None,
        platform_role: PlatformRole | None = None,
    ) -> Invitation:
        normalized_email = normalize_username(email)
        normalized_username = normalize_username(username)
        user_by_email = self.db.scalar(
            select(AppUser).where(AppUser.normalized_email == normalized_email)
        )
        user_by_username = self.db.scalar(
            select(AppUser).where(AppUser.normalized_username == normalized_username)
        )
        if user_by_username and user_by_username.id != getattr(user_by_email, "id", None):
            raise AccountConflictError("Dieser Benutzername ist bereits vergeben.")
        existing_user = user_by_email or user_by_username
        if existing_user and not existing_user.is_active:
            raise AccountConflictError("Das zugehörige Konto ist deaktiviert.")
        if tenant is not None and existing_user:
            memberships = self.repository.active_memberships(existing_user.id)
            if any(item.tenant_id != tenant.id for item in memberships):
                raise AccountConflictError(
                    "Dieser Benutzer besitzt bereits eine aktive Unternehmensmitgliedschaft."
                )
            if any(item.tenant_id == tenant.id for item in memberships):
                raise AccountConflictError("Dieser Benutzer gehört bereits zum Unternehmen.")
        if platform_role and existing_user and existing_user.platform_role is not None:
            raise AccountConflictError("Dieser Benutzer besitzt bereits eine Plattformrolle.")

        now = self._now()
        conditions = [
            Invitation.normalized_email == normalized_email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        ]
        conditions.append(
            Invitation.tenant_id == tenant.id if tenant else Invitation.tenant_id.is_(None)
        )
        for previous in self.db.scalars(select(Invitation).where(*conditions)):
            previous.revoked_at = now

        raw_token = secrets.token_urlsafe(32)
        invitation = Invitation(
            tenant_id=tenant.id if tenant else None,
            user_id=existing_user.id if existing_user else None,
            created_by_user_id=self.actor.id,
            email=email.strip(),
            normalized_email=normalized_email,
            username=username.strip(),
            display_name=display_name.strip(),
            tenant_role=tenant_role,
            platform_role=platform_role,
            token_hash=sha256_token(raw_token),
            expires_at=now + self.invitation_lifetime,
            delivery_status="pending",
        )
        self.db.add(invitation)
        self.db.flush()
        link = f"{self.settings.frontend_url.rstrip('/')}/einladung/{raw_token}"
        try:
            self.mailer.send(
                OutboundMail(
                    recipient=invitation.email,
                    subject="Einladung zu Telefonagent",
                    text=(
                        "Diese Einladung ist 72 Stunden gültig und kann einmalig "
                        f"verwendet werden:\n\n{link}"
                    ),
                )
            )
        except MailDeliveryError as exc:
            invitation.delivery_status = "failed"
            invitation.revoked_at = now
            raise AccountDeliveryError("Die Einladung konnte nicht zugestellt werden.") from exc
        invitation.delivery_status = "sent"
        return invitation

    def _new_user(
        self,
        *,
        username: str,
        display_name: str,
        email: str,
        password: str,
        must_change_password: bool,
    ) -> AppUser:
        normalized_username = normalize_username(username)
        normalized_email = normalize_username(email)
        conflict = self.db.scalar(
            select(AppUser.id).where(
                or_(
                    AppUser.normalized_username == normalized_username,
                    AppUser.normalized_email == normalized_email,
                )
            )
        )
        if conflict:
            raise AccountConflictError("Benutzername oder E-Mail-Adresse ist bereits vergeben.")
        user = AppUser(
            username=username.strip(),
            normalized_username=normalized_username,
            display_name=display_name.strip(),
            email=email.strip(),
            normalized_email=normalized_email,
            password_hash=hash_password(password),
            password_changed_at=self._now(),
            must_change_password=must_change_password,
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _verify_actor_password(self, password: str) -> None:
        valid, _ = verify_password(password, self.actor.password_hash)
        if not valid:
            raise AccountReauthenticationError("Die Reauthentifizierung ist fehlgeschlagen.")

    def _ensure_another_active_admin(self, tenant_id: UUID, membership_id: UUID) -> None:
        another = self.db.scalar(
            select(TenantMembership.id).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.id != membership_id,
                TenantMembership.role == TenantRole.company_admin,
                TenantMembership.is_active.is_(True),
            )
        )
        if another is None:
            raise AccountInvariantError(
                "Der letzte aktive Unternehmensadministrator darf nicht entfernt werden."
            )

    def _record(
        self,
        tenant_id: UUID | None,
        action: str,
        target_type: str,
        target_id: object,
        *,
        before: dict | None = None,
        after: dict | None = None,
    ) -> None:
        self.audit.record(
            actor_user_id=self.actor.id,
            platform_role=self.actor.platform_role,
            tenant_id=tenant_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            request_id=self.request_id,
            client_ip=self.client_ip,
        )

    @staticmethod
    def _company_snapshot(tenant: Tenant) -> dict:
        return {
            "slug": tenant.slug,
            "name": tenant.name,
            "legal_name": tenant.legal_name,
            "status": tenant.status.value,
            "is_demo": tenant.is_demo,
        }

    @staticmethod
    def _membership_snapshot(user: AppUser, membership: TenantMembership) -> dict:
        return {
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "role": membership.role.value,
            "is_active": user.is_active and membership.is_active,
            "is_primary_admin": membership.is_primary_admin,
        }

    @staticmethod
    def _platform_user_snapshot(user: AppUser) -> dict:
        return {
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "platform_role": user.platform_role.value if user.platform_role else None,
            "is_active": user.is_active,
        }

    def _set_tenant_context(self, tenant_id: UUID) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


def active_user_count_statement():
    return (
        select(TenantMembership.tenant_id, func.count(TenantMembership.id))
        .join(AppUser, AppUser.id == TenantMembership.user_id)
        .where(TenantMembership.is_active.is_(True), AppUser.is_active.is_(True))
        .group_by(TenantMembership.tenant_id)
    )


def invitation_query(*, tenant_id: UUID | None):
    condition = Invitation.tenant_id == tenant_id if tenant_id else Invitation.tenant_id.is_(None)
    return select(Invitation).where(condition).order_by(Invitation.created_at.desc())


def audit_query(*, tenant_id: UUID | None = None, action: str | None = None):
    statement = select(AuditLog)
    if tenant_id is not None:
        statement = statement.where(AuditLog.tenant_id == tenant_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    return statement.order_by(AuditLog.created_at.desc())
