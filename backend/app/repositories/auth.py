from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppUser, Tenant, TenantMembership, UserSession


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AuthRepository:
    def __init__(self, db: Session):
        self.db = db

    def user_by_normalized_username(self, normalized_username: str) -> AppUser | None:
        return self.db.scalar(
            select(AppUser).where(AppUser.normalized_username == normalized_username)
        )

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
    ) -> tuple[UserSession, AppUser, TenantMembership, Tenant] | None:
        return self.db.execute(
            select(UserSession, AppUser, TenantMembership, Tenant)
            .join(AppUser, AppUser.id == UserSession.user_id)
            .join(
                TenantMembership,
                (TenantMembership.user_id == UserSession.user_id)
                & (TenantMembership.tenant_id == UserSession.tenant_id),
            )
            .join(Tenant, Tenant.id == UserSession.tenant_id)
            .where(UserSession.token_hash == token_hash)
        ).one_or_none()

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
                UserSession.tenant_id == tenant_id,
                UserSession.revoked_at.is_(None),
            )
        )
        for session in sessions:
            session.revoked_at = now
            session.revoke_reason = reason
