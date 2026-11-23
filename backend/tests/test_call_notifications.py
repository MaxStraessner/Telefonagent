import asyncio
import ssl
from datetime import datetime, timedelta, timezone
from smtplib import SMTPDataError, SMTPRecipientsRefused
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.api.dependencies import TenantContext, UserContext, get_user_context
from app.core.config import Settings, get_settings
from app.main import app
from app.models import AgentConfiguration, CallChannel, CallSession, Tenant, TenantRole
from app.schemas.calls import CallAnalysisResult
from app.services.agent_runtime import build_runtime_config
from app.services.call_lifecycle import CallLifecycleService
from app.services.call_notifications import process_call_notification
from app.services.call_processing import erase_call_content, process_tenant_calls
from app.services.mail import MailDeliveryError, OutboundMail, SmtpMailAdapter


@pytest.fixture
def opted_in(db):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant.id))
    original = config.operating_mode, config.call_email_enabled, config.call_email_recipient
    config.operating_mode = "answering_machine"
    config.call_email_enabled = True
    config.call_email_recipient = "owner@example.org"
    db.commit()
    created = []

    def new(**changes):
        now = datetime.now(timezone.utc)
        values = dict(tenant_id=tenant.id, channel=CallChannel.telephone, provider_session_id="CA" + uuid4().hex,
                      operating_mode="answering_machine", started_at=now, connected_at=now, ended_at=now,
                      status="ended", documentation_status="complete", analysis_status="completed",
                      summary="Ein Rückruf ist gewünscht.", analysis_details={"callback_required": True},
                      transcript=[{"speaker": "customer", "text": "Geheimer vollständiger Wortlaut", "state": "confirmed"}],
                      notification_status="waiting_analysis", notification_recipient=config.call_email_recipient)
        values.update(changes)
        call = CallSession(**values)
        db.add(call)
        db.commit()
        created.append(call.id)
        return call
    yield tenant, config, new, created
    db.rollback()
    config = db.get(AgentConfiguration, config.id)
    db.refresh(config)
    config.operating_mode, config.call_email_enabled, config.call_email_recipient = original
    db.execute(delete(CallSession).where(CallSession.id.in_(created)))
    db.commit()


class Mailbox:
    def __init__(self, failure=None):
        self.messages = []
        self.failure = failure

    def send(self, message):
        self.messages.append(message)
        if self.failure:
            raise self.failure


def test_new_calls_only_mode_snapshot_and_no_recipient_in_runtime(db, opted_in):
    tenant, config, new, created = opted_in
    old = new(notification_status="not_requested", notification_recipient=None)
    runtime = build_runtime_config(db, TenantContext(id=tenant.id, tenant=tenant), Settings(), test_mode=False)
    assert config.call_email_recipient not in str(runtime.manifest.model_dump()) + runtime.prompt
    service = CallLifecycleService(db, tenant.id)
    call, fresh = service.provision_provider_call(CallChannel.telephone, "CA" + uuid4().hex, runtime)
    created.append(call.id)
    assert fresh and call.notification_status == "waiting_analysis"
    duplicate, fresh = service.provision_provider_call(CallChannel.telephone, call.provider_session_id, runtime)
    assert not fresh and duplicate.id == call.id
    assert old.notification_status == "not_requested"
    config.operating_mode = "agent"
    db.commit()
    runtime = build_runtime_config(db, TenantContext(id=tenant.id, tenant=tenant), Settings(), test_mode=False)
    call, _ = service.provision_provider_call(CallChannel.telephone, "CA" + uuid4().hex, runtime)
    created.append(call.id)
    assert call.notification_status == "not_requested"
    assert call.notification_recipient is None


def test_analysis_worker_sends_once_and_history_uses_same_result(db, client, opted_in, monkeypatch):
    tenant, _, new, _ = opted_in
    call = new(analysis_status="pending", summary=None)
    mailbox = Mailbox()
    async def analysis(*args):
        return CallAnalysisResult(category="callback", summary="Frische Zusammenfassung", result="Nachricht", action_required=True, action_items=[], priority="normal", contact_email="attacker@example.org", callback_required=True)
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", analysis)
    monkeypatch.setattr("app.services.call_notifications.build_mail_adapter", lambda _: mailbox)
    settings = Settings(smtp_host="smtp.example.org", smtp_from_address="agent@example.org")
    asyncio.run(process_tenant_calls(db, settings, tenant.id))
    asyncio.run(process_tenant_calls(db, settings, tenant.id))
    assert len(mailbox.messages) == 1
    message = mailbox.messages[0]
    assert message.recipient == "owner@example.org"
    assert "Frische Zusammenfassung" in message.text
    assert "Geheimer vollständiger Wortlaut" not in message.text
    assert f"/anrufe/{call.id}" in message.text
    detail = client.get(f"/api/v1/calls/{call.id}").json()
    assert detail["notification_status"] == "sent"
    assert detail["summary"] == detail["summary_preview"] == "Frische Zusammenfassung"
    assert detail["transcript"][0]["text"] == "Geheimer vollständiger Wortlaut"


