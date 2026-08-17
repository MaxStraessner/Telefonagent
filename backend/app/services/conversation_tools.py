from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import TenantContext
from app.calendar.errors import CalendarError
from app.core.config import Settings
from app.models import BookingState, CalendarAppointmentType, Service, ToolExecution
from app.schemas.calendar import (
    AvailabilityResponse,
    CalendarBookingResponse,
    FinalizeStoredAppointmentRequest,
    ListBookableServicesRequest,
    PrepareAppointmentConfirmationRequest,
    PrepareAppointmentConfirmationResponse,
    ResolveBookingDateTimeRequest,
    ResolveBookingDateTimeResponse,
    ResolveServiceRequest,
    SelectBookingSlotRequest,
    SnapshotAvailabilityResponse,
    StoredAlternativeSlotsRequest,
    StoredAvailabilityRequest,
)
from app.services.availability import SlotSigner, aware_utc
from app.services.availability_snapshot import AvailabilitySnapshotService
from app.services.booking_orchestrator import AppointmentBookingOrchestrator
from app.services.calendar_configuration import get_or_create_booking_configuration
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.german_datetime import resolve_german_datetime
from app.services.tool_audit import ToolAudit, tool_continuation_mode


def _jsonable(value: object) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    return {"success": True, "result": value}


def _calendar_http_error(exc: CalendarError) -> HTTPException:
    status_by_code = {
        "calendar_not_found": status.HTTP_404_NOT_FOUND,
        "calendar_connection_not_found": status.HTTP_404_NOT_FOUND,
        "booking_not_found": status.HTTP_404_NOT_FOUND,
        "slot_not_found": status.HTTP_404_NOT_FOUND,
        "provider_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
        "provider_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "provider_rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
        "slot_no_longer_available": status.HTTP_409_CONFLICT,
        "duplicate_booking": status.HTTP_409_CONFLICT,
        "reauthorization_required": status.HTTP_409_CONFLICT,
    }
    return HTTPException(
        status_code=status_by_code.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": exc.message},
    )


def bookable_services(db: Session, tenant_id: UUID) -> dict[str, Any]:
    items = list(
        db.scalars(
            select(CalendarAppointmentType)
            .where(
                CalendarAppointmentType.tenant_id == tenant_id,
                CalendarAppointmentType.is_active.is_(True),
                Service.is_active.is_(True),
            )
            .join(Service, Service.id == CalendarAppointmentType.service_id)
            .options(selectinload(CalendarAppointmentType.service))
            .order_by(Service.name)
        )
    )
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.service_id)
        grouped.setdefault(
            key,
            {
                "service_id": key,
                "name": item.service.name,
                "description": item.service.description,
                "duration_minutes": item.service.duration_minutes,
                "appointment_types": [],
            },
        )["appointment_types"].append(
            {
                "appointment_type_id": str(item.id),
                "appointment_format": item.location_type.value,
                "location": item.location_text,
                "buffer_before_minutes": item.buffer_before_minutes or 0,
                "buffer_after_minutes": item.buffer_after_minutes or 0,
            }
        )
    return {"success": True, "services": list(grouped.values())}


def booking_api_response(result: Any) -> CalendarBookingResponse:
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


