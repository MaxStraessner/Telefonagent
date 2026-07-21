from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import (
    TenantContext,
    UserContext,
    get_tenant_context,
    get_user_context,
    require_agent_admin,
)
from app.calendar.errors import CalendarError
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import (
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBookingSource,
    CalendarLocationType,
    CalendarProviderName,
)
from app.schemas.calendar import (
    AgentAvailabilityRequest,
    AppointmentTypeResponse,
    AppointmentTypeWrite,
    AvailabilityResponse,
    AvailabilitySearchRequest,
    BookingConfigurationResponse,
    BookingConfigurationUpdate,
    BookingDetailResponse,
    CalendarBookingCreate,
    CalendarBookingResponse,
    CalendarConnectionResponse,
    CalendarConnectionsOverview,
    CalendarSelectionUpdate,
    ConnectionTestResponse,
    ExternalCalendarResponse,
    OAuthStartResponse,
    ProviderConfigurationResponse,
)
from app.services.availability import AvailabilityService, aware_utc
from app.services.calendar_booking import CalendarBookingService
from app.services.calendar_configuration import (
    get_or_create_booking_configuration,
    update_booking_configuration,
    update_calendar_selection,
)
from app.services.calendar_connections import (
    disconnect_connection,
    get_connection,
    list_connection_calendars,
    provider_configuration,
    synchronize_calendars,
    test_connection,
)
from app.services.calendar_oauth import complete_oauth, consume_state, load_valid_state, start_oauth

router = APIRouter(prefix="/calendar", tags=["calendar"])


def provider_enum(value: str) -> CalendarProviderName:
    try:
        return CalendarProviderName(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "provider_not_configured", "message": "Unbekannter Kalenderanbieter."},
        ) from exc


def calendar_http_error(exc: CalendarError) -> HTTPException:
    status_by_code = {
        "tenant_access_denied": status.HTTP_403_FORBIDDEN,
        "provider_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
        "provider_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "provider_rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
        "oauth_state_invalid": status.HTTP_400_BAD_REQUEST,
        "slot_no_longer_available": status.HTTP_409_CONFLICT,
        "duplicate_booking": status.HTTP_409_CONFLICT,
        "calendar_event_creation_failed": status.HTTP_502_BAD_GATEWAY,
        "reauthorization_required": status.HTTP_409_CONFLICT,
    }
    return HTTPException(
        status_code=status_by_code.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": exc.message},
    )


def connection_response(db: Session, context: TenantContext, connection) -> CalendarConnectionResponse:
    return CalendarConnectionResponse.model_validate(
        {
            **{column.name: getattr(connection, column.name) for column in connection.__table__.columns},
            "calendars": [
                ExternalCalendarResponse.model_validate(item)
                for item in list_connection_calendars(db, context.id, connection.id)
            ],
        }
    )


def configuration_response(configuration, hours) -> BookingConfigurationResponse:
    return BookingConfigurationResponse(
        id=configuration.id,
        tenant_id=configuration.tenant_id,
        timezone=configuration.timezone,
        slot_interval_minutes=configuration.slot_interval_minutes,
        minimum_notice_minutes=configuration.minimum_notice_minutes,
        maximum_booking_horizon_days=configuration.maximum_booking_horizon_days,
        buffer_before_minutes=configuration.buffer_before_minutes,
        buffer_after_minutes=configuration.buffer_after_minutes,
        maximum_suggestions_per_request=configuration.maximum_suggestions_per_request,
        business_hours=hours,
        updated_at=configuration.updated_at,
    )


def booking_api_response(result) -> CalendarBookingResponse:
    if result.booking is None:
        return CalendarBookingResponse(
            success=False,
            error_code=result.error_code,
            message=result.message,
            alternative_slots=result.alternative_slots or [],
        )
    booking = result.booking
    return CalendarBookingResponse(
        success=True,
        booking_id=booking.id,
        status=booking.status.value,
        start=aware_utc(booking.start_at),
        end=aware_utc(booking.end_at),
        timezone=booking.timezone,
    )


@router.get("/connections", response_model=CalendarConnectionsOverview)
def connections(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarConnectionsOverview:
    from app.models import CalendarConnection

    items = list(
        db.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.tenant_id == context.id)
            .order_by(CalendarConnection.provider, CalendarConnection.account_email)
        )
    )
    return CalendarConnectionsOverview(
        providers=[ProviderConfigurationResponse(**item) for item in provider_configuration(settings)],
        connections=[connection_response(db, context, item) for item in items],
    )


