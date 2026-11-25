from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import AppUser, CallSession, PlatformRole, Tenant, TenantMembership, TenantRole, TenantStatus
from app.models.entities import GmailNotification


def active_connection(db: Session, tenant_id: UUID) -> GmailNotification | None:
    return db.scalar(
        select(GmailNotification)
        .join(Tenant, Tenant.id == GmailNotification.tenant_id)
        .join(AppUser, AppUser.id == GmailNotification.user_id)
        .outerjoin(TenantMembership, and_(
            TenantMembership.tenant_id == GmailNotification.tenant_id,
            TenantMembership.user_id == GmailNotification.user_id,
        ))
        .where(
            GmailNotification.tenant_id == tenant_id,
            GmailNotification.enabled.is_(True),
            GmailNotification.encrypted_refresh_token.is_not(None),
            GmailNotification.recipient.is_not(None),
            AppUser.is_active.is_(True),
            Tenant.status.in_([TenantStatus.active, TenantStatus.trial]),
            or_(
                AppUser.platform_role.in_([PlatformRole.owner, PlatformRole.admin]),
                and_(TenantMembership.is_active.is_(True), TenantMembership.role == TenantRole.company_admin),
            ),
        ).execution_options(populate_existing=True)
    )


def queue_gmail_notification(db: Session, call: CallSession) -> bool:
    connection = active_connection(db, call.tenant_id)
    if connection is None:
        return False
    call.notification_status = "waiting_analysis"
    call.notification_recipient = connection.recipient
    call.notification_gmail_generation = connection.generation
    return True


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
