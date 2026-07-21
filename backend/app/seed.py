import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    AddressFormality,
    AgentBusinessHours,
    AgentConfiguration,
    AgentKnowledgeProfile,
    AgentKnowledgeService,
    AgentTopic,
    AppUser,
    Location,
    ResponseLength,
    Service,
    StaffMember,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantSettings,
    TenantStatus,
    TurnDetectionType,
    TurnEagerness,
)

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

    owner = db.scalar(select(AppUser).where(AppUser.email == "owner@telefonagent.local"))
    if owner is None:
        owner = AppUser(email="owner@telefonagent.local", display_name="Lokale Administration", is_active=True)
        db.add(owner)
        db.flush()
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == owner.id,
    ))
    if membership is None:
        db.add(TenantMembership(tenant_id=tenant.id, user_id=owner.id, role=TenantRole.owner))

    agent_config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant.id))
    if agent_config is None:
        welcome = settings.welcome_message if settings else (
            "Guten Tag, Sie sprechen mit dem digitalen Terminassistenten von Salon Haarkunst Test. Wie kann ich Ihnen helfen?"
        )
        assistant_name = settings.assistant_name if settings else "Lina"
        language = settings.default_language if settings else "de"
        db.add(AgentConfiguration(
            tenant_id=tenant.id, company_name=tenant.name, assistant_name=assistant_name,
            assistant_role="digitaler Terminassistent", transparency_notice="Ich bin ein KI-gestützter Sprachassistent.",
            address_formality=AddressFormality.formal, language=language,
            standard_greeting=welcome,
            outside_hours_greeting="Guten Tag. Sie erreichen uns außerhalb unserer Öffnungszeiten. Wie kann ich Ihnen weiterhelfen?",
            test_greeting="Willkommen zum Testgespräch. Wie kann ich Ihnen helfen?",
            farewell="Vielen Dank für Ihren Anruf. Auf Wiederhören!", voice="marin", speech_speed=1.0,
            pronunciation_instructions="", pronunciation_style="neutral", regional_accent="",
            tone="friendly_service", custom_style_instructions="", response_length=ResponseLength.short,
            question_style="one_at_a_time", turn_detection_type=TurnDetectionType.server_vad,
            turn_eagerness=TurnEagerness.medium, vad_threshold=0.5, prefix_padding_ms=300,
            silence_duration_ms=600, interruptions_enabled=True, idle_prompt_enabled=False, idle_timeout_ms=10000,
            primary_task="Unterstütze bei Terminanfragen und beantworte Fragen zum Unternehmen.",
            off_topic_behavior="Lehne sachfremde Fragen kurz ab und führe zum Unternehmensthema zurück.", off_topic_mode="brief_redirect",
            uncertainty_behavior="Sage offen, wenn eine Information nicht im Unternehmenswissen enthalten ist.",
            uncertainty_modes=["acknowledge", "ask_clarifying"],
            fallback_message="Dazu liegt mir keine verlässliche Information vor. Bitte wenden Sie sich direkt an das Unternehmen.",
            simple_mode=True, version=1, updated_by_user_id=owner.id,
        ))
        db.add(AgentKnowledgeProfile(
            tenant_id=tenant.id,
            company_description="Salon Haarkunst Test ist ein Friseursalon für Damen und Herren.",
            products="Haarpflegeprodukte auf Anfrage.",
            locations="Hauptstandort des Salons.",
            important_notes="Preise und Verfügbarkeiten nur nennen, wenn sie im strukturierten Wissen hinterlegt sind.",
            contact_phone="", contact_email="", website="",
        ))
        db.add(AgentTopic(
            tenant_id=tenant.id, label="Terminanfragen", topic_type="allowed",
            instructions="Erfrage Leistung, gewünschten Tag, Tageszeit und optional einen Mitarbeiterwunsch.",
            is_active=True, sort_order=0,
        ))
        for order, label in enumerate(("Rechtsberatung", "Medizinische Diagnose", "Interne Mitarbeiterdaten", "Vertrauliche Unternehmensinformationen"), start=1):
            db.add(AgentTopic(
                tenant_id=tenant.id, label=label, topic_type="forbidden",
                instructions="Gib hierzu keine inhaltliche Beratung oder verbindliche Auskunft.",
                is_active=True, sort_order=order,
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

    existing_knowledge_services = set(db.scalars(select(AgentKnowledgeService.name).where(AgentKnowledgeService.tenant_id == tenant.id)))
    for order, (name, description, _duration) in enumerate(service_data):
        if name not in existing_knowledge_services:
            db.add(AgentKnowledgeService(
                tenant_id=tenant.id, name=name, description=description,
                price_information="Preis auf Anfrage", is_active=True, sort_order=order,
            ))

    existing_hours = set(db.scalars(select(AgentBusinessHours.weekday).where(AgentBusinessHours.tenant_id == tenant.id)))
    for weekday in range(7):
        if weekday not in existing_hours:
            db.add(AgentBusinessHours(
                tenant_id=tenant.id, weekday=weekday, opens_at="09:00", closes_at="18:00",
                is_closed=weekday in {6},
            ))

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

