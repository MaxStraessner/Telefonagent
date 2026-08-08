from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import AppUser, Tenant, TenantMembership, UserSession


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AuthRepository:
    def __init__(self, db: Session):
        self.db = db

    def user_by_login_identifier(self, normalized_identifier: str) -> AppUser | None:
        return self.db.scalar(
            select(AppUser).where(
                or_(
                    AppUser.normalized_username == normalized_identifier,
                    AppUser.normalized_email == normalized_identifier,
                )
            )
        )

    def user_by_normalized_username(self, normalized_username: str) -> AppUser | None:
        """Compatibility wrapper for callers predating e-mail login."""
        return self.user_by_login_identifier(normalized_username)

    def active_memberships(self, user_id: object) -> list[TenantMembership]:
        return list(
            self.db.scalars(
                select(TenantMembership).where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.is_active.is_(True),
                )
            )
        )

    def raw_session_by_token_hash(self, token_hash: str) -> UserSession | None:
        return self.db.scalar(
            select(UserSession).where(UserSession.token_hash == token_hash)
        )

    def session_by_token_hash(
        self, token_hash: str
    ) -> tuple[UserSession, AppUser, TenantMembership | None, Tenant | None] | None:
        base = self.db.execute(
            select(UserSession, AppUser)
            .join(AppUser, AppUser.id == UserSession.user_id)
            .where(UserSession.token_hash == token_hash)
        ).one_or_none()
        if base is None:
            return None
        session, user = base
        if session.active_tenant_id is None:
            return session, user, None, None
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == session.user_id,
                TenantMembership.tenant_id == session.active_tenant_id,
            )
        )
        tenant = self.db.get(Tenant, session.active_tenant_id)
        return session, user, membership, tenant

    def revoke_user_sessions(self, user_id: object, reason: str) -> None:
        now = utc_now()
        sessions = self.db.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
        )
        for session in sessions:
            session.revoked_at = now
            session.revoke_reason = reason

    def revoke_tenant_sessions(self, tenant_id: object, reason: str) -> None:
        now = utc_now()
        sessions = self.db.scalars(
            select(UserSession).where(
                UserSession.active_tenant_id == tenant_id,
                UserSession.revoked_at.is_(None),
            )
        )
        for session in sessions:
            session.revoked_at = now
            session.revoke_reason = reason
