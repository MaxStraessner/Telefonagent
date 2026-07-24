import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.models import Tenant, TenantInboundRoute, TenantStatus


class TenantResolutionError(Exception):
    pass


class AuthenticatedBrowserTenantResolver:
    def resolve(self, authenticated_context: TenantContext) -> TenantContext:
        return authenticated_context


class InboundRouteTenantResolver:
    def __init__(self, db: Session):
        self.db = db

    def resolve(self, route_type: str, identifier: str) -> TenantContext:
        normalized = normalize_inbound_identifier(route_type, identifier)
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            tenant_id = self.db.scalar(
                text(
                    "SELECT resolve_inbound_route_tenant("
                    ":route_type, :normalized_identifier)"
                ),
                {
                    "route_type": route_type,
                    "normalized_identifier": normalized,
                },
            )
            if tenant_id is None:
                raise TenantResolutionError(
                    "Eingehende Route ist unbekannt oder mehrdeutig."
                )
            self.db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        rows = list(
            self.db.execute(
                select(TenantInboundRoute, Tenant)
                .join(Tenant, Tenant.id == TenantInboundRoute.tenant_id)
                .where(
                    TenantInboundRoute.route_type == route_type,
                    TenantInboundRoute.normalized_identifier == normalized,
                    TenantInboundRoute.is_active.is_(True),
                    Tenant.status == TenantStatus.active,
                )
            ).all()
        )
        if len(rows) != 1:
            raise TenantResolutionError("Eingehende Route ist unbekannt oder mehrdeutig.")
        route, tenant = rows[0]
        return TenantContext(id=route.tenant_id, tenant=tenant)


class DevelopmentTenantResolver:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def resolve(self) -> TenantContext:
        if (
            self.settings.app_env.lower() != "development"
            or not self.settings.allow_development_tenant_fallback
        ):
            raise TenantResolutionError("Entwicklungs-Tenant-Fallback ist deaktiviert.")
        tenant = self.db.scalar(
            select(Tenant).where(
                Tenant.slug == self.settings.development_tenant_slug,
                Tenant.status == TenantStatus.active,
            )
        )
        if tenant is None:
            raise TenantResolutionError("Entwicklungs-Tenant ist nicht verfügbar.")
        return TenantContext(id=tenant.id, tenant=tenant)


def normalize_inbound_identifier(route_type: str, identifier: str) -> str:
    value = identifier.strip()
    if route_type == "phone_number":
        plus = value.startswith("+")
        digits = re.sub(r"\D", "", value)
        return f"+{digits}" if plus else digits
    return value.casefold()
