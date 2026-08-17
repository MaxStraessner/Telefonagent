import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import (
    SessionSecrets,
    constant_time_token_matches,
    generate_session_secrets,
    hash_password,
    normalize_username,
    pseudonymize,
    sha256_token,
    verify_password,
)
from app.db.session import bind_tenant_context
from app.models import (
    AppUser,
    AuditLog,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantStatus,
    UserSession,
)
from app.repositories.auth import AuthRepository, as_utc
from app.services.login_throttle import LoginThrottle, LoginThrottledError

logger = logging.getLogger(__name__)

GENERIC_LOGIN_MESSAGE = "Benutzername oder Passwort ist ungültig."


class InvalidCredentialsError(Exception):
    pass


class SessionInvalidError(Exception):
    pass


class CsrfValidationError(Exception):
    pass


@dataclass(frozen=True)
class AuthenticatedSession:
    session: UserSession
    user: AppUser
    membership: TenantMembership | None
    tenant: Tenant | None


class AuthenticationService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.repository = AuthRepository(db)
        self.throttle = LoginThrottle(db, settings)

    def login(
        self, username: str, password: str, client_ip: str
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        normalized = normalize_username(username)
        try:
            self.throttle.assert_allowed("username", normalized)
            self.throttle.assert_allowed("ip", client_ip)
        except LoginThrottledError as exc:
            self._log_event("login_throttled", normalized, client_ip, "rejected")
            raise InvalidCredentialsError from exc

        user = self.repository.user_by_login_identifier(normalized)
        valid_password, updated_hash = verify_password(
            password, user.password_hash if user else None
        )
        membership_rows = []
        if user and valid_password and user.is_active:
            self._set_user_context(user.id)
            membership_rows = self.repository.active_memberships(user.id)
        accepted = bool(
            user
            and valid_password
            and user.is_active
            and (user.platform_role is not None or membership_rows)
        )
        membership: TenantMembership | None = None
        tenant: Tenant | None = None
        if accepted and user and user.platform_role is None and len(membership_rows) == 1:
            membership = membership_rows[0]
            self._set_tenant_context(membership.tenant_id)
            tenant = self.db.scalar(
                select(Tenant).where(
                    Tenant.id == membership.tenant_id,
                    Tenant.status.in_([TenantStatus.trial, TenantStatus.active]),
                )
            )
            accepted = tenant is not None
        if not accepted:
            username_bucket = self.throttle.record_failure(
                "username", normalized, self.settings.auth_username_failure_limit
            )
            ip_bucket = self.throttle.record_failure(
                "ip", client_ip, self.settings.auth_ip_failure_limit
            )
            self.db.commit()
            logger.info(
                "authentication_event",
                extra={
                    "event_type": "login_failed",
                    "username_bucket": username_bucket,
                    "ip_bucket": ip_bucket,
                    "result": "rejected",
                },
            )
            raise InvalidCredentialsError

        assert user is not None
        self._set_platform_role_context(user.platform_role)
        if updated_hash:
            user.password_hash = updated_hash
        user.last_login_at = self._now()
        self.throttle.clear("username", normalized)
        authenticated, secrets = self._new_session(user, membership, tenant)
        self.db.commit()
        self._log_event("login_succeeded", normalized, client_ip, "accepted")
        return authenticated, secrets

    def authenticate(self, raw_token: str | None) -> AuthenticatedSession:
        if not raw_token:
            raise SessionInvalidError
        raw_session = self.repository.raw_session_by_token_hash(sha256_token(raw_token))
        if raw_session is None:
            raise SessionInvalidError
        self._set_user_context(raw_session.user_id)
        self._set_tenant_context(raw_session.active_tenant_id)
        row = self.repository.session_by_token_hash(sha256_token(raw_token))
        if row is None:
            raise SessionInvalidError
        session, user, membership, tenant = row
        now = self._now()
        expired = (
            session.revoked_at is not None
            or as_utc(session.idle_expires_at) <= now
            or as_utc(session.absolute_expires_at) <= now
        )
        has_company_access = bool(
            membership
            and membership.is_active
            and membership.role in {TenantRole.company_admin, TenantRole.company_user}
            and tenant
            and (
                user.platform_role in {PlatformRole.owner, PlatformRole.admin}
                or tenant.status in {TenantStatus.trial, TenantStatus.active}
            )
        )
        has_platform_access = user.platform_role in {
            PlatformRole.owner,
            PlatformRole.admin,
        }
        if session.active_tenant_id is None:
            has_company_access = bool(self.repository.active_memberships(user.id))
        active = user.is_active and (has_platform_access or has_company_access)
        if expired or not active:
            if session.revoked_at is None:
                session.revoked_at = now
                session.revoke_reason = "expired" if expired else "access_inactive"
                self.db.commit()
            raise SessionInvalidError

        touch_after = timedelta(seconds=self.settings.session_touch_interval_seconds)
        if as_utc(session.last_seen_at) + touch_after <= now:
            session.last_seen_at = now
            session.idle_expires_at = min(
                now + timedelta(minutes=self.settings.session_idle_minutes),
                as_utc(session.absolute_expires_at),
            )
            self.db.commit()

        self._set_user_context(user.id)
        self._set_platform_role_context(user.platform_role)
        self._set_tenant_context(tenant.id if tenant else None)
        return AuthenticatedSession(session, user, membership, tenant)

    def validate_csrf(
        self, authenticated: AuthenticatedSession, cookie_token: str | None, header_token: str | None
    ) -> None:
        if (
            not cookie_token
            or not header_token
            or cookie_token != header_token
            or not constant_time_token_matches(
                header_token, authenticated.session.csrf_token_hash
            )
        ):
            raise CsrfValidationError

    def logout(self, authenticated: AuthenticatedSession) -> None:
        authenticated.session.revoked_at = self._now()
        authenticated.session.revoke_reason = "logout"
        self.db.commit()

    def change_password(
        self,
        authenticated: AuthenticatedSession,
        current_password: str,
        new_password: str,
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        valid, _ = verify_password(current_password, authenticated.user.password_hash)
        if not valid:
            raise InvalidCredentialsError
        authenticated.user.password_hash = hash_password(new_password)
        authenticated.user.password_changed_at = self._now()
        authenticated.user.must_change_password = False
        self.repository.revoke_user_sessions(authenticated.user.id, "password_changed")
        result = self._new_session(
            authenticated.user, authenticated.membership, authenticated.tenant
        )
        self.db.commit()
        return result

    def issue_session(
        self,
        user: AppUser,
        membership: TenantMembership | None,
        tenant: Tenant | None,
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        """Issue a normal browser session after an already-authorized flow."""
        self._set_user_context(user.id)
        self._set_platform_role_context(user.platform_role)
        self._set_tenant_context(tenant.id if tenant else None)
        user.last_login_at = self._now()
        result = self._new_session(user, membership, tenant)
        self.db.commit()
        return result

    def switch_context(
        self, authenticated: AuthenticatedSession, tenant_id: UUID
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        self._set_user_context(authenticated.user.id)
        self._set_platform_role_context(authenticated.user.platform_role)
        self._set_tenant_context(tenant_id)
        tenant = self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise SessionInvalidError
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == authenticated.user.id,
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.is_active.is_(True),
            )
        )
        is_platform_actor = authenticated.user.platform_role in {
            PlatformRole.owner,
            PlatformRole.admin,
        }
        if not is_platform_actor and (
            membership is None
            or tenant.status not in {TenantStatus.trial, TenantStatus.active}
        ):
            raise SessionInvalidError
        authenticated.session.revoked_at = self._now()
        authenticated.session.revoke_reason = "company_context_switched"
        result = self._new_session(authenticated.user, membership, tenant)
        self.db.add(
            AuditLog(
                actor_user_id=authenticated.user.id,
                tenant_id=tenant.id,
                platform_role=(
                    authenticated.user.platform_role.value
                    if authenticated.user.platform_role
                    else None
                ),
                action="auth.context.selected",
                target_type="tenant",
                target_id=str(tenant.id),
                metadata_after={"tenant_slug": tenant.slug},
            )
        )
        self.db.commit()
        return result

    def clear_context(
        self, authenticated: AuthenticatedSession
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        if authenticated.user.platform_role not in {
            PlatformRole.owner,
            PlatformRole.admin,
        }:
            raise SessionInvalidError
        previous_tenant = authenticated.tenant
        authenticated.session.revoked_at = self._now()
        authenticated.session.revoke_reason = "company_context_cleared"
        self._set_tenant_context(previous_tenant.id if previous_tenant else None)
        result = self._new_session(authenticated.user, None, None)
        self.db.add(
            AuditLog(
                actor_user_id=authenticated.user.id,
                tenant_id=previous_tenant.id if previous_tenant else None,
                platform_role=authenticated.user.platform_role.value,
                action="auth.context.cleared",
                target_type="tenant",
                target_id=str(previous_tenant.id) if previous_tenant else None,
            )
        )
        self.db.commit()
        return result

    def _new_session(
        self,
        user: AppUser,
        membership: TenantMembership | None,
        tenant: Tenant | None,
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        now = self._now()
        absolute = now + timedelta(hours=self.settings.session_absolute_hours)
        secrets = generate_session_secrets()
        session = UserSession(
            token_hash=secrets.token_hash,
            csrf_token_hash=secrets.csrf_token_hash,
            user_id=user.id,
            active_tenant_id=tenant.id if tenant else None,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=min(
                now + timedelta(minutes=self.settings.session_idle_minutes), absolute
            ),
            absolute_expires_at=absolute,
        )
        self.db.add(session)
        self.db.flush()
        return AuthenticatedSession(session, user, membership, tenant), secrets

    def _set_tenant_context(self, tenant_id: UUID | None) -> None:
        if tenant_id is not None:
            bind_tenant_context(self.db, tenant_id)
        else:
            self.db.info.pop("tenant_id", None)
        if tenant_id is None and self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": ""},
            )

    def _set_user_context(self, user_id: UUID) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )

    def _set_platform_role_context(self, role: PlatformRole | None) -> None:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(
                text("SELECT set_config('app.platform_role', :role, true)"),
                {"role": role.value if role else ""},
            )

    def _log_event(
        self, event_type: str, normalized_username: str, client_ip: str, result: str
    ) -> None:
        logger.info(
            "authentication_event",
            extra={
                "event_type": event_type,
                "username_bucket": pseudonymize(
                    f"username:{normalized_username}", self.settings.auth_hmac_secret
                ),
                "ip_bucket": pseudonymize(
                    f"ip:{client_ip}", self.settings.auth_hmac_secret
                ),
                "result": result,
            },
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
