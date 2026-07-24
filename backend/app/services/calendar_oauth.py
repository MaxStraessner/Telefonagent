import base64
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.calendar.errors import CalendarError
from app.calendar.providers import create_calendar_provider
from app.core.config import Settings
from app.core.encryption import CalendarTokenCipher
from app.models import (
    AppUser,
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarOAuthState,
    CalendarProviderName,
    Tenant,
    TenantMembership,
    TenantStatus,
)
from app.services.calendar_connections import as_utc, utc_now

OAUTH_STATE_LIFETIME_MINUTES = 10


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def start_oauth(
    db: Session,
    tenant_id: UUID,
    user_id: UUID,
    provider_name: CalendarProviderName,
    settings: Settings,
) -> tuple[str, object]:
    provider = create_calendar_provider(provider_name, settings)
    cipher = CalendarTokenCipher(settings.calendar_token_encryption_key)
    state = secrets.token_urlsafe(48)
    verifier = secrets.token_urlsafe(64)
    expires_at = utc_now() + timedelta(minutes=OAUTH_STATE_LIFETIME_MINUTES)
    db.add(
        CalendarOAuthState(
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider_name,
            state_hash=state_hash(state),
            encrypted_code_verifier=cipher.encrypt(verifier),
            expires_at=expires_at,
        )
    )
    db.commit()
    return provider.build_authorization_url(state, code_challenge(verifier)), expires_at


def load_valid_state(
    db: Session, provider_name: CalendarProviderName, state: str
) -> CalendarOAuthState:
    oauth_state = db.scalar(
        select(CalendarOAuthState).where(
            CalendarOAuthState.state_hash == state_hash(state),
            CalendarOAuthState.provider == provider_name,
        )
    )
    expires_at = as_utc(oauth_state.expires_at) if oauth_state else None
    if oauth_state is None or oauth_state.consumed_at is not None or expires_at is None or expires_at <= utc_now():
        raise CalendarError("oauth_state_invalid", "Der OAuth-Status ist ungültig oder abgelaufen.")
    return oauth_state


def consume_state(db: Session, oauth_state: CalendarOAuthState) -> None:
    oauth_state.consumed_at = utc_now()
    db.commit()


async def complete_oauth(
    db: Session,
    provider_name: CalendarProviderName,
    state: str,
    code: str,
    settings: Settings,
) -> CalendarConnection:
    if db.bind and db.bind.dialect.name == "postgresql":
        tenant_id = db.scalar(
            text(
                "SELECT resolve_calendar_oauth_tenant(:state_hash, :provider)"
            ),
            {
                "state_hash": state_hash(state),
                "provider": provider_name.value,
            },
        )
        if tenant_id is None:
            raise CalendarError(
                "oauth_state_invalid",
                "Der OAuth-Status ist ungültig oder abgelaufen.",
            )
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
    oauth_state = load_valid_state(db, provider_name, state)
    active_context = db.execute(
        select(AppUser, TenantMembership, Tenant)
        .join(
            TenantMembership,
            TenantMembership.user_id == AppUser.id,
        )
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            AppUser.id == oauth_state.user_id,
            AppUser.is_active.is_(True),
            TenantMembership.tenant_id == oauth_state.tenant_id,
            TenantMembership.is_active.is_(True),
            Tenant.status == TenantStatus.active,
        )
    ).one_or_none()
    if active_context is None:
        consume_state(db, oauth_state)
        raise CalendarError(
            "oauth_membership_inactive",
            "Die zugehörige Benutzer- oder Tenant-Berechtigung ist nicht mehr aktiv.",
        )
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(oauth_state.tenant_id)},
        )
    provider = create_calendar_provider(provider_name, settings)
    cipher = CalendarTokenCipher(settings.calendar_token_encryption_key)
    verifier = cipher.decrypt(oauth_state.encrypted_code_verifier)
    try:
        tokens = await provider.exchange_authorization_code(code, verifier)
        account = await provider.get_account_information(tokens.access_token)
    except Exception:
        consume_state(db, oauth_state)
        raise
    connection = db.scalar(
        select(CalendarConnection).where(
            CalendarConnection.tenant_id == oauth_state.tenant_id,
            CalendarConnection.provider == provider_name,
            CalendarConnection.provider_account_id == account.account_id,
        )
    )
    if connection is None:
        connection = CalendarConnection(
            tenant_id=oauth_state.tenant_id,
            created_by_user_id=oauth_state.user_id,
            provider=provider_name,
            provider_account_id=account.account_id,
            account_email=account.email,
            display_name=account.display_name,
            encrypted_access_token=cipher.encrypt(tokens.access_token),
            encrypted_refresh_token=cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
            access_token_expires_at=tokens.expires_at,
            granted_scopes=tokens.scopes,
            connection_status=CalendarConnectionStatus.connected,
            last_successful_request_at=utc_now(),
        )
        db.add(connection)
    else:
        connection.created_by_user_id = oauth_state.user_id
        connection.account_email = account.email
        connection.display_name = account.display_name
        connection.encrypted_access_token = cipher.encrypt(tokens.access_token)
        if tokens.refresh_token:
            connection.encrypted_refresh_token = cipher.encrypt(tokens.refresh_token)
        connection.access_token_expires_at = tokens.expires_at
        connection.granted_scopes = tokens.scopes
        connection.connection_status = CalendarConnectionStatus.connected
        connection.last_successful_request_at = utc_now()
        connection.last_error_code = None
        connection.last_error_at = None
    oauth_state.consumed_at = utc_now()
    db.commit()
    db.refresh(connection)
    return connection