@pytest.mark.parametrize("failure, expected", [
    (MailDeliveryError("Sensitive response", code="smtp_rejected", retryable=True), "pending"),
    (MailDeliveryError("Sensitive response", code="smtp_rejected"), "failed"),
    (MailDeliveryError("Sensitive response", code="smtp_uncertain", uncertain=True), "uncertain"),
    (RuntimeError("Sensitive response"), "uncertain"),
])
def test_delivery_failure_classification_and_bounded_retries(db, opted_in, failure, expected):
    tenant, _, new, _ = opted_in
    call = new()
    mailbox = Mailbox(failure)
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    db.refresh(call)
    assert call.notification_status == expected
    assert "Sensitive" not in (call.notification_error_code or "")
    for _ in range(4):
        call.notification_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert len(mailbox.messages) == (3 if expected == "pending" else 1)
    assert call.notification_status != "pending"


def test_expired_submission_lease_is_uncertain_without_resending(db, opted_in):
    tenant, _, new, _ = opted_in
    call = new(notification_status="sending", notification_lease_token=str(uuid4()), notification_next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    mailbox = Mailbox()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert call.notification_status == "uncertain" and not mailbox.messages


def test_greeting_without_caller_message_does_not_send_summary(db, opted_in):
    tenant, _, new, _ = opted_in
    call = new(transcript=[{"speaker": "agent", "text": "Hallo"}])
    mailbox = Mailbox()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert call.notification_status == "canceled" and call.notification_error_code == "no_message"
    assert not mailbox.messages


@pytest.mark.parametrize("change", ["disabled", "recipient", "deleted", "empty", "analysis_failed"])
def test_cancels_ineligible_work_without_sending(db, opted_in, change):
    tenant, config, new, _ = opted_in
    call = new()
    if change == "disabled":
        config.call_email_enabled = False
    elif change == "recipient":
        config.call_email_recipient = "new@example.org"
    elif change == "deleted":
        erase_call_content(call)
    else:
        call.analysis_status = "failed"
        call.analysis_error_code = "transcript_empty" if change == "empty" else "analysis_invalid_response"
    db.commit()
    mailbox = Mailbox()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert call.notification_status == ("failed" if change == "analysis_failed" else "canceled")
    assert not mailbox.messages


def test_late_correction_waits_for_reanalysis_but_does_not_resend(db, opted_in):
    tenant, _, new, _ = opted_in
    call = new()
    service = CallLifecycleService(db, tenant.id)
    service.save_segment(call.id, {"speaker": "customer", "text": "Korrektur", "segment_id": "late"})
    mailbox = Mailbox()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert not mailbox.messages
    call.analysis_status = "completed"
    call.summary = "Korrigierte Zusammenfassung"
    db.commit()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    service.save_segment(call.id, {"speaker": "customer", "text": "Noch eine Korrektur", "segment_id": "later"})
    call.analysis_status = "completed"
    db.commit()
    asyncio.run(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
    assert len(mailbox.messages) == 1 and "Korrigierte" in mailbox.messages[0].text


def test_concurrent_workers_and_deletion_during_submission(db, opted_in):
    tenant, _, new, _ = opted_in
    call = new()
    entered, release = Event(), Event()
    class BlockingMailbox(Mailbox):
        def send(self, message):
            entered.set()
            assert release.wait(5)
            super().send(message)
    mailbox = BlockingMailbox()
    async def run():
        task = asyncio.create_task(process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await process_call_notification(db, Settings(), tenant.id, call.id, adapter=mailbox)
            erase_call_content(call)
            db.commit()
        finally:
            release.set()
            await task
    asyncio.run(run())
    assert len(mailbox.messages) == 1
    assert call.notification_status == "canceled" and call.notification_recipient is None
    assert call.summary is None and call.notification_sent_at is None


def write_payload(client):
    response = client.get("/api/v1/agent/config").json()
    return {**{k: v for k, v in response.items() if k not in {"tenant_id", "version", "updated_at", "can_edit", "role", "call_email_transport_available"}}, "expected_version": response["version"]}


@pytest.mark.parametrize("recipient", ["x@example.org\r\nBcc: stolen@example.org", "a@example.org,b@example.org", "User <a@example.org>", "bad", ".bad@example.org", "bad.@example.org", "bad@-invalid.example.org", None])
def test_config_rejects_invalid_enabled_recipients(client, recipient):
    payload = write_payload(client)
    assert client.put("/api/v1/agent/config", json={**payload, "call_email_enabled": True, "call_email_recipient": recipient}).status_code == 422


def test_config_transport_gate_and_immediate_cancellation(db, client, opted_in):
    _, _, new, _ = opted_in
    call = new()
    app.dependency_overrides[get_settings] = lambda: Settings(mail_enabled=False)
    payload = write_payload(client)
    assert client.put("/api/v1/agent/config", json=payload).status_code == 409
    assert client.put("/api/v1/agent/config", json={**payload, "call_email_enabled": False}).status_code == 200
    db.refresh(call)
    assert call.notification_status == "canceled" and call.notification_recipient is None


def test_config_can_enable_prepared_smtp_without_sending_mail(db, client, opted_in):
    _, config, _, _ = opted_in
    config.call_email_enabled = False
    db.commit()
    app.dependency_overrides[get_settings] = lambda: Settings(smtp_host="smtp.example.org", smtp_from_address="agent@example.org")
    payload = write_payload(client)
    response = client.put("/api/v1/agent/config", json={**payload, "call_email_enabled": True, "call_email_recipient": " office@example.org "})
    assert response.status_code == 200
    assert response.json()["call_email_enabled"] is True
    assert response.json()["call_email_recipient"] == "office@example.org"
    assert response.json()["call_email_transport_available"] is True


def test_implicit_smtp_tls_uses_verified_context(monkeypatch):
    seen = []
    class Connection:
        def __init__(self, host, port, *, timeout, context):
            assert port == 465 and timeout == 10
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def login(self, username, password):
            seen.append("login")
        def send_message(self, message):
            seen.append("send")
    monkeypatch.setattr("app.services.mail.SMTP_SSL", Connection)
    settings = Settings(smtp_host="smtp.example.org", smtp_from_address="agent@example.org", smtp_port=465, smtp_use_ssl=True, smtp_starttls=False, smtp_username="synthetic", smtp_password="synthetic")
    SmtpMailAdapter(settings).send(OutboundMail("owner@example.org", "Test", "Synthetic"))
    assert seen == ["login", "send"]


def test_manual_retry_requires_admin_and_current_opt_in(db, client, opted_in):
    tenant, config, new, _ = opted_in
    app.dependency_overrides[get_settings] = lambda: Settings(smtp_host="smtp.example.org", smtp_from_address="agent@example.org")
    call = new(notification_status="uncertain", notification_error_code="smtp_uncertain")
    response = client.post(f"/api/v1/calls/{call.id}/notification/retry")
    assert response.status_code == 200
    assert response.json()["notification_status"] == "pending"
    assert client.post(f"/api/v1/calls/{call.id}/notification/retry").status_code == 409
    other = new(notification_status="not_requested", notification_recipient=None)
    assert client.post(f"/api/v1/calls/{other.id}/notification/retry").status_code == 409
    call.notification_status = "failed"
    config.call_email_recipient = "different@example.org"
    db.commit()
    assert client.post(f"/api/v1/calls/{call.id}/notification/retry").status_code == 409


def test_foreign_tenant_cannot_claim_notification(db, opted_in):
    _, _, new, _ = opted_in
    call = new()
    mailbox = Mailbox()
    asyncio.run(process_call_notification(db, Settings(), uuid4(), call.id, adapter=mailbox))
    assert not mailbox.messages and call.notification_status == "waiting_analysis"


def test_company_member_cannot_retry_delivery(client, opted_in):
    _, _, new, _ = opted_in
    call = new(notification_status="uncertain")
    app.dependency_overrides[get_user_context] = lambda: UserContext(id=uuid4(), email="member@example.test", role=TenantRole.company_user)
    assert client.post(f"/api/v1/calls/{call.id}/notification/retry").status_code == 403


@pytest.mark.parametrize("failure_phase", ["connect", "data", "temporary", "permanent", "recipients", "quit", "ok"])
def test_smtp_tls_and_delivery_certainty(monkeypatch, failure_phase):
    class SMTP:
        def __init__(self, *args, **kwargs):
            assert kwargs["timeout"] == 10
            if failure_phase == "connect":
                raise OSError("private")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            if failure_phase == "quit":
                raise OSError("private")
        def starttls(self, *, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        def send_message(self, message):
            assert message["Message-ID"] == "<test@example.org>"
            if failure_phase == "data":
                raise TimeoutError("private")
            if failure_phase in {"temporary", "permanent"}:
                raise SMTPDataError(451 if failure_phase == "temporary" else 550, b"private")
            if failure_phase == "recipients":
                raise SMTPRecipientsRefused({"owner@example.org": (550, b"private")})
    monkeypatch.setattr("app.services.mail.SMTP", SMTP)
    adapter = SmtpMailAdapter(Settings(smtp_host="smtp.example.org", smtp_from_address="agent@example.org"))
    message = OutboundMail("owner@example.org", "Test", "Nur synthetisch", "<test@example.org>")
    if failure_phase in {"ok", "quit"}:
        adapter.send(message)
    else:
        with pytest.raises(MailDeliveryError) as caught:
            adapter.send(message)
        assert caught.value.uncertain == (failure_phase == "data")
        assert caught.value.retryable == (failure_phase in {"connect", "temporary"})
        assert "private" not in str(caught.value)
