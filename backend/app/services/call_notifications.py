"""One opt-in SMTP notification per call, using the existing tenant-bound queue.

A lost worker after submission is uncertain, never automatically retried. SMTP
does not provide exactly-once delivery. No database locks span network I/O.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.calendar.errors import CalendarError
from app.core.encryption import CalendarTokenCipher
from app.models import AgentConfiguration, CallChannel, CallSession, Tenant
from app.services.gmail import send_gmail
from app.services.gmail_call_mail import call_mail, twilio_cost
from app.services.gmail_notifications import active_connection
from app.services.mail import MailDeliveryError, OutboundMail, build_mail_adapter, mail_transport_available

OPEN_NOTIFICATIONS = ("waiting_analysis", "pending", "sending")


def cancel_notification(call: CallSession) -> None:
    if call.notification_status in OPEN_NOTIFICATIONS:
        call.notification_status = "canceled"
    call.notification_recipient = None
    call.notification_next_attempt_at = None
    call.notification_lease_token = None


def render_call_mail(call: CallSession, settings) -> OutboundMail:
    details = call.analysis_details or {}
    callback = {True: "Ja", False: "Nein", None: "Unbekannt"}.get(details.get("callback_required"), "Unbekannt")
    text = "\n".join([
        "Neue Nachricht vom Anrufbeantworter",
        f"Zeitpunkt: {_utc(call.started_at).isoformat()} (Zeit mit UTC-Offset)",
        f"Name: {call.caller_name or 'Nicht angegeben'}",
        f"Anrufnummer: {call.caller_phone or 'Unbekannt'}",
        f"Genannte Telefonnummer: {details.get('contact_phone') or 'Nicht angegeben'}",
        f"Genannte E-Mail: {details.get('contact_email') or 'Nicht angegeben'}",
        f"Anliegen: {details.get('main_concern') or 'Nicht angegeben'}",
        f"Zusammenfassung: {call.summary or 'Keine Zusammenfassung verfügbar.'}",
        f"Rückruf gewünscht: {callback}",
        "",
        f"Transkript und Details (Anmeldung erforderlich): {settings.frontend_url.rstrip('/')}/anrufe/{call.id}",
    ])
    return OutboundMail(call.notification_recipient, "Neue Anrufbeantworter-Nachricht", text,
                        message_id=f"<call-{call.id}@telefonagent.local>")


async def process_call_notification(db, settings, tenant_id, call_id, *, adapter=None) -> None:
    def locked_call():
        return db.scalar(select(CallSession).where(
            CallSession.id == call_id, CallSession.tenant_id == tenant_id,
            CallSession.channel == CallChannel.telephone,
        ).with_for_update().execution_options(populate_existing=True))

    now = datetime.now(timezone.utc)
    call = locked_call()
    if call is None or call.notification_status not in OPEN_NOTIFICATIONS:
        db.rollback()
        return
    gmail = call.notification_gmail_generation is not None
    if call.notification_status == "sending":
        deadline = call.notification_next_attempt_at
        if deadline is None or _utc(deadline) <= now:
            call.notification_status = "uncertain"
            call.notification_error_code = "gmail_worker_lost" if gmail else "smtp_worker_lost"
            call.notification_next_attempt_at = None
            call.notification_lease_token = None
            db.commit()
        else:
            db.rollback()
        return
    # Configuration edits cancel unclaimed work atomically. A submission already
    # in flight cannot be recalled, like any email accepted by an SMTP server.
    connection = active_connection(db, tenant_id) if gmail else None
    if gmail:
        authorized = bool(connection and connection.generation == call.notification_gmail_generation
                          and connection.recipient == call.notification_recipient)
    else:
        config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant_id).execution_options(populate_existing=True))
        authorized = bool(call.operating_mode == "answering_machine" and config and config.call_email_enabled
                          and config.call_email_recipient == call.notification_recipient)
    if call.content_deleted_at or not call.notification_recipient or not authorized:
        cancel_notification(call)
        db.commit()
        return
    if call.ended_at is None:
        db.rollback()
        return
    analysis_failed = call.analysis_status == "failed" and not call.analysis_next_attempt_at
    if analysis_failed and not gmail:
        call.notification_status = "canceled" if call.analysis_error_code == "transcript_empty" else "failed"
        call.notification_error_code = "no_message" if call.analysis_error_code == "transcript_empty" else "analysis_failed"
        db.commit()
        return
    if call.analysis_status != "completed" and not analysis_failed:
        db.rollback()
        return
    if not gmail and not any(turn.get("speaker") == "customer" and turn.get("state", "confirmed") == "confirmed" and turn.get("text", "").strip() for turn in call.transcript or []):
        call.notification_status = "canceled"
        call.notification_error_code = "no_message"
        call.notification_recipient = None
        db.commit()
        return
    if call.notification_next_attempt_at and _utc(call.notification_next_attempt_at) > now:
        db.rollback()
        return
    if not gmail and not mail_transport_available(settings) and adapter is None:
        call.notification_status = "failed"
        call.notification_error_code = "smtp_not_configured"
        db.commit()
        return
    token = str(uuid4())
    if gmail:
        encrypted_token = connection.encrypted_refresh_token
        sections = list(connection.sections)
        call_sid = call.provider_session_id
    else:
        message = render_call_mail(call, settings)
    call.notification_status = "sending"
    call.notification_attempts += 1
    call.notification_lease_token = token
    call.notification_next_attempt_at = now + timedelta(minutes=2)
    call.notification_error_code = None
    db.commit()
    failure = None
    try:
        if gmail:
            cost = await twilio_cost(settings, call_sid) if "cost" in sections else None
            call = locked_call()
            if call is None or call.notification_lease_token != token or call.content_deleted_at:
                db.rollback()
                return
            current = active_connection(db, tenant_id)
            if current is None or current.generation != call.notification_gmail_generation:
                cancel_notification(call)
                db.commit()
                return
            timezone_name = db.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id)) or "UTC"
            message = call_mail(call, settings, sections, timezone_name, cost)
            db.commit()
            refresh_token = CalendarTokenCipher(settings.calendar_token_encryption_key).decrypt(encrypted_token)
            await send_gmail(settings, refresh_token, message)
        else:
            await asyncio.to_thread((adapter or build_mail_adapter(settings)).send, message)
    except asyncio.CancelledError:
        # The thread might still submit; lease recovery must not resend.
        raise
    except MailDeliveryError as exc:
        failure = exc
    except CalendarError:
        failure = MailDeliveryError("Google-Verbindung muss erneuert werden.", code="gmail_reconnect_required")
    except Exception:
        transport = "gmail" if gmail else "smtp"
        failure = MailDeliveryError("Unklarer Zustellstatus", code=f"{transport}_unexpected_failure", uncertain=True)
    call = locked_call()
    if call is None or call.notification_lease_token != token or call.content_deleted_at:
        db.rollback()
        return
    call.notification_lease_token = None
    call.notification_next_attempt_at = None
    if failure is None:
        call.notification_status = "sent"
        call.notification_sent_at = datetime.now(timezone.utc)
    else:
        call.notification_error_code = failure.code
        call.notification_status = "uncertain" if failure.uncertain else "failed"
        if failure.retryable and not failure.uncertain and call.notification_attempts < 3:
            call.notification_status = "pending"
            call.notification_next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30 * 2 ** call.notification_attempts)
    db.commit()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
