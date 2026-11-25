from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext, UserContext
from app.models import (
    AddressFormality,
    AgentBehaviorRule,
    AgentBusinessHours,
    AgentCapability,
    AgentConfiguration,
    AgentConfigurationAudit,
    AgentFaq,
    AgentKnowledgeProfile,
    AgentKnowledgeService,
    AgentTopic,
    CallSession,
    PlatformRole,
    ResponseLength,
    Tenant,
    TenantRole,
    TurnDetectionType,
    TurnEagerness,
)
from app.schemas.agent_config import (
    AgentConfigurationResponse,
    AgentConfigurationUpdate,
    AgentKnowledgeResponse,
    AgentKnowledgeUpdate,
    AgentRuleData,
    AgentTopicData,
    BusinessHoursData,
    FaqData,
    KnowledgeProfileData,
    KnowledgeServiceData,
)

MAX_KNOWLEDGE_CHARACTERS = 12_000


@dataclass(frozen=True)
class AgentBundle:
    configuration: AgentConfiguration
    topics: list[AgentTopic]
    rules: list[AgentBehaviorRule]
    profile: AgentKnowledgeProfile
    faqs: list[AgentFaq]
    services: list[AgentKnowledgeService]
    business_hours: list[AgentBusinessHours]
    capabilities: list[AgentCapability]


def _configuration_missing() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "agent_configuration_missing", "message": "Für diesen Mandanten fehlt die KI-Konfiguration."},
    )


def load_agent_bundle(db: Session, tenant_id: UUID, *, for_update: bool = False) -> AgentBundle:
    query = select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant_id)
    configuration = db.scalar(query.with_for_update() if for_update else query)
    profile = db.scalar(select(AgentKnowledgeProfile).where(AgentKnowledgeProfile.tenant_id == tenant_id))
    if configuration is None or profile is None:
        raise _configuration_missing()
    def ordered(model):
        return list(db.scalars(
            select(model).where(model.tenant_id == tenant_id).order_by(model.sort_order, model.created_at)
        ))
    return AgentBundle(
        configuration=configuration,
        topics=ordered(AgentTopic),
        rules=ordered(AgentBehaviorRule),
        profile=profile,
        faqs=ordered(AgentFaq),
        services=ordered(AgentKnowledgeService),
        business_hours=list(db.scalars(
            select(AgentBusinessHours).where(AgentBusinessHours.tenant_id == tenant_id).order_by(AgentBusinessHours.weekday)
        )),
        capabilities=list(db.scalars(
            select(AgentCapability).where(AgentCapability.tenant_id == tenant_id).order_by(AgentCapability.capability_key)
        )),
    )


def _can_edit(user: UserContext) -> bool:
    return user.role == TenantRole.company_admin or user.platform_role in {
        PlatformRole.owner,
        PlatformRole.admin,
    }


def configuration_response(bundle: AgentBundle, user: UserContext) -> AgentConfigurationResponse:
    config = bundle.configuration
    return AgentConfigurationResponse(
        tenant_id=config.tenant_id, version=config.version, updated_at=config.updated_at,
        can_edit=_can_edit(user),
        role=user.role.value if user.role else "platform_admin",
        operating_mode=config.operating_mode,
        telephone_model=config.telephone_model,
        message_greeting=config.message_greeting,
        call_retention_days=config.call_retention_days,
        call_email_enabled=config.call_email_enabled,
        call_email_recipient=config.call_email_recipient,
        company_name=config.company_name, assistant_name=config.assistant_name,
        assistant_role=config.assistant_role, transparency_notice=config.transparency_notice,
        address_formality=config.address_formality.value, language=config.language,
        standard_greeting=config.standard_greeting, outside_hours_greeting=config.outside_hours_greeting,
        test_greeting=config.test_greeting, farewell=config.farewell, voice=config.voice,
        speech_speed=config.speech_speed, pronunciation_instructions=config.pronunciation_instructions,
        pronunciation_style=config.pronunciation_style, regional_accent=config.regional_accent,
        tone=config.tone, custom_style_instructions=config.custom_style_instructions,
        response_length=config.response_length.value, question_style=config.question_style,
        turn_detection_type=config.turn_detection_type.value, turn_eagerness=config.turn_eagerness.value,
        vad_threshold=config.vad_threshold, prefix_padding_ms=config.prefix_padding_ms,
        silence_duration_ms=config.silence_duration_ms, interruptions_enabled=config.interruptions_enabled,
        idle_prompt_enabled=config.idle_prompt_enabled, idle_timeout_ms=config.idle_timeout_ms,
        primary_task=config.primary_task, off_topic_behavior=config.off_topic_behavior,
        off_topic_mode=config.off_topic_mode, uncertainty_behavior=config.uncertainty_behavior,
        uncertainty_modes=config.uncertainty_modes, fallback_message=config.fallback_message,
        simple_mode=config.simple_mode,
        topics=[AgentTopicData.model_validate(item, from_attributes=True) for item in bundle.topics],
        custom_rules=[AgentRuleData.model_validate(item, from_attributes=True) for item in bundle.rules],
    )


def knowledge_response(bundle: AgentBundle, user: UserContext) -> AgentKnowledgeResponse:
    return AgentKnowledgeResponse(
        tenant_id=bundle.configuration.tenant_id,
        version=bundle.configuration.version,
        can_edit=_can_edit(user),
        profile=KnowledgeProfileData.model_validate(bundle.profile, from_attributes=True),
        faqs=[FaqData.model_validate(item, from_attributes=True) for item in bundle.faqs],
        services=[KnowledgeServiceData.model_validate(item, from_attributes=True) for item in bundle.services],
        business_hours=[BusinessHoursData.model_validate(item, from_attributes=True) for item in bundle.business_hours],
    )