@router.post("/oauth/{provider}/start", response_model=OAuthStartResponse)
def oauth_start(
    provider: str,
    context: TenantContext = Depends(get_tenant_context),
    user: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OAuthStartResponse:
    try:
        authorization_url, expires_at = start_oauth(db, context.id, user.id, provider_enum(provider), settings)
        return OAuthStartResponse(authorization_url=authorization_url, expires_at=expires_at)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.get("/oauth/{provider}/callback", include_in_schema=True)
async def oauth_callback(
    provider: str,
    state_value: str = Query(alias="state"),
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    provider_name = provider_enum(provider)
    frontend = f"{settings.frontend_url.rstrip('/')}/kalender"
    try:
        if error:
            oauth_state = load_valid_state(db, provider_name, state_value)
            consume_state(db, oauth_state)
            raise CalendarError("oauth_access_denied", "Die Kalenderfreigabe wurde nicht erteilt.")
        if not code:
            raise CalendarError("oauth_access_denied", "Der Kalenderanbieter hat keinen Autorisierungscode geliefert.")
        connection = await complete_oauth(db, provider_name, state_value, code, settings)
        await synchronize_calendars(db, connection, settings)
        query = urlencode({"calendar_oauth": "success", "provider": provider_name.value})
    except CalendarError as exc:
        query = urlencode({"calendar_oauth": "error", "error_code": exc.code})
    return RedirectResponse(f"{frontend}?{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/connections/{connection_id}/test", response_model=ConnectionTestResponse)
async def connection_test(
    connection_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConnectionTestResponse:
    try:
        connection = get_connection(db, context.id, connection_id)
        found, read, checked_from, checked_until = await test_connection(db, connection, settings)
        return ConnectionTestResponse(
            success=True,
            calendars_found=found,
            availability_calendars_read=read,
            checked_from=checked_from,
            checked_until=checked_until,
        )
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        await disconnect_connection(db, get_connection(db, context.id, connection_id), settings)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.get("/connections/{connection_id}/calendars", response_model=list[ExternalCalendarResponse])
async def calendars(
    connection_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ExternalCalendarResponse]:
    try:
        items = await synchronize_calendars(db, get_connection(db, context.id, connection_id), settings)
        return [ExternalCalendarResponse.model_validate(item) for item in items]
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.put("/configuration/calendars", response_model=list[ExternalCalendarResponse])
def save_calendar_selection(
    payload: CalendarSelectionUpdate,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> list[ExternalCalendarResponse]:
    try:
        return [ExternalCalendarResponse.model_validate(item) for item in update_calendar_selection(db, context.id, payload)]
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.get("/configuration", response_model=BookingConfigurationResponse)
def get_configuration(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> BookingConfigurationResponse:
    return configuration_response(*get_or_create_booking_configuration(db, context.id, context.tenant.timezone))


@router.put("/configuration", response_model=BookingConfigurationResponse)
def save_configuration(
    payload: BookingConfigurationUpdate,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> BookingConfigurationResponse:
    try:
        return configuration_response(
            *update_booking_configuration(db, context.id, context.tenant.timezone, payload)
        )
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.get("/appointment-types", response_model=list[AppointmentTypeResponse])
def appointment_types(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> list[AppointmentTypeResponse]:
    items = list(
        db.scalars(
            select(CalendarAppointmentType)
            .where(CalendarAppointmentType.tenant_id == context.id)
            .order_by(CalendarAppointmentType.name)
        )
    )
    return [AppointmentTypeResponse.model_validate(item) for item in items]


@router.post("/appointment-types", response_model=AppointmentTypeResponse, status_code=status.HTTP_201_CREATED)
def create_appointment_type(
    payload: AppointmentTypeWrite,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> AppointmentTypeResponse:
    item = CalendarAppointmentType(
        tenant_id=context.id,
        **payload.model_dump(exclude={"location_type"}),
        location_type=CalendarLocationType(payload.location_type),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_appointment_type", "message": "Eine Terminart mit diesem Namen existiert bereits."},
        ) from exc
    db.refresh(item)
    return AppointmentTypeResponse.model_validate(item)


def tenant_appointment_type(db: Session, tenant_id: UUID, appointment_type_id: UUID) -> CalendarAppointmentType:
    item = db.scalar(
        select(CalendarAppointmentType).where(
            CalendarAppointmentType.id == appointment_type_id,
            CalendarAppointmentType.tenant_id == tenant_id,
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invalid_appointment_type", "message": "Die Terminart wurde nicht gefunden."},
        )
    return item


@router.put("/appointment-types/{appointment_type_id}", response_model=AppointmentTypeResponse)
def update_appointment_type(
    appointment_type_id: UUID,
    payload: AppointmentTypeWrite,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> AppointmentTypeResponse:
    item = tenant_appointment_type(db, context.id, appointment_type_id)
    for field, value in payload.model_dump(exclude={"location_type"}).items():
        setattr(item, field, value)
    item.location_type = CalendarLocationType(payload.location_type)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_appointment_type", "message": "Eine Terminart mit diesem Namen existiert bereits."},
        ) from exc
    db.refresh(item)
    return AppointmentTypeResponse.model_validate(item)


@router.delete("/appointment-types/{appointment_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment_type(
    appointment_type_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> None:
    item = tenant_appointment_type(db, context.id, appointment_type_id)
    db.delete(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "appointment_type_in_use", "message": "Diese Terminart wird bereits von Buchungen verwendet und kann nur deaktiviert werden."},
        ) from exc


@router.post("/availability/search", response_model=AvailabilityResponse)
async def search_availability(
    payload: AvailabilitySearchRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AvailabilityResponse:
    try:
        timezone_name, slots = await AvailabilityService(
            db, settings, context.id, context.tenant.timezone
        ).search(
            payload.appointment_type_id,
            payload.search_start,
            payload.search_end,
            preferred_day=payload.preferred_day,
            preferred_time_range=payload.preferred_time_range,
            maximum_results=payload.maximum_results,
        )
        return AvailabilityResponse(timezone=timezone_name, slots=slots)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


async def agent_search(payload: AgentAvailabilityRequest, context, db, settings) -> AvailabilityResponse:
    try:
        configuration, _ = get_or_create_booking_configuration(db, context.id, context.tenant.timezone)
        zone = ZoneInfo(configuration.timezone)
        if payload.preferred_date:
            start = datetime.combine(payload.preferred_date, time.min, tzinfo=zone)
        else:
            start = datetime.now(zone)
        end = start + timedelta(days=payload.search_days)
        timezone_name, slots = await AvailabilityService(
            db, settings, context.id, context.tenant.timezone
        ).search(
            payload.appointment_type_id,
            start,
            end,
            preferred_day=payload.preferred_date,
            preferred_time_range=payload.preferred_time_of_day,
            maximum_results=3,
        )
        return AvailabilityResponse(timezone=timezone_name, slots=slots)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.post("/bookings", response_model=CalendarBookingResponse)
async def create_booking(
    payload: CalendarBookingCreate,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarBookingResponse:
    try:
        result = await CalendarBookingService(db, settings, context.id, context.tenant.timezone).create(
            payload, source=CalendarBookingSource.admin_api
        )
        return booking_api_response(result)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.get("/bookings/{booking_id}", response_model=BookingDetailResponse)
def get_booking(
    booking_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> BookingDetailResponse:
    booking = db.scalar(
        select(CalendarBooking).where(
            CalendarBooking.id == booking_id,
            CalendarBooking.tenant_id == context.id,
        )
    )
    if booking is None:
        raise HTTPException(status_code=404, detail={"code": "tenant_access_denied", "message": "Buchung nicht gefunden."})
    return BookingDetailResponse.model_validate(booking, from_attributes=True)


@router.get("/tools/list-appointment-types", response_model=dict)
def tool_list_appointment_types(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> dict:
    items = list(
        db.scalars(
            select(CalendarAppointmentType).where(
                CalendarAppointmentType.tenant_id == context.id,
                CalendarAppointmentType.is_active.is_(True),
            ).order_by(CalendarAppointmentType.name)
        )
    )
    return {
        "success": True,
        "appointment_types": [
            {"id": str(item.id), "name": item.name, "duration_minutes": item.duration_minutes, "description": item.description}
            for item in items
        ],
    }


@router.post("/tools/find-available-appointments", response_model=AvailabilityResponse)
async def tool_find_available_appointments(
    payload: AgentAvailabilityRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AvailabilityResponse:
    return await agent_search(payload, context, db, settings)


@router.post("/tools/create-calendar-booking", response_model=CalendarBookingResponse)
async def tool_create_calendar_booking(
    payload: CalendarBookingCreate,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarBookingResponse:
    try:
        result = await CalendarBookingService(db, settings, context.id, context.tenant.timezone).create(payload)
        return booking_api_response(result)
    except CalendarError as exc:
        return CalendarBookingResponse(success=False, error_code=exc.code, message=exc.message)
