import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    TenantContext,
    UserContext,
    get_tenant_context,
    get_user_context,
    require_agent_admin,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.agent_config import (
    AgentCatalogResponse,
    AgentConfigurationResponse,
    AgentConfigurationUpdate,
    AgentKnowledgeResponse,
    AgentKnowledgeUpdate,
    CapabilityResponse,
    PromptPreviewResponse,
    RuntimeSummaryResponse,
    VoiceOptionResponse,
    VoicePreviewRequest,
)
from app.services.agent_configuration import (
    configuration_response,
    knowledge_response,
    load_agent_bundle,
    update_configuration,
    update_knowledge,
)
from app.services.agent_runtime import build_runtime_config
from app.services.capabilities import active_capabilities
from app.services.prompt_compiler import compile_pronunciation_instruction
from app.services.rate_limit import enforce_rate_limit
from app.services.realtime import build_safety_identifier

router = APIRouter(prefix="/agent", tags=["agent-configuration"])
logger = logging.getLogger(__name__)
AUDIO_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
VOICE_OPTIONS = [
    ("marin", "Marin", True), ("cedar", "Cedar", True), ("coral", "Coral", False),
    ("sage", "Sage", False), ("alloy", "Alloy", False), ("ash", "Ash", False),
    ("ballad", "Ballad", False), ("echo", "Echo", False),
    ("shimmer", "Shimmer", False), ("verse", "Verse", False),
]


@router.get("/config", response_model=AgentConfigurationResponse)
def get_configuration(
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> AgentConfigurationResponse:
    return configuration_response(load_agent_bundle(db, context.id), user)


@router.put("/config", response_model=AgentConfigurationResponse)
def put_configuration(
    payload: AgentConfigurationUpdate,
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> AgentConfigurationResponse:
    enforce_rate_limit(f"agent-config:{context.id}:{user.id}", limit=20)
    return configuration_response(update_configuration(db, context, user, payload), user)


@router.get("/knowledge", response_model=AgentKnowledgeResponse)
def get_knowledge(
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> AgentKnowledgeResponse:
    return knowledge_response(load_agent_bundle(db, context.id), user)


@router.put("/knowledge", response_model=AgentKnowledgeResponse)
def put_knowledge(
    payload: AgentKnowledgeUpdate,
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> AgentKnowledgeResponse:
    enforce_rate_limit(f"agent-knowledge:{context.id}:{user.id}", limit=20)
    return knowledge_response(update_knowledge(db, context, user, payload), user)


@router.get("/catalog", response_model=AgentCatalogResponse)
def get_catalog(_user: UserContext = Depends(get_user_context)) -> AgentCatalogResponse:
    return AgentCatalogResponse(
        voices=[VoiceOptionResponse(value=value, label=label, recommended=recommended) for value, label, recommended in VOICE_OPTIONS],
        capabilities=[],
    )


@router.get("/capabilities", response_model=list[CapabilityResponse])
def get_capabilities(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> list[CapabilityResponse]:
    return [
        {
            "key": item.key, "label": item.display_name, "description": item.description,
            "available": True, "active": True, "unavailable_reason": None,
        }
        for item in active_capabilities(load_agent_bundle(db, context.id))
    ]


@router.post("/test-session", response_model=RuntimeSummaryResponse)
def test_session_summary(
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RuntimeSummaryResponse:
    enforce_rate_limit(f"agent-test:{context.id}:{user.id}", limit=30)
    runtime = build_runtime_config(db, context, settings, test_mode=True)
    return RuntimeSummaryResponse(
        tenant_id=context.id, configuration_version=runtime.version,
        company_name=runtime.bundle.configuration.company_name,
        assistant_name=runtime.bundle.configuration.assistant_name,
        language=runtime.bundle.configuration.language,
        style=runtime.bundle.configuration.tone,
        business_hours_status=runtime.business_hours_status,
        model=runtime.model, voice=runtime.voice, speed=runtime.speed,
        turn_detection=runtime.turn_detection,
        capability_keys=runtime.capability_keys,
        tool_names=[str(item.get("name", "")) for item in runtime.tools], greeting=runtime.greeting,
        prompt_sections=runtime.prompt_sections,
    )


@router.get("/prompt-preview", response_model=PromptPreviewResponse)
def prompt_preview(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PromptPreviewResponse:
    runtime = build_runtime_config(db, context, settings, test_mode=True)
    return PromptPreviewResponse(
        configuration_version=runtime.version, prompt=runtime.prompt, sections=runtime.prompt_sections,
    )


@router.post("/voice-preview")
async def voice_preview(
    payload: VoicePreviewRequest,
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(require_agent_admin),
    settings: Settings = Depends(get_settings),
) -> Response:
    enforce_rate_limit(f"voice-preview:{context.id}:{user.id}", limit=10)
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "voice_preview_not_configured", "message": "Die Stimmprobe ist serverseitig noch nicht konfiguriert."},
        )
    request_payload = {
        "model": "gpt-4o-mini-tts", "voice": payload.voice, "speed": payload.speed,
        "input": payload.text, "response_format": "mp3",
        "instructions": compile_pronunciation_instruction(
            payload.pronunciation_style,
            payload.regional_accent,
            payload.pronunciation_instructions,
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=4.0)) as client:
            upstream = await client.post(
                AUDIO_SPEECH_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "OpenAI-Safety-Identifier": build_safety_identifier(context, settings),
                },
                json=request_payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "voice_preview_timeout", "message": "Die Stimmprobe antwortet nicht rechtzeitig."},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "voice_preview_unavailable", "message": "Die Stimmprobe ist derzeit nicht erreichbar."},
        ) from exc
    if not upstream.is_success:
        logger.warning("OpenAI voice preview rejected", extra={"provider_status": upstream.status_code})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "voice_preview_rejected", "message": "Die gewählte Stimme konnte nicht wiedergegeben werden."},
        )
    return Response(content=upstream.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
