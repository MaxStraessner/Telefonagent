from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext, get_tenant_context, get_tenant_repository
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.repositories import TenantRepository
from app.schemas.api import (
    AppointmentResponse, HealthResponse, LocationResponse, PlatformStatusResponse,
    RealtimeAgentConfigResponse, RealtimeClientSecretResponse, ServiceResponse,
    StaffResponse, TenantResponse,
)
from app.services.realtime import agent_config, create_client_secret

router = APIRouter()


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
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> PlatformStatusResponse:
    return PlatformStatusResponse(
        environment=settings.app_env,
        backend_version=settings.backend_version,
        realtime_voice_configured=bool(settings.openai_api_key),
        telephony_configured=settings.telephony_configured,
        calendar_configured=settings.calendar_configured,
        database_connected=database_is_connected(db),
        realtime_model=settings.openai_realtime_model,
        realtime_voice=settings.openai_realtime_voice,
    )


@router.get("/realtime/agent-config", response_model=RealtimeAgentConfigResponse)
def realtime_agent_config(
    context: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings),
) -> RealtimeAgentConfigResponse:
    return agent_config(context, settings)


@router.post("/realtime/client-secret", response_model=RealtimeClientSecretResponse)
async def realtime_client_secret(
    context: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings),
) -> RealtimeClientSecretResponse:
    return await create_client_secret(context, settings)


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


@router.get("/staff", response_model=list[StaffResponse])
def staff(repo: TenantRepository = Depends(get_tenant_repository)) -> list[StaffResponse]:
    return [StaffResponse.model_validate(item) for item in repo.list_staff()]


@router.get("/appointments", response_model=list[AppointmentResponse])
def appointments(repo: TenantRepository = Depends(get_tenant_repository)) -> list[AppointmentResponse]:
    return [AppointmentResponse.model_validate(item) for item in repo.list_appointments()]

