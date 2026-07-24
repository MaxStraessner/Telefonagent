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
from app.models import AppUser, Tenant, TenantMembership, TenantRole, TenantStatus, UserSession
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
    membership: TenantMembership
    tenant: Tenant


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

        user = self.repository.user_by_normalized_username(normalized)
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
            and len(membership_rows) == 1
        )
        tenant = None
        if accepted:
            membership = membership_rows[0]
            self._set_tenant_context(membership.tenant_id)
            tenant = self.db.scalar(
                select(Tenant).where(
                    Tenant.id == membership.tenant_id,
                    Tenant.status == TenantStatus.active,
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
        membership = membership_rows[0]
        assert tenant is not None
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
        self._set_tenant_context(raw_session.tenant_id)
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
        active = (
            user.is_active
            and membership.is_active
            and membership.role
            in {TenantRole.owner, TenantRole.admin, TenantRole.employee, TenantRole.member}
            and tenant.status == TenantStatus.active
        )
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
        self._set_tenant_context(tenant.id)
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
        self.repository.revoke_user_sessions(authenticated.user.id, "password_changed")
        result = self._new_session(
            authenticated.user, authenticated.membership, authenticated.tenant
        )
        self.db.commit()
        return result

    def _new_session(
        self, user: AppUser, membership: TenantMembership, tenant: Tenant
    ) -> tuple[AuthenticatedSession, SessionSecrets]:
        now = self._now()
        absolute = now + timedelta(hours=self.settings.session_absolute_hours)
        secrets = generate_session_secrets()
        session = UserSession(
            token_hash=secrets.token_hash,
            csrf_token_hash=secrets.csrf_token_hash,
            user_id=user.id,
            tenant_id=tenant.id,
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
