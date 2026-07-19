import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Location, Service, StaffMember, Tenant, TenantSettings, TenantStatus

logger = logging.getLogger(__name__)


def seed_database(db: Session) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    if tenant is None:
        tenant = Tenant(
            slug="salon-haarkunst-test", name="Salon Haarkunst Test", industry="hair_salon",
            timezone="Europe/Berlin", status=TenantStatus.active,
        )
        db.add(tenant)
        db.flush()

    settings = db.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant.id))
    if settings is None:
        db.add(TenantSettings(
            tenant_id=tenant.id, assistant_name="Lina", default_language="de",
            welcome_message="Guten Tag, Sie sprechen mit dem digitalen Terminassistenten von Salon Haarkunst Test. Wie kann ich Ihnen helfen?",
            presentation_mode_enabled=False, diagnostics_enabled=True,
        ))

    location = db.scalar(select(Location).where(Location.tenant_id == tenant.id, Location.is_primary.is_(True)))
    if location is None:
        db.add(Location(tenant_id=tenant.id, name="Hauptstandort", timezone="Europe/Berlin", is_primary=True))

    service_data = [
        ("Herrenhaarschnitt", "Klassischer Haarschnitt für Herren.", 30),
        ("Damenhaarschnitt", "Individueller Haarschnitt für Damen.", 60),
        ("Waschen und Föhnen", "Waschen, Pflege und professionelles Föhnen.", 45),
    ]
    existing_services = set(db.scalars(select(Service.name).where(Service.tenant_id == tenant.id)))
    for name, description, duration in service_data:
        if name not in existing_services:
            db.add(Service(tenant_id=tenant.id, name=name, description=description, duration_minutes=duration, is_active=True))

    existing_staff = set(db.scalars(select(StaffMember.display_name).where(StaffMember.tenant_id == tenant.id)))
    for name in ("Anna", "Ben"):
        if name not in existing_staff:
            db.add(StaffMember(tenant_id=tenant.id, display_name=name, role_name="Stylist:in", is_active=True))

    db.commit()
    db.refresh(tenant)
    return tenant


def main() -> None:
    with SessionLocal() as db:
        tenant = seed_database(db)
        logger.info("Seed abgeschlossen", extra={"tenant_slug": tenant.slug})


if __name__ == "__main__":
    main()

