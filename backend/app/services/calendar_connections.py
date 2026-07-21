import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.errors import CalendarError, CalendarProviderError
from app.calendar.providers import CalendarProvider, ProviderCalendar, create_calendar_provider
from app.core.config import Settings
from app.core.encryption import CalendarTokenCipher
from app.models import (
    CalendarBooking,
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarProviderName,
    ExternalCalendar,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def provider_configuration(settings: Settings) -> list[dict[str, object]]:
    definitions = [
        (
            CalendarProviderName.google,
            "Google Kalender",
            settings.google_calendar_configured,
            {
                "CALENDAR_TOKEN_ENCRYPTION_KEY": settings.calendar_token_encryption_key,
                "GOOGLE_CALENDAR_CLIENT_ID": settings.google_calendar_client_id,
                "GOOGLE_CALENDAR_CLIENT_SECRET": settings.google_calendar_client_secret,
                "GOOGLE_CALENDAR_REDIRECT_URI": settings.google_calendar_redirect_uri,
            },
        ),
        (
            CalendarProviderName.microsoft,
            "Microsoft Outlook",
            settings.microsoft_calendar_configured,
            {
                "CALENDAR_TOKEN_ENCRYPTION_KEY": settings.calendar_token_encryption_key,
                "MICROSOFT_CALENDAR_CLIENT_ID": settings.microsoft_calendar_client_id,
                "MICROSOFT_CALENDAR_CLIENT_SECRET": settings.microsoft_calendar_client_secret,
                "MICROSOFT_CALENDAR_REDIRECT_URI": settings.microsoft_calendar_redirect_uri,
            },
        ),
    ]
    return [
        {
            "provider": provider.value,
            "label": label,
            "configured": configured,
            "missing_configuration": [name for name, value in values.items() if not value],
        }
        for provider, label, configured, values in definitions
    ]


def get_connection(db: Session, tenant_id: UUID, connection_id: UUID) -> CalendarConnection:
    connection = db.scalar(
        select(CalendarConnection).where(
            CalendarConnection.id == connection_id,
            CalendarConnection.tenant_id == tenant_id,
        )
    )
    if connection is None:
        raise CalendarError("tenant_access_denied", "Die Kalenderverbindung gehört nicht zu diesem Account.")
    return connection


def get_provider_and_cipher(
    connection: CalendarConnection, settings: Settings
) -> tuple[CalendarProvider, CalendarTokenCipher]:
    return create_calendar_provider(connection.provider, settings), CalendarTokenCipher(settings.calendar_token_encryption_key)


async def valid_access_token(db: Session, connection: CalendarConnection, settings: Settings) -> str:
    provider, cipher = get_provider_and_cipher(connection, settings)
    expires_at = as_utc(connection.access_token_expires_at)
    if expires_at is None or expires_at > utc_now() + timedelta(seconds=90):
        return cipher.decrypt(connection.encrypted_access_token)
    if not connection.encrypted_refresh_token:
        connection.connection_status = CalendarConnectionStatus.reauthorization_required
        connection.last_error_code = "reauthorization_required"
        connection.last_error_at = utc_now()
        db.commit()
        raise CalendarError("reauthorization_required", "Die Kalenderverbindung muss erneut autorisiert werden.")
    try:
        tokens = await provider.refresh_access_token(cipher.decrypt(connection.encrypted_refresh_token))
    except CalendarProviderError as exc:
        connection.connection_status = (
            CalendarConnectionStatus.error if exc.transient else CalendarConnectionStatus.reauthorization_required
        )
        connection.last_error_code = "token_refresh_failed" if exc.transient else "reauthorization_required"
        connection.last_error_at = utc_now()
        db.commit()
        raise CalendarError(connection.last_error_code, "Das Kalenderzugangstoken konnte nicht aktualisiert werden.") from exc
    connection.encrypted_access_token = cipher.encrypt(tokens.access_token)
    if tokens.refresh_token:
        connection.encrypted_refresh_token = cipher.encrypt(tokens.refresh_token)
    connection.access_token_expires_at = tokens.expires_at
    connection.granted_scopes = tokens.scopes or connection.granted_scopes
    connection.connection_status = CalendarConnectionStatus.connected
    connection.last_error_code = None
    connection.last_error_at = None
    connection.last_successful_request_at = utc_now()
    db.commit()
    return tokens.access_token


def list_connection_calendars(db: Session, tenant_id: UUID, connection_id: UUID | None = None) -> list[ExternalCalendar]:
    statement = select(ExternalCalendar).where(ExternalCalendar.tenant_id == tenant_id)
    if connection_id is not None:
        statement = statement.where(ExternalCalendar.calendar_connection_id == connection_id)
    return list(db.scalars(statement.order_by(ExternalCalendar.is_primary.desc(), ExternalCalendar.calendar_name)))


def upsert_calendar_metadata(
    db: Session, connection: CalendarConnection, calendars: list[ProviderCalendar]
) -> list[ExternalCalendar]:
    existing = {
        item.external_calendar_id: item
        for item in list_connection_calendars(db, connection.tenant_id, connection.id)
    }
    now = utc_now()
    for provider_calendar in calendars:
        calendar = existing.get(provider_calendar.external_id)
        if calendar is None:
            calendar = ExternalCalendar(
                tenant_id=connection.tenant_id,
                calendar_connection_id=connection.id,
                external_calendar_id=provider_calendar.external_id,
                calendar_name=provider_calendar.name,
            )
            db.add(calendar)
        calendar.calendar_name = provider_calendar.name
        calendar.calendar_timezone = provider_calendar.timezone
        calendar.owner_name = provider_calendar.owner_name
        calendar.access_role = provider_calendar.access_role
        calendar.is_primary = provider_calendar.is_primary
        calendar.can_write = provider_calendar.can_write
        calendar.last_seen_at = now
        if not provider_calendar.can_write:
            calendar.is_selected_for_booking = False
    connection.last_successful_request_at = now
    connection.connection_status = CalendarConnectionStatus.connected
    connection.last_error_code = None
    connection.last_error_at = None
    db.commit()
    return list_connection_calendars(db, connection.tenant_id, connection.id)


async def synchronize_calendars(
    db: Session, connection: CalendarConnection, settings: Settings
) -> list[ExternalCalendar]:
    provider = create_calendar_provider(connection.provider, settings)
    token = await valid_access_token(db, connection, settings)
    try:
        calendars = await provider.list_calendars(token)
    except CalendarProviderError as exc:
        connection.connection_status = (
            CalendarConnectionStatus.error if exc.transient else CalendarConnectionStatus.reauthorization_required
        )
        connection.last_error_code = exc.code
        connection.last_error_at = utc_now()
        db.commit()
        raise
    return upsert_calendar_metadata(db, connection, calendars)


async def test_connection(
    db: Session, connection: CalendarConnection, settings: Settings
) -> tuple[int, int, datetime, datetime]:
    provider = create_calendar_provider(connection.provider, settings)
    token = await valid_access_token(db, connection, settings)
    calendars = await provider.list_calendars(token)
    saved = upsert_calendar_metadata(db, connection, calendars)
    selected = [item.external_calendar_id for item in saved if item.is_selected_for_availability]
    readable = selected or ([calendars[0].external_id] if calendars else [])
    start = utc_now()
    end = start + timedelta(hours=24)
    if readable:
        await provider.get_busy_intervals(token, readable, start, end)
    connection.last_successful_request_at = utc_now()
    connection.connection_status = CalendarConnectionStatus.connected
    connection.last_error_code = None
    connection.last_error_at = None
    db.commit()
    return len(calendars), len(readable), start, end


async def disconnect_connection(db: Session, connection: CalendarConnection, settings: Settings) -> None:
    provider, cipher = get_provider_and_cipher(connection, settings)
    try:
        access_token = cipher.decrypt(connection.encrypted_access_token)
        refresh_token = (
            cipher.decrypt(connection.encrypted_refresh_token) if connection.encrypted_refresh_token else None
        )
        await provider.revoke_connection(access_token, refresh_token)
    except CalendarError as exc:
        logger.warning(
            "Calendar remote revocation failed; local credentials will still be removed",
            extra={"calendar_provider": connection.provider.value, "calendar_error_code": exc.code},
        )
    calendars = list_connection_calendars(db, connection.tenant_id, connection.id)
    historical_booking = db.scalar(
        select(CalendarBooking.id).where(CalendarBooking.calendar_connection_id == connection.id).limit(1)
    )
    if historical_booking is None:
        for calendar in calendars:
            db.delete(calendar)
        db.delete(connection)
    else:
        for calendar in calendars:
            calendar.is_selected_for_availability = False
            calendar.is_selected_for_booking = False
        connection.encrypted_access_token = cipher.encrypt("revoked")
        connection.encrypted_refresh_token = None
        connection.access_token_expires_at = None
        connection.connection_status = CalendarConnectionStatus.disconnected
        connection.last_error_code = None
        connection.last_error_at = None
    db.commit()