class ConversationToolDispatcher:
    """Shared calendar conversation core for browser and telephone transports."""

    def __init__(
        self,
        db: Session,
        settings: Settings,
        context: TenantContext,
        call_session_id: UUID,
    ) -> None:
        self.db = db
        self.settings = settings
        self.context = context
        self.call_session_id = call_session_id

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
        latest_confirmed_user_utterance: str | None,
    ) -> dict[str, Any]:
        payload_values = {
            **arguments,
            "session_id": self.call_session_id,
            "tool_call_id": call_id,
        }
        continuation_token = tool_continuation_mode.set("server_websocket")
        try:
            if tool_name == "list_bookable_services":
                result = self.list_bookable_services(
                    ListBookableServicesRequest(**payload_values)
                )
            elif tool_name == "resolve_service":
                result = self.resolve_service(ResolveServiceRequest(**payload_values))
            elif tool_name == "resolve_booking_datetime":
                result = self.resolve_booking_datetime(
                    ResolveBookingDateTimeRequest(**payload_values)
                )
            elif tool_name == "check_appointment_availability":
                result = await self.check_availability(
                    StoredAvailabilityRequest(**payload_values)
                )
            elif tool_name == "find_alternative_slots":
                result = await self.find_alternatives(
                    StoredAlternativeSlotsRequest(**payload_values)
                )
            elif tool_name == "select_booking_slot":
                result = self.select_booking_slot(
                    SelectBookingSlotRequest(**payload_values)
                )
            elif tool_name == "prepare_appointment_confirmation":
                result = await self.prepare_confirmation(
                    PrepareAppointmentConfirmationRequest(**payload_values)
                )
            elif tool_name == "finalize_appointment_booking":
                utterance = (latest_confirmed_user_utterance or "").strip()
                if not utterance:
                    return {
                        "success": False,
                        "error_code": "confirmation_utterance_missing",
                        "message": "Die letzte bestätigte Nutzeräußerung fehlt; es wurde nicht gebucht.",
                    }
                payload_values["confirmation_utterance"] = utterance
                result = await self.finalize_booking(
                    FinalizeStoredAppointmentRequest(**payload_values)
                )
            else:
                return {
                    "success": False,
                    "error_code": "tool_not_supported",
                    "message": "Das angeforderte Werkzeug ist nicht verfügbar.",
                }
            return _jsonable(result)
        except ValidationError:
            return {
                "success": False,
                "error_code": "invalid_tool_arguments",
                "message": "Die Werkzeugargumente sind ungültig.",
            }
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "success": False,
                "error_code": str(detail.get("code") or "tool_failed"),
                "message": str(
                    detail.get("message")
                    or "Das Werkzeug konnte nicht ausgeführt werden."
                ),
            }
        except CalendarError as exc:
            return {"success": False, "error_code": exc.code, "message": exc.message}
        except Exception:
            self.db.rollback()
            return {
                "success": False,
                "error_code": "tool_failed",
                "message": "Das Werkzeug konnte nicht ausgeführt werden.",
            }
        finally:
            tool_continuation_mode.reset(continuation_token)

    def list_bookable_services(self, payload: ListBookableServicesRequest) -> dict:
        ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "list_bookable_services",
        )
        try:
            result = bookable_services(self.db, self.context.id)
            audit.complete(success=True)
            return result
        except Exception:
            audit.complete(success=False, error_code="catalog_unavailable")
            raise

    def resolve_service(self, payload: ResolveServiceRequest) -> dict:
        orchestrator = ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "resolve_service",
        )
        normalized = payload.service_name.strip().casefold()
        candidates = list(
            self.db.scalars(
                select(Service)
                .where(
                    Service.tenant_id == self.context.id,
                    Service.is_active.is_(True),
                )
                .order_by(Service.name)
            )
        )
        exact = [item for item in candidates if item.name.casefold() == normalized]
        partial = [
            item
            for item in candidates
            if normalized in item.name.casefold()
            or item.name.casefold() in normalized
        ]
        matches = exact or partial
        if len(matches) != 1:
            audit.complete(success=False, error_code="service_not_unique")
            return {
                "success": False,
                "error_code": "service_not_unique",
                "matches": [
                    {"service_id": str(item.id), "name": item.name}
                    for item in matches[:5]
                ],
            }
        service = matches[0]
        appointment_types = list(
            self.db.scalars(
                select(CalendarAppointmentType).where(
                    CalendarAppointmentType.tenant_id == self.context.id,
                    CalendarAppointmentType.service_id == service.id,
                    CalendarAppointmentType.is_active.is_(True),
                )
            )
        )
        appointment_type_id = (
            appointment_types[0].id if len(appointment_types) == 1 else None
        )
        orchestrator.select_service(service.id, service.name, appointment_type_id)
        self.db.commit()
        audit.complete(success=True)
        return {
            "success": True,
            "service_id": str(service.id),
            "name": service.name,
            "appointment_types": [
                {
                    "appointment_type_id": str(item.id),
                    "appointment_format": item.location_type.value,
                }
                for item in appointment_types
            ],
        }

    def resolve_booking_datetime(
        self, payload: ResolveBookingDateTimeRequest
    ) -> ResolveBookingDateTimeResponse:
        orchestrator = ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "resolve_booking_datetime",
        )
        if orchestrator.context.state == BookingState.date_time_required:
            orchestrator.transition(BookingState.date_time_resolving)
        elif orchestrator.context.state in {
            BookingState.slot_available,
            BookingState.alternatives_available,
            BookingState.slot_rechecking,
            BookingState.customer_data_required,
            BookingState.awaiting_confirmation,
        }:
            orchestrator.transition(BookingState.date_time_resolving)
        if orchestrator.context.state != BookingState.date_time_resolving:
            audit.complete(success=False, error_code="service_required")
            raise _calendar_http_error(
                CalendarError(
                    "service_required",
                    "Vor der Datumsauflösung muss eine Leistung ausgewählt werden.",
                )
            )
        configuration, _hours = get_or_create_booking_configuration(
            self.db, self.context.id, self.context.tenant.timezone
        )
        resolution = resolve_german_datetime(
            payload.expression,
            now=datetime.now(timezone.utc),
            timezone_name=self.context.tenant.timezone,
            horizon_days=configuration.maximum_booking_horizon_days,
        )
        booking = orchestrator.context
        orchestrator.invalidate_confirmation("datetime_changed")
        booking.selected_slot_start = None
        booking.selected_slot_end = None
        booking.selected_slot_id = None
        booking.offered_slot_ids = []
        booking.datetime_resolution_status = resolution.status.value
        booking.datetime_resolution_version += 1
        booking.datetime_explicit_year = resolution.explicit_year
        booking.requested_start = aware_utc(resolution.start) if resolution.start else None
        booking.requested_end = aware_utc(resolution.end) if resolution.end else None
        self.db.commit()
        audit.complete(
            success=resolution.status.value in {"concrete", "search_window"},
            error_code=None
            if resolution.status.value in {"concrete", "search_window"}
            else resolution.reason,
        )
        return ResolveBookingDateTimeResponse(
            status=resolution.status.value,
            timezone=self.context.tenant.timezone,
            start=resolution.start,
            end=resolution.end,
            speech=resolution.speech,
            reason=resolution.reason,
            explicit_year=resolution.explicit_year,
            resolution_version=booking.datetime_resolution_version,
        )

    async def check_availability(
        self, payload: StoredAvailabilityRequest
    ) -> SnapshotAvailabilityResponse:
        orchestrator = ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "check_appointment_availability",
        )
        booking = orchestrator.context
        if (
            booking.state != BookingState.date_time_resolving
            or booking.datetime_resolution_status != "concrete"
            or booking.requested_start is None
            or booking.service_id is None
        ):
            audit.complete(success=False, error_code="datetime_resolution_required")
            raise _calendar_http_error(
                CalendarError(
                    "datetime_resolution_required",
                    "Die Verfügbarkeit darf nur mit einer gespeicherten konkreten Datumsauflösung geprüft werden.",
                )
            )
        if booking.appointment_type_id not in {None, payload.appointment_type_id}:
            audit.complete(success=False, error_code="conversation_context_mismatch")
            raise _calendar_http_error(
                CalendarError(
                    "conversation_context_mismatch",
                    "Die Terminart passt nicht zur ausgewählten Leistung.",
                )
            )
        booking.appointment_type_id = payload.appointment_type_id
        requested_start = aware_utc(booking.requested_start)
        orchestrator.transition(BookingState.availability_checking)
        appointment_type = self.db.scalar(
            select(CalendarAppointmentType)
            .where(
                CalendarAppointmentType.id == payload.appointment_type_id,
                CalendarAppointmentType.tenant_id == self.context.id,
                CalendarAppointmentType.service_id == booking.service_id,
            )
            .options(selectinload(CalendarAppointmentType.service))
        )
        if appointment_type is None or appointment_type.service is None:
            audit.complete(success=False, error_code="invalid_appointment_type")
            raise _calendar_http_error(
                CalendarError("invalid_appointment_type", "Die Terminart ist ungültig.")
            )
        requested_end = requested_start + timedelta(
            minutes=appointment_type.service.duration_minutes
        )
        snapshot_service = AvailabilitySnapshotService(
            self.db,
            self.settings,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        configuration, _hours, _configured_type = (
            snapshot_service.availability.load_rules(payload.appointment_type_id)
        )
        timezone_name, exact_candidates, refreshed = await snapshot_service.search(
            payload.appointment_type_id,
            requested_start - timedelta(minutes=configuration.slot_interval_minutes),
            requested_end + timedelta(minutes=configuration.slot_interval_minutes),
            maximum_results=10,
        )
        exact = next(
            (
                slot
                for slot in exact_candidates
                if aware_utc(slot.start) == requested_start
            ),
            None,
        )
        available = exact is not None
        alternatives = []
        if not available:
            timezone_name, alternatives, alternatives_refreshed = (
                await snapshot_service.search(
                    payload.appointment_type_id,
                    requested_start,
                    requested_end + timedelta(days=7),
                    maximum_results=3,
                )
            )
            refreshed = refreshed or alternatives_refreshed
            alternatives.sort(
                key=lambda slot: abs(
                    (aware_utc(slot.start) - requested_start).total_seconds()
                )
            )
        booking.requested_end = requested_end
        if available:
            booking.selected_slot_start = aware_utc(exact.start)
            booking.selected_slot_end = aware_utc(exact.end)
            booking.selected_slot_id = exact.slot_id
            booking.offered_slot_ids = [exact.slot_id]
        else:
            booking.selected_slot_start = None
            booking.selected_slot_end = None
            booking.selected_slot_id = None
            booking.offered_slot_ids = [item.slot_id for item in alternatives[:3]]
        orchestrator.transition(
            BookingState.slot_available
            if available
            else BookingState.alternatives_available
        )
        self.db.commit()
        audit.complete(success=True)
        return SnapshotAvailabilityResponse(
            available=available,
            appointment_start=requested_start,
            appointment_end=requested_end,
            blocked_start=requested_start,
            blocked_end=requested_end,
            slot_id=exact.slot_id if exact else None,
            reason=None if available else "slot_unavailable",
            alternatives=[] if available else alternatives[:3],
            source="targeted_refresh" if refreshed else "snapshot",
            timezone=timezone_name,
        )

    async def find_alternatives(
        self, payload: StoredAlternativeSlotsRequest
    ) -> AvailabilityResponse:
        orchestrator = ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "find_alternative_slots",
        )
        booking = orchestrator.context
        if (
            booking.appointment_type_id is None
            or booking.requested_start is None
            or booking.state
            not in {
                BookingState.date_time_resolving,
                BookingState.alternatives_available,
            }
        ):
            audit.complete(success=False, error_code="datetime_resolution_required")
            raise _calendar_http_error(
                CalendarError(
                    "datetime_resolution_required",
                    "Alternativen benötigen eine gespeicherte Datumsauflösung.",
                )
            )
        search_start = aware_utc(booking.requested_start)
        search_end = (
            aware_utc(booking.requested_end)
            if booking.datetime_resolution_status == "search_window"
            and booking.requested_end
            else search_start + timedelta(days=7)
        )
        timezone_name, slots, _refreshed = await AvailabilitySnapshotService(
            self.db,
            self.settings,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        ).search(
            booking.appointment_type_id,
            search_start,
            search_end,
            preferred_time_range=payload.preferred_time_of_day,
            maximum_results=payload.maximum_results,
        )
        booking.offered_slot_ids = [slot.slot_id for slot in slots]
        if booking.state == BookingState.date_time_resolving:
            orchestrator.transition(BookingState.availability_checking)
            orchestrator.transition(BookingState.alternatives_available)
        self.db.commit()
        audit.complete(success=True)
        return AvailabilityResponse(timezone=timezone_name, slots=slots)

    def select_booking_slot(self, payload: SelectBookingSlotRequest) -> dict:
        orchestrator = ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "select_booking_slot",
        )
        booking = orchestrator.context
        if (
            booking.state != BookingState.alternatives_available
            or payload.slot_id not in (booking.offered_slot_ids or [])
        ):
            audit.complete(success=False, error_code="slot_selection_invalid")
            raise _calendar_http_error(
                CalendarError(
                    "slot_selection_invalid",
                    "Der Terminvorschlag gehört nicht zu den aktuellen Alternativen.",
                )
            )
        claim = SlotSigner(self.settings).verify(payload.slot_id)
        if (
            claim.tenant_id != self.context.id
            or claim.appointment_type_id != booking.appointment_type_id
        ):
            audit.complete(success=False, error_code="slot_selection_invalid")
            raise _calendar_http_error(
                CalendarError(
                    "slot_selection_invalid",
                    "Der Terminvorschlag passt nicht zum Gesprächskontext.",
                )
            )
        booking.selected_slot_id = payload.slot_id
        booking.selected_slot_start = claim.start
        booking.selected_slot_end = claim.end
        orchestrator.invalidate_confirmation("slot_changed")
        orchestrator.transition(BookingState.slot_rechecking)
        self.db.commit()
        audit.complete(success=True)
        return {
            "success": True,
            "state": booking.state.value,
            "start": claim.start,
            "end": claim.end,
            "timezone": self.context.tenant.timezone,
        }

    async def prepare_confirmation(
        self, payload: PrepareAppointmentConfirmationRequest
    ) -> PrepareAppointmentConfirmationResponse:
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "prepare_appointment_confirmation",
        )
        try:
            result = await AppointmentBookingOrchestrator(
                self.db,
                self.settings,
                self.context.id,
                self.context.tenant.timezone,
            ).prepare(payload)
            audit.complete(success=True)
            return result
        except CalendarError as exc:
            audit.complete(success=False, error_code=exc.code)
            raise _calendar_http_error(exc) from exc

    async def finalize_booking(
        self, payload: FinalizeStoredAppointmentRequest
    ) -> CalendarBookingResponse:
        ConversationOrchestrator(
            self.db,
            self.context.id,
            payload.session_id,
            self.context.tenant.timezone,
        )
        audit = ToolAudit(
            self.db,
            self.context.id,
            payload.session_id,
            payload.tool_call_id,
            "finalize_appointment_booking",
        )
        try:
            result = await AppointmentBookingOrchestrator(
                self.db,
                self.settings,
                self.context.id,
                self.context.tenant.timezone,
            ).finalize(payload)
            success = (
                result.booking is not None
                and result.booking.status.value == "confirmed"
                and bool(result.booking.external_event_id)
            )
            audit.complete(
                success=success,
                error_code=None if success else result.error_code,
            )
            return booking_api_response(result)
        except CalendarError as exc:
            audit.complete(success=False, error_code=exc.code)
            return CalendarBookingResponse(
                success=False, error_code=exc.code, message=exc.message
            )

    def mark_result_sent(self, call_id: str) -> None:
        try:
            execution = self.db.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == self.context.id,
                    ToolExecution.call_session_id == self.call_session_id,
                    ToolExecution.call_id == call_id,
                )
            )
            if execution is None:
                return
            now = datetime.now(timezone.utc)
            execution.result_sent_at = now
            execution.continuation_triggered_at = now
            self.db.commit()
        except Exception:
            self.db.rollback()
