from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Tenant, TenantInboundRoute, TenantStatus
from app.schemas.telephony import CompanyTelephonyResponse, TwilioNumberResponse
from app.services.tenant_resolution import normalize_inbound_identifier

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
CALL_SID_PATTERN = re.compile(r"^CA[0-9a-fA-F]{32}$")
ACCOUNT_SID_PATTERN = re.compile(r"^AC[0-9a-fA-F]{32}$")
PHONE_SID_PATTERN = re.compile(r"^PN[0-9a-fA-F]{32}$")
STREAM_SID_PATTERN = re.compile(r"^MZ[0-9a-fA-F]{32}$")
TOKEN_TTL_SECONDS = 120


class TwilioServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        blocked: bool = False,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.blocked = blocked
        self.details = details or {}


@dataclass(frozen=True)
class TwilioNumber:
    sid: str
    phone_number: str
    friendly_name: str
    voice_capable: bool
    voice_application_sid: str | None
    trunk_sid: str | None
    voice_url: str | None
    voice_method: str | None


class TwilioProvider(Protocol):
    def list_numbers(self) -> list[TwilioNumber]: ...
    def find_number(self, phone_number: str) -> TwilioNumber | None: ...
    def fetch_number(self, sid: str) -> TwilioNumber: ...
    def configure_voice(self, sid: str, *, voice_url: str, status_callback: str) -> None: ...
    def validate_request(self, url: str, params: dict[str, str], signature: str) -> bool: ...


class TwilioSdkProvider:
    def __init__(self, account_sid: str, auth_token: str) -> None:
        from twilio.request_validator import RequestValidator
        from twilio.rest import Client

        self._client = Client(account_sid, auth_token)
        self._validator = RequestValidator(auth_token)

    @staticmethod
    def _number(resource: object) -> TwilioNumber:
        capabilities = getattr(resource, "capabilities", None) or {}
        return TwilioNumber(
            sid=str(getattr(resource, "sid")),
            phone_number=str(getattr(resource, "phone_number")),
            friendly_name=str(getattr(resource, "friendly_name", "") or ""),
            voice_capable=bool(capabilities.get("voice", False)),
            voice_application_sid=getattr(resource, "voice_application_sid", None),
            trunk_sid=getattr(resource, "trunk_sid", None),
            voice_url=getattr(resource, "voice_url", None),
            voice_method=getattr(resource, "voice_method", None),
        )

    def list_numbers(self) -> list[TwilioNumber]:
        try:
            return [
                self._number(item)
                for item in self._client.incoming_phone_numbers.list(limit=1000)
            ]
        except Exception as exc:
            raise TwilioServiceError(
                "provider_numbers_unavailable",
                "Die Twilio-Nummern konnten nicht geladen werden.",
            ) from exc

    def find_number(self, phone_number: str) -> TwilioNumber | None:
        try:
            matches = self._client.incoming_phone_numbers.list(
                phone_number=phone_number,
                limit=2,
            )
        except Exception as exc:
            raise TwilioServiceError(
                "provider_numbers_unavailable",
                "Die Twilio-Nummer konnte nicht im Twilio-Konto gesucht werden.",
            ) from exc
        if len(matches) > 1:
            raise TwilioServiceError(
                "provider_number_ambiguous",
                "Twilio hat die Telefonnummer nicht eindeutig aufgelöst.",
                blocked=True,
            )
        return self._number(matches[0]) if matches else None

    def fetch_number(self, sid: str) -> TwilioNumber:
        try:
            return self._number(self._client.incoming_phone_numbers(sid).fetch())
        except Exception as exc:
            raise TwilioServiceError("provider_number_unavailable", "Die Twilio-Nummer konnte nicht geladen werden.") from exc

    def configure_voice(self, sid: str, *, voice_url: str, status_callback: str) -> None:
        try:
            self._client.incoming_phone_numbers(sid).update(
                voice_url=voice_url,
                voice_method="POST",
                status_callback=status_callback,
                status_callback_method="POST",
            )
        except Exception as exc:
            raise TwilioServiceError("provider_sync_failed", "Die Twilio-Konfiguration konnte nicht synchronisiert werden.") from exc

    def validate_request(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(signature and self._validator.validate(url, params, signature))


def require_twilio_provider(settings: Settings) -> TwilioProvider:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise TwilioServiceError("twilio_not_configured", "Twilio ist serverseitig nicht konfiguriert.")
    return TwilioSdkProvider(settings.twilio_account_sid, settings.twilio_auth_token)


def _public_url(settings: Settings, path: str, *, websocket: bool = False) -> str:
    parsed = urlsplit(settings.app_base_url.rstrip("/"))
    scheme = "wss" if websocket and parsed.scheme == "https" else "ws" if websocket else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, f"{parsed.path.rstrip('/')}{path}", "", ""))


def voice_url(settings: Settings) -> str:
    return _public_url(settings, "/api/v1/twilio/voice")


def stream_status_url(settings: Settings) -> str:
    return _public_url(settings, "/api/v1/twilio/stream-status")


def media_url(settings: Settings) -> str:
    return _public_url(settings, "/api/v1/twilio/media", websocket=True)


