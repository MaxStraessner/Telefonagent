import hmac
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import InitialAppSetup, TenantMembership
from app.services.authentication import AuthenticatedSession, AuthenticationService, SessionSecrets
from app.services.login_throttle import LoginThrottle, LoginThrottledError
from app.services.provisioning import ProvisioningService

logger = logging.getLogger(__name__)


class InitialSetupUnavailableError(Exception):
    pass


class InitialSetupInvalidCodeError(Exception):
    pass


@dataclass(frozen=True)
class InitialSetupResult:
    authenticated: AuthenticatedSession
    secrets: SessionSecrets


def generated_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return f"{(base or 'unternehmen')[:82]}-{secrets.token_hex(8)}"


class InitialSetupService:
    _state_id = 1
    _throttle_scope = "setup_ip"

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.throttle = LoginThrottle(db, settings)

    def available(self) -> bool:
        state = self.db.get(InitialAppSetup, self._state_id)
        return bool(
            self.settings.initial_setup_token
            and state is not None
            and state.completed_at is None
        )

    def complete(
        self,
        *,
        setup_code: str,
        company_name: str,
        industry: str,
        timezone_name: str,
        username: str,
        display_name: str,
        email: str | None,
        password: str,
        client_ip: str,
    ) -> InitialSetupResult:
        configured_code = self.settings.initial_setup_token
        if not configured_code:
            raise InitialSetupUnavailableError
        try:
            self.throttle.assert_allowed(self._throttle_scope, client_ip)
        except LoginThrottledError as exc:
            self._log_event("initial_setup_throttled", client_ip, "rejected")
            raise InitialSetupInvalidCodeError from exc
        if not hmac.compare_digest(configured_code, setup_code):
            self.throttle.record_failure(
                self._throttle_scope,
                client_ip,
                self.settings.auth_username_failure_limit,
            )
            self.db.commit()
            self._log_event("initial_setup_failed", client_ip, "rejected")
            raise InitialSetupInvalidCodeError

        now = datetime.now(timezone.utc)
        claim = self.db.execute(
            update(InitialAppSetup)
            .where(
                InitialAppSetup.id == self._state_id,
                InitialAppSetup.completed_at.is_(None),
            )
            .values(completed_at=now)
        )
        if claim.rowcount != 1:
            self.db.rollback()
            raise InitialSetupUnavailableError

        try:
            tenant, user = ProvisioningService(self.db).provision_tenant(
                slug=generated_slug(company_name),
                name=company_name,
                industry=industry,
                timezone_name=timezone_name,
                username=username,
                display_name=display_name,
                email=email,
                password=password,
                commit=False,
                mark_initial_setup=False,
            )
            state = self.db.get(InitialAppSetup, self._state_id)
            assert state is not None
            state.tenant_id = tenant.id
            state.user_id = user.id
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        assert membership is not None
        self.throttle.clear(self._throttle_scope, client_ip)
        authenticated, session_secrets = AuthenticationService(
            self.db, self.settings
        ).issue_session(user, membership, tenant)
        self._log_event("initial_setup_succeeded", client_ip, "accepted")
        return InitialSetupResult(authenticated, session_secrets)

    def _log_event(self, event_type: str, client_ip: str, result: str) -> None:
        logger.info(
            "initial_setup_event",
            extra={
                "event_type": event_type,
                "ip_bucket": self.throttle.bucket_id(self._throttle_scope, client_ip),
                "result": result,
            },
        )
