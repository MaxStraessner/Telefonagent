from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.dependencies import (
    AccessContext,
    get_access_context,
    get_authenticated_session,
    get_tenant_context,
    require_company_admin,
)
from app.calendar.errors import CalendarError
from app.core.config import Settings, get_settings
from app.core.encryption import CalendarTokenCipher
from app.db.session import get_db
from app.models import Tenant, TenantStatus
from app.models.entities import GmailNotification
from app.schemas.agent_config import AgentConfigurationData
from app.schemas.notifications import DEFAULT_SECTIONS, NotificationSettingsResponse, NotificationSettingsUpdate
from app.services.calendar_oauth import code_challenge, state_hash
from app.services.gmail import STATE_PREFIX, authorization_url, exchange_code
from app.services.gmail_notifications import active_connection, utc
from app.services.mail import MailDeliveryError
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(get_tenant_context)])


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def own_settings(db: Session, context: AccessContext, *, lock: bool = False) -> GmailNotification | None:
    tenant_id = context.active_tenant.id
    if lock:
        # Serialize switches of the active Gmail sender for this company.
        db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    return db.get(GmailNotification, (tenant_id, context.user.id), populate_existing=lock)


def profile(db: Session, context: AccessContext, settings: Settings, item: GmailNotification | None):
    active = active_connection(db, context.active_tenant.id)
    try:
        recipient_on_account = AgentConfigurationData.validate_recipient(context.user.email)
    except ValueError:
        recipient_on_account = None
    return NotificationSettingsResponse(
        available=settings.google_calendar_configured,
        connected=bool(item and item.encrypted_refresh_token),
        enabled=bool(item and item.enabled),
        active_elsewhere=bool(active and active.user_id != context.user.id),
        recipient=item.recipient if item else recipient_on_account,
        sections=item.sections if item else DEFAULT_SECTIONS,
    )


def disable_other_senders(db: Session, item: GmailNotification) -> None:
    db.execute(update(GmailNotification).where(
        GmailNotification.tenant_id == item.tenant_id,
        GmailNotification.user_id != item.user_id,
        GmailNotification.enabled.is_(True),
    ).values(enabled=False, generation=uuid4()))


@router.get("/gmail", response_model=NotificationSettingsResponse)
def gmail_settings(
    response: Response,
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    return profile(db, context, settings, own_settings(db, context))


@router.put("/gmail", response_model=NotificationSettingsResponse)
def save_gmail_settings(
    body: NotificationSettingsUpdate,
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    item = own_settings(db, context, lock=True)
    if body.enabled and (not item or not item.encrypted_refresh_token or not body.recipient):
        raise api_error(409, "gmail_setup_required", "Verbinden Sie Google und geben Sie eine Empfängeradresse ein.")
    if item is None:
        item = GmailNotification(tenant_id=context.active_tenant.id, user_id=context.user.id, sections=DEFAULT_SECTIONS)
        db.add(item)
    sections = list(dict.fromkeys(body.sections))
    if (item.enabled, item.recipient, item.sections) != (body.enabled, body.recipient, sections):
        clear_oauth(item)
        item.generation = uuid4()
    if body.enabled:
        disable_other_senders(db, item)
    item.enabled = body.enabled
    item.recipient = body.recipient
    item.sections = sections
    db.commit()
    return profile(db, context, settings, item)


@router.post("/gmail/connect")
def connect_gmail(
    activate: bool = False,
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.google_calendar_configured:
        raise api_error(503, "gmail_unavailable", "Google OAuth ist auf dem Server noch nicht eingerichtet.")
    enforce_rate_limit(f"gmail-oauth:{context.user.id}", limit=12)
    item = own_settings(db, context, lock=True)
    if activate and (not item or not item.recipient):
        raise api_error(409, "gmail_recipient_required", "Speichern Sie zuerst eine Empfängeradresse.")
    if item is None:
        item = GmailNotification(
            tenant_id=context.active_tenant.id, user_id=context.user.id,
            sections=DEFAULT_SECTIONS,
        )
        db.add(item)
    state = STATE_PREFIX + ("activate." if activate else "") + token_urlsafe(32)
    verifier = token_urlsafe(64)
    item.oauth_state_hash = state_hash(state)
    item.oauth_session_id = context.authenticated.session.id
    item.oauth_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    item.encrypted_verifier = CalendarTokenCipher(settings.calendar_token_encryption_key).encrypt(verifier)
    db.commit()
    return {"authorization_url": authorization_url(settings, state, code_challenge(verifier))}


@router.delete("/gmail", status_code=204)
def disconnect_gmail(
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
):
    item = own_settings(db, context, lock=True)
    if item:
        item.enabled = False
        item.generation = uuid4()
        item.encrypted_refresh_token = None
        clear_oauth(item)
        db.commit()


def clear_oauth(item: GmailNotification) -> None:
    item.oauth_state_hash = None
    item.oauth_session_id = None
    item.encrypted_verifier = None
    item.oauth_expires_at = None


async def gmail_callback(
    request: Request, state: str, code: str | None, error: str | None, db: Session, settings: Settings,
) -> RedirectResponse:
    result = "error"
    try:
        authenticated = get_authenticated_session(request, db, settings)
        context = require_company_admin(get_access_context(authenticated))
        if context.active_tenant.status not in {TenantStatus.active, TenantStatus.trial}:
            raise api_error(403, "company_inactive", "Unternehmen ist gesperrt.")
        item = own_settings(db, context, lock=True)
        digest = state_hash(state)
        if (not item or item.oauth_state_hash != digest or item.oauth_session_id != authenticated.session.id
                or not item.encrypted_verifier or not item.oauth_expires_at
                or utc(item.oauth_expires_at) <= datetime.now(timezone.utc)):
            raise api_error(400, "gmail_state_invalid", "Die Google-Verbindung ist abgelaufen.")
        encrypted_verifier = item.encrypted_verifier
        item.encrypted_verifier = None
        db.commit()
        if not error and code:
            cipher = CalendarTokenCipher(settings.calendar_token_encryption_key)
            refresh_token = await exchange_code(settings, code, cipher.decrypt(encrypted_verifier))
            renewed_session = get_authenticated_session(request, db, settings)
            current = require_company_admin(get_access_context(renewed_session))
            item = own_settings(db, current, lock=True)
            if item and item.oauth_state_hash == digest and item.oauth_session_id == renewed_session.session.id:
                clear_oauth(item)
                item.encrypted_refresh_token = cipher.encrypt(refresh_token)
                item.generation = uuid4()
                if state.startswith(STATE_PREFIX + "activate.") and item.recipient:
                    disable_other_senders(db, item)
                    item.enabled = True
                db.commit()
                result = "connected"
    except (HTTPException, CalendarError, MailDeliveryError):
        db.rollback()
    return RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}/benachrichtigungen?{urlencode({'gmail': result})}",
        status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
