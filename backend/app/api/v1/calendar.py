from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

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
    BookingState,
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBookingSource,
    CalendarLocationType,
    CalendarProviderName,
    Service,
)
from app.schemas.calendar import (
    AgentAppointmentCreate,
    AgentAvailabilityRequest,
    AlternativeSlotsRequest,
    AppointmentTypeResponse,
    AppointmentTypeWrite,
    AvailabilityResponse,
    AvailabilitySearchRequest,
    BookingConfigurationResponse,
    BookingConfigurationUpdate,
    BookingDetailResponse,
    CalendarAgendaResponse,
    CalendarBookingCreate,
    CalendarBookingResponse,
    CalendarConnectionResponse,
    CalendarConnectionsOverview,
    CalendarSelectionUpdate,
    ConnectionTestResponse,
    ConversationBootstrapRequest,
    ConversationBootstrapResponse,
    ExactAvailabilityRequest,
    ExactAvailabilityResponse,
    ExternalCalendarResponse,
    FinalizeAppointmentRequest,
    ListBookableServicesRequest,
    OAuthStartResponse,
    ProviderConfigurationResponse,
    ResolveBookingDateTimeRequest,
    ResolveBookingDateTimeResponse,
    ResolveServiceRequest,
    SnapshotAvailabilityRequest,
    SnapshotAvailabilityResponse,
)
from app.services.availability import AvailabilityService, SlotSigner, aware_utc
from app.services.availability_snapshot import AvailabilitySnapshotService
from app.services.booking_orchestrator import AppointmentBookingOrchestrator
from app.services.calendar_agenda import calendar_agenda
from app.services.calendar_booking import BookingServiceResult, CalendarBookingService
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
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.german_datetime import resolve_german_datetime
from app.services.tool_audit import ToolAudit

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
        "local_confirmation_failed": status.HTTP_502_BAD_GATEWAY,
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
        external_event_id=booking.external_event_id,
        calendar_name=booking.calendar_name_snapshot,
        service_name=booking.service_name_snapshot,
    )


def appointment_type_response(item: CalendarAppointmentType) -> AppointmentTypeResponse:
    return AppointmentTypeResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        service_id=item.service_id,
        name=item.service.name,
        service_name=item.service.name,
        description=item.service.description,
        duration_minutes=item.service.duration_minutes,
        buffer_before_minutes=item.buffer_before_minutes,
        buffer_after_minutes=item.buffer_after_minutes,
        location_type=item.location_type.value,
        location_text=item.location_text,
        is_active=item.is_active,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def active_tenant_service(db: Session, tenant_id: UUID, service_id: UUID) -> Service:
    service = db.scalar(
        select(Service).where(Service.id == service_id, Service.tenant_id == tenant_id, Service.is_active.is_(True))
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_service", "message": "Die Leistung ist ungültig oder nicht aktiv."},
        )
    return service


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
            .options(selectinload(CalendarAppointmentType.service))
            .order_by(CalendarAppointmentType.name)
        )
    )
    return [appointment_type_response(item) for item in items]


