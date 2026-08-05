from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import (
    TenantContext,
    UserContext,
    get_tenant_context,
    get_tenant_repository,
    get_user_context,
    require_agent_admin,
)
from app.api.v1.agent import router as agent_router
from app.api.v1.auth import router as auth_router
from app.api.v1.calendar import router as calendar_router
from app.api.v1.company import router as company_router
from app.api.v1.platform import router as platform_router
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import AgentConfiguration, Service
from app.repositories import TenantRepository
from app.schemas.api import (
    AppointmentResponse,
    HealthResponse,
    LocationResponse,
    PlatformStatusResponse,
    RealtimeAgentConfigResponse,
    RealtimeAttemptFinishRequest,
    RealtimeClientSecretResponse,
    RealtimeSessionBootstrapRequest,
    RealtimeSessionBootstrapResponse,
    ServiceResponse,
    ServiceWrite,
    StaffResponse,
    TenantResponse,
)
from app.services.realtime import (
    agent_config,
    create_client_secret,
    create_session_bootstrap,
    finish_attempt,
    mark_attempt_connected,
)

router = APIRouter()
router.include_router(auth_router)
router.include_router(agent_router)
router.include_router(calendar_router)
router.include_router(platform_router)
router.include_router(company_router)


def database_is_connected(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    if not database_is_connected(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_unavailable", "message": "Die Datenbank ist nicht erreichbar."},
        )
    return HealthResponse(status="healthy", database="connected")


@router.get("/platform/status", response_model=PlatformStatusResponse)
def platform_status(
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> PlatformStatusResponse:
    agent_configuration = db.query(AgentConfiguration).filter(AgentConfiguration.tenant_id == context.id).one_or_none()
    return PlatformStatusResponse(
        environment=settings.app_env,
        backend_version=settings.backend_version,
        realtime_voice_configured=bool(settings.openai_api_key),
        telephony_configured=settings.telephony_configured,
        calendar_configured=settings.any_calendar_provider_configured,
        database_connected=database_is_connected(db),
        realtime_model=settings.openai_realtime_model,
        realtime_voice=agent_configuration.voice if agent_configuration else settings.openai_realtime_voice,
    )


@router.get("/realtime/agent-config", response_model=RealtimeAgentConfigResponse)
def realtime_agent_config(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RealtimeAgentConfigResponse:
    return agent_config(context, settings, db)


@router.post("/realtime/client-secret", response_model=RealtimeClientSecretResponse)
async def realtime_client_secret(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RealtimeClientSecretResponse:
    return await create_client_secret(context, settings, db)


@router.post("/realtime/session-bootstrap", response_model=RealtimeSessionBootstrapResponse)
async def realtime_session_bootstrap(
    payload: RealtimeSessionBootstrapRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RealtimeSessionBootstrapResponse:
    return await create_session_bootstrap(
        context, settings, db, payload.call_attempt_id
    )


@router.post(
    "/realtime/call-attempts/{call_attempt_id}/connected",
    status_code=status.HTTP_204_NO_CONTENT,
)
def realtime_attempt_connected(
    call_attempt_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> None:
    mark_attempt_connected(db, context, call_attempt_id)


@router.post(
    "/realtime/call-attempts/{call_attempt_id}/finish",
    status_code=status.HTTP_204_NO_CONTENT,
)
def realtime_attempt_finished(
    call_attempt_id: UUID,
    payload: RealtimeAttemptFinishRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> None:
    finish_attempt(db, context, call_attempt_id, payload)


@router.get("/tenant", response_model=TenantResponse)
def tenant(context: TenantContext = Depends(get_tenant_context)) -> TenantResponse:
    primary = next((item for item in context.tenant.locations if item.is_primary), None)
    return TenantResponse(
        id=context.tenant.id,
        slug=context.tenant.slug,
        name=context.tenant.name,
        industry=context.tenant.industry,
        timezone=context.tenant.timezone,
        status=context.tenant.status.value,
        settings=context.tenant.settings,
        primary_location=LocationResponse.model_validate(primary) if primary else None,
    )


@router.get("/services", response_model=list[ServiceResponse])
def services(repo: TenantRepository = Depends(get_tenant_repository)) -> list[ServiceResponse]:
    return [ServiceResponse.model_validate(item) for item in repo.list_services()]


@router.post("/services", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(
    payload: ServiceWrite,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> ServiceResponse:
    item = Service(tenant_id=context.id, **payload.model_dump())
    db.add(item)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "service_name_conflict", "message": "Eine Leistung mit diesem Namen existiert bereits."},
        ) from exc
    db.refresh(item)
    return ServiceResponse.model_validate(item)


@router.put("/services/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: UUID,
    payload: ServiceWrite,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> ServiceResponse:
    item = db.scalar(select(Service).where(Service.id == service_id, Service.tenant_id == context.id))
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "service_not_found", "message": "Leistung nicht gefunden."})
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "service_name_conflict", "message": "Eine Leistung mit diesem Namen existiert bereits."},
        ) from exc
    db.refresh(item)
    return ServiceResponse.model_validate(item)


@router.get("/staff", response_model=list[StaffResponse])
def staff(repo: TenantRepository = Depends(get_tenant_repository)) -> list[StaffResponse]:
    return [StaffResponse.model_validate(item) for item in repo.list_staff()]


@router.get("/appointments", response_model=list[AppointmentResponse])
def appointments(repo: TenantRepository = Depends(get_tenant_repository)) -> list[AppointmentResponse]:
    return [AppointmentResponse.model_validate(item) for item in repo.list_appointments()]

