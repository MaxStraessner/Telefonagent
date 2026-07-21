from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Tenant
from app.repositories import TenantRepository, get_tenant_by_slug


@dataclass(frozen=True)
class TenantContext:
    id: UUID
    tenant: Tenant


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