@router.post("/appointment-types", response_model=AppointmentTypeResponse, status_code=status.HTTP_201_CREATED)
def create_appointment_type(
    payload: AppointmentTypeWrite,
    context: TenantContext = Depends(get_tenant_context),
    _admin: UserContext = Depends(require_agent_admin),
    db: Session = Depends(get_db),
) -> AppointmentTypeResponse:
    service = active_tenant_service(db, context.id, payload.service_id)
    item = CalendarAppointmentType(
        tenant_id=context.id,
        name=service.name,
        description=service.description,
        duration_minutes=service.duration_minutes,
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
    item.service = service
    return appointment_type_response(item)


def tenant_appointment_type(db: Session, tenant_id: UUID, appointment_type_id: UUID) -> CalendarAppointmentType:
    item = db.scalar(
        select(CalendarAppointmentType).where(
            CalendarAppointmentType.id == appointment_type_id,
            CalendarAppointmentType.tenant_id == tenant_id,
        ).options(selectinload(CalendarAppointmentType.service))
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
    service = active_tenant_service(db, context.id, payload.service_id)
    for field, value in payload.model_dump(exclude={"location_type"}).items():
        setattr(item, field, value)
    item.name = service.name
    item.description = service.description
    item.duration_minutes = service.duration_minutes
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
    item.service = service
    return appointment_type_response(item)


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


async def exact_availability_result(payload: ExactAvailabilityRequest, context, db, settings) -> ExactAvailabilityResponse:
    availability = AvailabilityService(db, settings, context.id, context.tenant.timezone)
    configuration, _hours, appointment_type = availability.load_rules(payload.appointment_type_id)
    if appointment_type.service_id != payload.service_id:
        raise CalendarError("invalid_service", "Leistung und Terminart gehören nicht zusammen.")
    if payload.timezone != configuration.timezone:
        raise CalendarError("invalid_timezone", "Die angegebene Zeitzone entspricht nicht der Buchungskonfiguration.")
    start = aware_utc(payload.requested_start)
    end = start + timedelta(minutes=appointment_type.service.duration_minutes)
    before = appointment_type.buffer_before_minutes
    if before is None:
        before = configuration.buffer_before_minutes
    after = appointment_type.buffer_after_minutes
    if after is None:
        after = configuration.buffer_after_minutes
    available = await availability.exact_slot_available(appointment_type.id, start, end)
    alternatives = []
    slot_id = None
    if available:
        slot_id = SlotSigner(settings).sign(context.id, appointment_type.id, start, end)
    else:
        _timezone_name, alternatives = await availability.search(
            appointment_type.id, start, start + timedelta(days=7), maximum_results=3
        )
    zone = ZoneInfo(configuration.timezone)
    return ExactAvailabilityResponse(
        available=available,
        appointment_start=start.astimezone(zone),
        appointment_end=end.astimezone(zone),
        blocked_start=(start - timedelta(minutes=before)).astimezone(zone),
        blocked_end=(end + timedelta(minutes=after)).astimezone(zone),
        slot_id=slot_id,
        reason=None if available else "calendar_conflict",
        alternatives=alternatives,
    )


@router.post("/tools/check-appointment-availability", response_model=ExactAvailabilityResponse)
async def tool_check_appointment_availability(
    payload: ExactAvailabilityRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ExactAvailabilityResponse:
    try:
        return await exact_availability_result(payload, context, db, settings)
    except CalendarError as exc:
        raise calendar_http_error(exc) from exc


@router.post("/tools/create-appointment", response_model=CalendarBookingResponse)
async def tool_create_appointment(
    payload: AgentAppointmentCreate,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarBookingResponse:
    if not payload.confirmed:
        return CalendarBookingResponse(
            success=False,
            error_code="confirmation_required",
            message="Der Termin darf erst nach ausdrücklicher Bestätigung gebucht werden.",
        )
    try:
        booking_service = CalendarBookingService(db, settings, context.id, context.tenant.timezone)
        existing = booking_service.existing_for_key(payload.idempotency_key)
        if existing is not None and existing.status.value == "confirmed":
            return booking_api_response(BookingServiceResult(existing))
        exact = await exact_availability_result(
            ExactAvailabilityRequest(
                service_id=payload.service_id,
                appointment_type_id=payload.appointment_type_id,
                requested_start=payload.start_at,
                timezone=payload.timezone,
            ),
            context,
            db,
            settings,
        )
        if not exact.available or not exact.slot_id:
            return CalendarBookingResponse(
                success=False,
                error_code="slot_no_longer_available",
                message="Der ausgewählte Termin ist inzwischen belegt.",
                alternative_slots=exact.alternatives,
            )
        result = await booking_service.create(
            CalendarBookingCreate(
                slot_id=exact.slot_id,
                service_id=payload.service_id,
                appointment_type_id=payload.appointment_type_id,
                customer_name=payload.customer_name,
                customer_phone=payload.customer_phone or "",
                customer_email=payload.customer_email or "",
                customer_notes="",
                idempotency_key=payload.idempotency_key,
            )
        )
        return booking_api_response(result)
    except CalendarError as exc:
        return CalendarBookingResponse(success=False, error_code=exc.code, message=exc.message)


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


@router.get("/appointments", response_model=CalendarAgendaResponse)
async def appointments_agenda(
    start: datetime,
    end: datetime,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarAgendaResponse:
    if aware_utc(end) <= aware_utc(start) or aware_utc(end) - aware_utc(start) > timedelta(days=93):
        raise HTTPException(status_code=400, detail={"code": "invalid_range", "message": "Ungültiger Terminzeitraum."})
    return await calendar_agenda(db, settings, context.id, start, end)


@router.get("/tools/list-bookable-services", response_model=dict)
@router.get("/tools/list-appointment-types", response_model=dict, include_in_schema=False)
def tool_list_appointment_types(
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> dict:
    return bookable_services(db, context.id)


def bookable_services(db: Session, tenant_id: UUID) -> dict:
    items = list(
        db.scalars(
            select(CalendarAppointmentType).where(
                CalendarAppointmentType.tenant_id == tenant_id,
                CalendarAppointmentType.is_active.is_(True),
                Service.is_active.is_(True),
            ).join(Service, Service.id == CalendarAppointmentType.service_id).options(
                selectinload(CalendarAppointmentType.service)
            ).order_by(Service.name)
        )
    )
    grouped: dict[str, dict] = {}
    for item in items:
        key = str(item.service_id)
        grouped.setdefault(key, {
            "service_id": key,
            "name": item.service.name,
            "description": item.service.description,
            "duration_minutes": item.service.duration_minutes,
            "appointment_types": [],
        })["appointment_types"].append({
            "appointment_type_id": str(item.id),
            "appointment_format": item.location_type.value,
            "location": item.location_text,
            "buffer_before_minutes": item.buffer_before_minutes or 0,
            "buffer_after_minutes": item.buffer_after_minutes or 0,
        })
    return {"success": True, "services": list(grouped.values())}


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


@router.post("/conversation/bootstrap", response_model=ConversationBootstrapResponse)
async def bootstrap_booking_conversation(
    payload: ConversationBootstrapRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConversationBootstrapResponse:
    orchestrator = ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    orchestrator.bootstrap_started()
    db.commit()
    catalog_available = bool(bookable_services(db, context.id)["services"])
    snapshot_status = "ready"
    error_code = None
    try:
        await AvailabilitySnapshotService(
            db, settings, context.id, payload.session_id, context.tenant.timezone
        ).refresh()
    except CalendarError as exc:
        snapshot_status = "unavailable"
        error_code = exc.code
    orchestrator.bootstrap_completed(catalog_available=catalog_available)
    current = orchestrator.commit()
    return ConversationBootstrapResponse(
        success=True, state=current.state.value, snapshot_status=snapshot_status, error_code=error_code
    )


@router.post("/tools/list-bookable-services", response_model=dict)
def conversation_list_bookable_services(
    payload: ListBookableServicesRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> dict:
    ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    audit = ToolAudit(db, context.id, payload.session_id, payload.tool_call_id, "list_bookable_services")
    try:
        result = bookable_services(db, context.id)
        audit.complete(success=True)
        return result
    except Exception:
        audit.complete(success=False, error_code="catalog_unavailable")
        raise


@router.post("/tools/resolve-service", response_model=dict)
def conversation_resolve_service(
    payload: ResolveServiceRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> dict:
    orchestrator = ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    audit = ToolAudit(db, context.id, payload.session_id, payload.tool_call_id, "resolve_service")
    normalized = payload.service_name.strip().casefold()
    candidates = list(db.scalars(select(Service).where(
        Service.tenant_id == context.id, Service.is_active.is_(True)
    ).order_by(Service.name)))
    exact = [item for item in candidates if item.name.casefold() == normalized]
    partial = [item for item in candidates if normalized in item.name.casefold() or item.name.casefold() in normalized]
    matches = exact or partial
    if len(matches) != 1:
        audit.complete(success=False, error_code="service_not_unique")
        return {"success": False, "error_code": "service_not_unique", "matches": [
            {"service_id": str(item.id), "name": item.name} for item in matches[:5]
        ]}
    service = matches[0]
    appointment_types = list(db.scalars(select(CalendarAppointmentType).where(
        CalendarAppointmentType.tenant_id == context.id,
        CalendarAppointmentType.service_id == service.id,
        CalendarAppointmentType.is_active.is_(True),
    )))
    appointment_type_id = appointment_types[0].id if len(appointment_types) == 1 else None
    orchestrator.select_service(service.id, service.name, appointment_type_id)
    db.commit()
    audit.complete(success=True)
    return {
        "success": True, "service_id": str(service.id), "name": service.name,
        "appointment_types": [{"appointment_type_id": str(item.id), "appointment_format": item.location_type.value}
                              for item in appointment_types],
    }


@router.post(
    "/tools/resolve-booking-datetime",
    response_model=ResolveBookingDateTimeResponse,
)
def conversation_resolve_booking_datetime(
    payload: ResolveBookingDateTimeRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
) -> ResolveBookingDateTimeResponse:
    orchestrator = ConversationOrchestrator(
        db,
        context.id,
        payload.session_id,
        context.tenant.timezone,
    )
    audit = ToolAudit(
        db,
        context.id,
        payload.session_id,
        payload.tool_call_id,
        "resolve_booking_datetime",
    )
    if orchestrator.context.state == BookingState.date_time_required:
        orchestrator.transition(BookingState.date_time_resolving)
    if orchestrator.context.state != BookingState.date_time_resolving:
        audit.complete(success=False, error_code="service_required")
        raise calendar_http_error(
            CalendarError(
                "service_required",
                "Vor der Datumsauflösung muss eine Leistung ausgewählt werden.",
            )
        )
    configuration, _hours = get_or_create_booking_configuration(
        db,
        context.id,
        context.tenant.timezone,
    )
    resolution = resolve_german_datetime(
        payload.expression,
        now=datetime.now(timezone.utc),
        timezone_name=context.tenant.timezone,
        horizon_days=configuration.maximum_booking_horizon_days,
    )
    booking = orchestrator.context
    booking.datetime_resolution_status = resolution.status.value
    booking.datetime_resolution_version += 1
    booking.datetime_explicit_year = resolution.explicit_year
    booking.requested_start = aware_utc(resolution.start) if resolution.start else None
    booking.requested_end = aware_utc(resolution.end) if resolution.end else None
    db.commit()
    audit.complete(
        success=resolution.status.value in {"concrete", "search_window"},
        error_code=None
        if resolution.status.value in {"concrete", "search_window"}
        else resolution.reason,
    )
    return ResolveBookingDateTimeResponse(
        status=resolution.status.value,
        timezone=context.tenant.timezone,
        start=resolution.start,
        end=resolution.end,
        speech=resolution.speech,
        reason=resolution.reason,
        explicit_year=resolution.explicit_year,
        resolution_version=booking.datetime_resolution_version,
    )


@router.post("/tools/check-appointment-availability/session", response_model=SnapshotAvailabilityResponse)
async def conversation_check_availability(
    payload: SnapshotAvailabilityRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SnapshotAvailabilityResponse:
    orchestrator = ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    audit = ToolAudit(db, context.id, payload.session_id, payload.tool_call_id, "check_appointment_availability")
    service = active_tenant_service(db, context.id, payload.service_id)
    if orchestrator.context.state in {BookingState.ready, BookingState.service_required, BookingState.service_selected}:
        if orchestrator.context.state != BookingState.service_selected:
            orchestrator.select_service(payload.service_id, service.name, payload.appointment_type_id)
        elif orchestrator.context.appointment_type_id is None:
            orchestrator.context.appointment_type_id = payload.appointment_type_id
            orchestrator.transition(BookingState.date_time_required)
    orchestrator.context.requested_start = aware_utc(payload.requested_start)
    orchestrator.transition(BookingState.availability_checking)
    appointment_type = db.scalar(select(CalendarAppointmentType).where(
        CalendarAppointmentType.id == payload.appointment_type_id,
        CalendarAppointmentType.tenant_id == context.id,
        CalendarAppointmentType.service_id == payload.service_id,
    ).options(selectinload(CalendarAppointmentType.service)))
    if appointment_type is None or appointment_type.service is None:
        audit.complete(success=False, error_code="invalid_appointment_type")
        raise calendar_http_error(CalendarError("invalid_appointment_type", "Die Terminart ist ungültig."))
    requested_end = aware_utc(payload.requested_start) + timedelta(minutes=appointment_type.service.duration_minutes)
    snapshot_service = AvailabilitySnapshotService(
        db, settings, context.id, payload.session_id, context.tenant.timezone
    )
    configuration, _hours, _configured_type = snapshot_service.availability.load_rules(payload.appointment_type_id)
    timezone_name, exact_candidates, refreshed = await snapshot_service.search(
        payload.appointment_type_id,
        payload.requested_start - timedelta(minutes=configuration.slot_interval_minutes),
        requested_end + timedelta(minutes=configuration.slot_interval_minutes),
        maximum_results=10,
    )
    exact = next((slot for slot in exact_candidates if aware_utc(slot.start) == aware_utc(payload.requested_start)), None)
    available = exact is not None
    alternatives = []
    if not available:
        timezone_name, alternatives, alternatives_refreshed = await snapshot_service.search(
            payload.appointment_type_id,
            payload.requested_start,
            requested_end + timedelta(days=7),
            maximum_results=3,
        )
        refreshed = refreshed or alternatives_refreshed
        alternatives.sort(
            key=lambda slot: abs((aware_utc(slot.start) - aware_utc(payload.requested_start)).total_seconds())
        )
    orchestrator.context.requested_end = requested_end
    if available:
        orchestrator.context.selected_slot_start = aware_utc(exact.start)
        orchestrator.context.selected_slot_end = aware_utc(exact.end)
    orchestrator.transition(BookingState.slot_available if available else BookingState.slot_unavailable)
    db.commit()
    audit.complete(success=True)
    return SnapshotAvailabilityResponse(
        available=available, appointment_start=aware_utc(payload.requested_start), appointment_end=requested_end,
        blocked_start=aware_utc(payload.requested_start), blocked_end=requested_end,
        slot_id=exact.slot_id if exact else None, reason=None if available else "slot_unavailable",
        alternatives=[] if available else alternatives[:3], source="targeted_refresh" if refreshed else "snapshot",
        timezone=timezone_name,
    )


@router.post("/tools/find-alternative-slots", response_model=AvailabilityResponse)
async def conversation_find_alternatives(
    payload: AlternativeSlotsRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AvailabilityResponse:
    ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    audit = ToolAudit(db, context.id, payload.session_id, payload.tool_call_id, "find_alternative_slots")
    timezone_name, slots, _refreshed = await AvailabilitySnapshotService(
        db, settings, context.id, payload.session_id, context.tenant.timezone
    ).search(
        payload.appointment_type_id, payload.search_start,
        payload.search_start + timedelta(days=payload.search_days), preferred_day=payload.preferred_day,
        preferred_time_range=payload.preferred_time_of_day, maximum_results=payload.maximum_results,
    )
    audit.complete(success=True)
    return AvailabilityResponse(timezone=timezone_name, slots=slots)


@router.post("/tools/finalize-appointment-booking", response_model=CalendarBookingResponse)
async def conversation_finalize_booking(
    payload: FinalizeAppointmentRequest,
    context: TenantContext = Depends(get_tenant_context),
    _user: UserContext = Depends(get_user_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalendarBookingResponse:
    ConversationOrchestrator(db, context.id, payload.session_id, context.tenant.timezone)
    audit = ToolAudit(db, context.id, payload.session_id, payload.tool_call_id, "finalize_appointment_booking")
    try:
        result = await AppointmentBookingOrchestrator(
            db, settings, context.id, context.tenant.timezone
        ).finalize(payload)
        success = result.booking is not None and result.booking.status.value == "confirmed" and bool(result.booking.external_event_id)
        audit.complete(success=success, error_code=None if success else result.error_code)
        return booking_api_response(result)
    except CalendarError as exc:
        audit.complete(success=False, error_code=exc.code)
        return CalendarBookingResponse(success=False, error_code=exc.code, message=exc.message)