def validate_e164(value: str) -> str:
    normalized = normalize_inbound_identifier("phone_number", value)
    if not E164_PATTERN.fullmatch(normalized):
        raise TwilioServiceError(
            "invalid_phone_number",
            "Bitte geben Sie eine gültige Telefonnummer im E.164-Format ein.",
            blocked=True,
        )
    return normalized


class TwilioTelephonyService:
    def __init__(
        self, db: Session, settings: Settings, provider: TwilioProvider | None = None
    ) -> None:
        self.db = db
        self.settings = settings
        self.provider = provider

    def status(self, tenant: Tenant) -> CompanyTelephonyResponse:
        route = self.db.scalar(select(TenantInboundRoute).where(
            TenantInboundRoute.tenant_id == tenant.id,
            TenantInboundRoute.route_type == "phone_number",
        ))
        return CompanyTelephonyResponse(
            provider="twilio" if route and route.provider == "twilio" else None,
            phone_number=route.normalized_identifier if route else None,
            phone_number_sid=route.provider_resource_id if route else None,
            sync_status=route.provider_sync_status if route else None,
            expected_voice_url=voice_url(self.settings),
            provider_synced_url=route.provider_synced_url if route else None,
            provider_synced_at=route.provider_synced_at if route else None,
            error_code=route.provider_error_code if route else None,
        )

    def list_numbers(self) -> list[TwilioNumberResponse]:
        if self.provider is None:
            raise TwilioServiceError("twilio_not_configured", "Twilio ist serverseitig nicht konfiguriert.")
        routes = self.db.execute(
            select(TenantInboundRoute, Tenant)
            .join(Tenant, Tenant.id == TenantInboundRoute.tenant_id)
            .where(TenantInboundRoute.provider == "twilio")
        ).all()
        assigned = {route.provider_resource_id: (route, tenant) for route, tenant in routes}
        result: list[TwilioNumberResponse] = []
        for number in self.provider.list_numbers():
            route_tenant = assigned.get(number.sid)
            route, tenant = route_tenant if route_tenant else (None, None)
            result.append(TwilioNumberResponse(
                sid=number.sid,
                phone_number=validate_e164(number.phone_number),
                friendly_name=number.friendly_name,
                voice_capable=number.voice_capable,
                assigned_company_id=tenant.id if tenant else None,
                assigned_company_name=tenant.name if tenant else None,
                routing_status=route.provider_sync_status if route else "available",
            ))
        return result

    def assign(
        self,
        tenant: Tenant,
        phone_number_value: str,
        *,
        transfer: bool = False,
    ) -> CompanyTelephonyResponse:
        if self.provider is None:
            raise TwilioServiceError(
                "twilio_not_configured",
                "Twilio ist serverseitig nicht konfiguriert.",
            )
        if tenant.status != TenantStatus.active:
            raise TwilioServiceError(
                "company_inactive",
                "Telefonie kann nur aktiven Unternehmen zugeordnet werden.",
                blocked=True,
            )

        phone_number = validate_e164(phone_number_value)
        number = self.provider.find_number(phone_number)
        if number is None:
            raise TwilioServiceError(
                "phone_number_not_found",
                "Diese Telefonnummer ist im konfigurierten Twilio-Konto nicht vorhanden.",
                blocked=True,
            )
        if not PHONE_SID_PATTERN.fullmatch(number.sid):
            raise TwilioServiceError(
                "provider_number_invalid",
                "Twilio hat keine gültige Telefonnummer-Ressource geliefert.",
                blocked=True,
            )
        if not number.voice_capable:
            raise TwilioServiceError(
                "number_not_voice_capable",
                "Die Twilio-Nummer unterstützt keine Sprachanrufe.",
                blocked=True,
            )
        if validate_e164(number.phone_number) != phone_number:
            raise TwilioServiceError(
                "provider_number_mismatch",
                "Die von Twilio gelieferte Telefonnummer stimmt nicht überein.",
                blocked=True,
            )

        routes = self.db.scalars(
            select(TenantInboundRoute)
            .where(
                TenantInboundRoute.route_type == "phone_number",
                or_(
                    TenantInboundRoute.tenant_id == tenant.id,
                    TenantInboundRoute.normalized_identifier == phone_number,
                    and_(
                        TenantInboundRoute.provider == "twilio",
                        TenantInboundRoute.provider_resource_id == number.sid,
                    ),
                ),
            )
            .with_for_update()
        ).all()
        target_route = next(
            (route for route in routes if route.tenant_id == tenant.id),
            None,
        )
        number_routes = [
            route
            for route in routes
            if route.normalized_identifier == phone_number
            or (route.provider == "twilio" and route.provider_resource_id == number.sid)
        ]
        if len({route.id for route in number_routes}) > 1:
            raise TwilioServiceError(
                "number_route_conflict",
                "Die gespeicherte Telefonnummer-Zuordnung ist inkonsistent.",
                blocked=True,
            )
        assigned_route = number_routes[0] if number_routes else None

        if assigned_route is not None and assigned_route.tenant_id != tenant.id:
            assigned_tenant = self.db.get(Tenant, assigned_route.tenant_id)
            assigned_name = assigned_tenant.name if assigned_tenant else "ein anderes Unternehmen"
            if not transfer:
                raise TwilioServiceError(
                    "number_already_assigned",
                    f"Die Twilio-Nummer ist bereits {assigned_name} zugeordnet.",
                    blocked=True,
                    details={
                        "assigned_company_id": str(assigned_route.tenant_id),
                        "assigned_company_name": assigned_name,
                    },
                )
            if target_route is not None and target_route.id != assigned_route.id:
                self.db.delete(target_route)
                self.db.flush()
            route = assigned_route
            route.tenant_id = tenant.id
        else:
            route = target_route or assigned_route
            if route is None:
                route = TenantInboundRoute(
                    tenant_id=tenant.id,
                    route_type="phone_number",
                    normalized_identifier=phone_number,
                )

        route.normalized_identifier = phone_number
        route.is_active = True
        route.provider = "twilio"
        route.provider_resource_id = number.sid
        route.provider_sync_status = "pending"
        route.provider_synced_url = None
        route.provider_synced_at = None
        route.provider_error_code = None
        self.db.add(route)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise TwilioServiceError(
                "number_already_assigned",
                "Die Twilio-Nummer ist bereits einem anderen Unternehmen zugeordnet.",
                blocked=True,
            ) from exc
        return self.sync(tenant)

    def remove(self, tenant: Tenant) -> CompanyTelephonyResponse:
        route = self.db.scalar(
            select(TenantInboundRoute)
            .where(
                TenantInboundRoute.tenant_id == tenant.id,
                TenantInboundRoute.route_type == "phone_number",
                TenantInboundRoute.provider == "twilio",
            )
            .with_for_update()
        )
        if route is not None:
            self.db.delete(route)
            self.db.commit()
        return self.status(tenant)

    def sync(self, tenant: Tenant) -> CompanyTelephonyResponse:
        if self.provider is None:
            raise TwilioServiceError("twilio_not_configured", "Twilio ist serverseitig nicht konfiguriert.")
        route = self.db.scalar(select(TenantInboundRoute).where(
            TenantInboundRoute.tenant_id == tenant.id,
            TenantInboundRoute.route_type == "phone_number",
            TenantInboundRoute.provider == "twilio",
        ))
        if route is None or not route.provider_resource_id:
            raise TwilioServiceError("telephony_not_assigned", "Dem Unternehmen ist keine Twilio-Nummer zugeordnet.", blocked=True)
        route.provider_sync_status = "pending"
        route.provider_error_code = None
        self.db.commit()
        try:
            number = self.provider.fetch_number(route.provider_resource_id)
            if number.voice_application_sid or number.trunk_sid:
                raise TwilioServiceError("provider_routing_conflict", "Die Nummer wird durch eine TwiML Application oder einen SIP-Trunk gesteuert.", blocked=True)
            if not number.voice_capable or validate_e164(number.phone_number) != route.normalized_identifier:
                raise TwilioServiceError("provider_number_mismatch", "Die gespeicherte Twilio-Zuordnung ist nicht mehr gültig.", blocked=True)
            if urlsplit(self.settings.app_base_url).scheme != "https":
                raise TwilioServiceError("public_https_required", "Für die Twilio-Synchronisation muss APP_BASE_URL eine öffentliche HTTPS-URL sein.", blocked=True)
            self.provider.configure_voice(route.provider_resource_id, voice_url=voice_url(self.settings), status_callback=stream_status_url(self.settings))
            route.provider_sync_status = "synced"
            route.provider_synced_url = voice_url(self.settings)
            route.provider_synced_at = datetime.now(timezone.utc)
        except TwilioServiceError as exc:
            route.provider_sync_status = "blocked" if exc.blocked else "error"
            route.provider_error_code = exc.code
        self.db.commit()
        self.db.refresh(route)
        return self.status(tenant)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_call_token(
    settings: Settings,
    *,
    tenant_id: UUID,
    call_session_id: UUID,
    call_sid: str,
    phone_number: str,
) -> str:
    if not settings.twilio_stream_token_secret:
        raise TwilioServiceError("twilio_not_configured", "Twilio ist serverseitig nicht vollständig konfiguriert.")
    payload = _b64encode(json.dumps({
        "tenant_id": str(tenant_id), "call_session_id": str(call_session_id),
        "call_sid": call_sid,
        "phone_number": validate_e164(phone_number),
        "exp": int((datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS)).timestamp()),
    }, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(settings.twilio_stream_token_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_call_token(
    settings: Settings, token: str, *, call_sid: str
) -> tuple[UUID, UUID, str]:
    if not settings.twilio_stream_token_secret:
        raise TwilioServiceError("invalid_stream_token", "Der Stream-Token ist ungültig.")
    try:
        payload, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(settings.twilio_stream_token_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        values = json.loads(_b64decode(payload))
        if values["call_sid"] != call_sid or int(values["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError
        return (
            UUID(values["tenant_id"]),
            UUID(values["call_session_id"]),
            validate_e164(values["phone_number"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TwilioServiceError("invalid_stream_token", "Der Stream-Token ist ungültig.") from exc