def _check_version(config: AgentConfiguration, expected: int) -> None:
    if config.version != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_configuration_version_conflict",
                "message": "Die KI-Konfiguration wurde zwischenzeitlich geändert. Bitte laden Sie die Seite neu.",
            },
        )


def _write_audit(db: Session, bundle: AgentBundle, user: UserContext) -> None:
    platform_actor = user.role is None and user.platform_role in {PlatformRole.owner, PlatformRole.admin}
    db.add(AgentConfigurationAudit(
        tenant_id=bundle.configuration.tenant_id,
        version=bundle.configuration.version,
        changed_by_user_id=None if platform_actor else user.id,
        platform_actor_id=user.id if platform_actor else None,
        snapshot={
            "configuration": configuration_response(bundle, user).model_dump(mode="json"),
            "knowledge": knowledge_response(bundle, user).model_dump(mode="json"),
        },
    ))


def rename_company_for_calls(db: Session, tenant: Tenant, name: str, user: UserContext) -> None:
    bundle = load_agent_bundle(db, tenant.id, for_update=True)
    config = bundle.configuration
    # Only rewrite greetings generated by provisioning. Preserve custom scripts verbatim.
    for prefix in ("Guten Tag, Sie sprechen mit ", "Guten Tag, Sie sprechen mit dem digitalen Terminassistenten von "):
        previous = f"{prefix}{tenant.name}. Wie kann ich Ihnen helfen?"
        updated = f"{prefix}{name}. Wie kann ich Ihnen helfen?"
        if tenant.settings and tenant.settings.welcome_message == previous:
            tenant.settings.welcome_message = updated
        if config.standard_greeting == previous:
            config.standard_greeting = updated
    config.company_name = name
    config.version += 1
    config.updated_by_user_id = user.id
    db.flush()
    _write_audit(db, bundle, user)


def update_configuration(
    db: Session, context: TenantContext, user: UserContext, payload: AgentConfigurationUpdate,
) -> AgentBundle:
    bundle = load_agent_bundle(db, context.id, for_update=True)
    config = bundle.configuration
    _check_version(config, payload.expected_version)
    if not payload.call_email_enabled or payload.call_email_recipient != config.call_email_recipient:
        db.execute(update(CallSession).where(
            CallSession.tenant_id == context.id,
            CallSession.notification_gmail_generation.is_(None),
            CallSession.notification_status.in_(["waiting_analysis", "pending"]),
        ).values(notification_status="canceled", notification_recipient=None,
                 notification_next_attempt_at=None, notification_lease_token=None))
    scalar_fields = payload.model_dump(exclude={"expected_version", "topics", "custom_rules"})
    if "telephone_model" not in payload.model_fields_set:
        scalar_fields.pop("telephone_model")
    enum_fields = {
        "address_formality": AddressFormality,
        "response_length": ResponseLength,
        "turn_detection_type": TurnDetectionType,
        "turn_eagerness": TurnEagerness,
    }
    for key, value in scalar_fields.items():
        setattr(config, key, enum_fields[key](value) if key in enum_fields else value)
    config.version += 1
    config.updated_by_user_id = user.id
    db.execute(delete(AgentTopic).where(AgentTopic.tenant_id == context.id))
    db.execute(delete(AgentBehaviorRule).where(AgentBehaviorRule.tenant_id == context.id))
    for index, item in enumerate(payload.topics):
        db.add(AgentTopic(
            tenant_id=context.id, label=item.label, instructions=item.instructions, topic_type=item.topic_type,
            is_active=item.is_active, sort_order=index,
        ))
    for index, item in enumerate(payload.custom_rules):
        db.add(AgentBehaviorRule(
            tenant_id=context.id, rule_text=item.rule_text, is_active=item.is_active, sort_order=index,
        ))
    db.flush()
    updated = load_agent_bundle(db, context.id)
    _write_audit(db, updated, user)
    db.commit()
    return load_agent_bundle(db, context.id)


def update_knowledge(
    db: Session, context: TenantContext, user: UserContext, payload: AgentKnowledgeUpdate,
) -> AgentBundle:
    bundle = load_agent_bundle(db, context.id, for_update=True)
    _check_version(bundle.configuration, payload.expected_version)
    for key, value in payload.profile.model_dump().items():
        setattr(bundle.profile, key, value)
    bundle.configuration.version += 1
    bundle.configuration.updated_by_user_id = user.id
    for model in (AgentFaq, AgentKnowledgeService, AgentBusinessHours):
        db.execute(delete(model).where(model.tenant_id == context.id))
    for index, item in enumerate(payload.faqs):
        db.add(AgentFaq(
            tenant_id=context.id, question=item.question, answer=item.answer,
            is_active=item.is_active, sort_order=index,
        ))
    for index, item in enumerate(payload.services):
        db.add(AgentKnowledgeService(
            tenant_id=context.id, name=item.name, description=item.description,
            price_information=item.price_information, is_active=item.is_active, sort_order=index,
        ))
    for item in payload.business_hours:
        db.add(AgentBusinessHours(tenant_id=context.id, **item.model_dump()))
    db.flush()
    updated = load_agent_bundle(db, context.id)
    _write_audit(db, updated, user)
    db.commit()
    return load_agent_bundle(db, context.id)


def is_open_now(bundle: AgentBundle, timezone: str, now: datetime | None = None) -> bool:
    local_now = now.astimezone(ZoneInfo(timezone)) if now else datetime.now(ZoneInfo(timezone))
    hours = next((item for item in bundle.business_hours if item.weekday == local_now.weekday()), None)
    if hours is None or hours.is_closed:
        return False
    opens = time.fromisoformat(hours.opens_at)
    closes = time.fromisoformat(hours.closes_at)
    return opens <= local_now.time().replace(tzinfo=None) < closes
