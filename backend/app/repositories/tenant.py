from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Appointment, Service, StaffMember, Tenant


class TenantRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def list_services(self) -> list[Service]:
        return list(self.db.scalars(select(Service).where(Service.tenant_id == self.tenant_id).order_by(Service.name)))

    def list_staff(self) -> list[StaffMember]:
        return list(self.db.scalars(select(StaffMember).where(StaffMember.tenant_id == self.tenant_id).order_by(StaffMember.display_name)))

    def list_appointments(self) -> list[Appointment]:
        statement = (
            select(Appointment)
            .where(Appointment.tenant_id == self.tenant_id)
            .options(selectinload(Appointment.service), selectinload(Appointment.staff_member))
            .order_by(Appointment.starts_at)
        )
        return list(self.db.scalars(statement))


def get_tenant_by_slug(db: Session, slug: str) -> Tenant | None:
    statement = (
        select(Tenant)
        .where(Tenant.slug == slug)
        .options(selectinload(Tenant.settings), selectinload(Tenant.locations))
    )
    return db.scalar(statement)

