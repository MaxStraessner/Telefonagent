from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import AppUser, Tenant, TenantMembership, TenantRole
from app.repositories import TenantRepository, get_tenant_by_slug


@dataclass(frozen=True)
class TenantContext:
    id: UUID
    tenant: Tenant


@dataclass(frozen=True)
class UserContext:
    id: UUID
    email: str
    role: TenantRole


def get_tenant_context(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> TenantContext:
    tenant = get_tenant_by_slug(db, settings.active_tenant_slug)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "active_tenant_unavailable", "message": "Der aktive Mandant ist nicht verfügbar."},
        )
    return TenantContext(id=tenant.id, tenant=tenant)


def get_tenant_repository(
    context: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> TenantRepository:
    return TenantRepository(db, context.id)


def get_user_context(
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserContext:
    membership = db.scalar(
        select(TenantMembership)
        .join(AppUser, AppUser.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == context.id,
            AppUser.email == settings.active_user_email,
            AppUser.is_active.is_(True),
        )
        .options(selectinload(TenantMembership.user))
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "tenant_access_denied", "message": "Für diesen Mandanten besteht keine aktive Mitgliedschaft."},
        )
    return UserContext(id=membership.user_id, email=membership.user.email, role=membership.role)


def require_agent_admin(user: UserContext = Depends(get_user_context)) -> UserContext:
    if user.role not in {TenantRole.owner, TenantRole.admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "agent_configuration_forbidden", "message": "Nur Owner und Admins dürfen die KI-Konfiguration ändern."},
        )
    return user

