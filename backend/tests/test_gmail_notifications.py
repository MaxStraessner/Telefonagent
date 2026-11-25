import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.encryption import CalendarTokenCipher
from app.main import app
from app.models import AppUser, CallChannel, Tenant
from app.models.entities import GmailNotification
from app.services import gmail
from app.services.call_lifecycle import CallLifecycleService
from app.services.call_notifications import process_call_notification
from app.services.gmail import GMAIL_SCOPE, SEND_URL, TOKEN_URL
from app.services.mail import MailDeliveryError, OutboundMail


@pytest.fixture
def google_settings():
    return Settings(
        google_calendar_client_id="test-client", google_calendar_client_secret="synthetic-client-secret",
        google_calendar_redirect_uri="http://testserver/api/v1/calendar/oauth/google/callback",
        calendar_token_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_gmail_oauth_scope_encryption_and_single_use(client, db, google_settings, monkeypatch):
    app.dependency_overrides[get_settings] = lambda: google_settings
    writes = []

    async def exchange(settings, code, verifier):
        writes.append((code, verifier))
        return "synthetic-user-refresh-token"

    monkeypatch.setattr("app.api.v1.notifications.exchange_code", exchange)
    assert client.put("/api/v1/notifications/gmail", json={
        "enabled": False, "recipient": "test@example.org", "sections": ["transcript", "summary"],
    }).is_success
    start = client.post("/api/v1/notifications/gmail/connect?activate=true")
    assert start.is_success
    params = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    assert params["scope"] == [GMAIL_SCOPE]
    assert params["include_granted_scopes"] == ["false"]
    assert params["access_type"] == ["offline"]
    assert params["code_challenge_method"] == ["S256"]
    path = "/api/v1/calendar/oauth/google/callback"
    oauth_params = {"state": params["state"][0], "code": "one-use-code"}
    # A different browser session cannot consume the otherwise valid state.
    other = TestClient(app)
    origin = {"Origin": "http://testserver", "X-Requested-With": "Telefonagent"}
    assert other.post("/api/v1/auth/login", headers=origin, json={
        "username": "owner@telefonagent.local", "password": "correct horse battery staple",
    }).is_success
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    assert other.post("/api/v1/auth/context", json={"company_id": str(tenant.id)}, headers={
        **origin, "X-CSRF-Token": other.cookies.get("telefonagent_csrf"),
    }).is_success
    response = other.get(path, params=oauth_params, follow_redirects=False)
    assert "gmail=error" in response.headers["location"] and not writes
    response = client.get(path, params=oauth_params, follow_redirects=False)
    assert "gmail=connected" in response.headers["location"]
    assert len(writes) == 1 and writes[0][1] != params["code_challenge"][0]
    assert "gmail=error" in client.get(path, params=oauth_params, follow_redirects=False).headers["location"]
    assert len(writes) == 1
    view = client.get("/api/v1/notifications/gmail").json()
    assert view["connected"] and view["enabled"] and view["recipient"] == "test@example.org"
    assert "token" not in str(view)
    row = db.scalar(select(GmailNotification).where(GmailNotification.recipient == "test@example.org"))
    assert row.encrypted_refresh_token != "synthetic-user-refresh-token"
    cipher = CalendarTokenCipher(google_settings.calendar_token_encryption_key)
    assert cipher.decrypt(row.encrypted_refresh_token) == "synthetic-user-refresh-token"
    assert row.encrypted_verifier is None
    assert client.delete("/api/v1/notifications/gmail").status_code == 204
    db.refresh(row)
    assert row.encrypted_refresh_token is None and not row.enabled


@pytest.mark.parametrize("mode", ["agent", "answering_machine"])
def test_gmail_queue_renders_and_retries_without_duplicate(db, google_settings, monkeypatch, mode):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    user = db.scalar(select(AppUser).where(AppUser.username == "owner@telefonagent.local"))
    cipher = CalendarTokenCipher(google_settings.calendar_token_encryption_key)
    connection = db.get(GmailNotification, (tenant.id, user.id))
    if connection is None:
        connection = GmailNotification(tenant_id=tenant.id, user_id=user.id)
        db.add(connection)
    connection.enabled = True
    connection.recipient = "gmail-queue@example.org"
    connection.sections = ["caller", "time", "summary", "concern", "tasks", "transcript", "cost"]
    connection.encrypted_refresh_token = cipher.encrypt("synthetic-refresh")
    db.commit()
    lifecycle = CallLifecycleService(db, tenant.id)
    call, _ = lifecycle.provision_provider_call(CallChannel.telephone, "CA" + uuid4().hex, None)
    call.operating_mode = mode
    call.ended_at = datetime.now(timezone.utc)
    call.analysis_status = "completed"
    call.caller_name = "<Beispiel>"
    call.caller_phone = "+49123"
    call.duration_seconds = 125
    call.summary = "Eine Zusammenfassung"
    call.analysis_details = {"main_concern": "Frage zum Termin", "callback_required": True}
    call.priority = "urgent"
    call.action_items = ["Termin prüfen"]
    call.transcript = [{"speaker": "customer", "text": "<img src=x> Frage zum Termin"}]
    db.commit()
    sent = []

    async def mail(settings, refresh, message):
        assert refresh == "synthetic-refresh"
        sent.append(message)
        if len(sent) == 1:
            raise MailDeliveryError("Temporär", code="gmail_send_rejected", retryable=True)

    async def cost(*args):
        return "0,02 EUR"

    monkeypatch.setattr("app.services.call_notifications.send_gmail", mail)
    monkeypatch.setattr("app.services.call_notifications.twilio_cost", cost)
    try:
        asyncio.run(process_call_notification(db, google_settings, tenant.id, call.id))
        assert call.notification_status == "pending" and call.notification_attempts == 1
        call.notification_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        asyncio.run(process_call_notification(db, google_settings, tenant.id, call.id))
        assert call.notification_status == "sent"
        asyncio.run(process_call_notification(db, google_settings, tenant.id, call.id))
        assert len(sent) == 2 and sent[0].message_id == sent[1].message_id
        assert sent[1].recipient == connection.recipient
        for value in ("&lt;Beispiel&gt;", "+49123", "2 Min. 05 Sek.", "Eine Zusammenfassung",
                      "Frage zum Termin", "Dringend", "Rückruf gewünscht: Ja", "Termin prüfen", "&lt;img", "0,02 EUR"):
            assert value in sent[1].html
        assert "<img src=x>" not in sent[1].html
    finally:
        db.delete(call)
        connection.enabled = False
        connection.encrypted_refresh_token = None
        db.commit()


def test_gmail_api_mime_and_unknown_submission(google_settings, monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def response(request):
        requests.append(request)
        if request.url == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "synthetic-access", "scope": GMAIL_SCOPE})
        assert request.url == SEND_URL
        raise httpx.ReadTimeout("simulated", request=request)

    monkeypatch.setattr(gmail.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(response), **kwargs,
    ))
    message = OutboundMail("test@example.org", "Bericht", "Text", "<example@telefonagent.local>", "<b>HTML</b>")
    with pytest.raises(MailDeliveryError) as error:
        asyncio.run(gmail.send_gmail(google_settings, "refresh", message))
    assert error.value.uncertain and not error.value.retryable
    assert len(requests) == 2
    raw = json.loads(requests[1].content)["raw"]
    decoded = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    assert decoded["To"] == "test@example.org"
    assert decoded.get_body(preferencelist=("html",)).get_content().strip() == "<b>HTML</b>"


def test_dedicated_gmail_oauth_client_does_not_change_calendar_settings(google_settings):
    settings = google_settings.model_copy(update={
        "app_base_url": "https://test.example.org",
        "gmail_client_id": "synthetic-personal-client",
        "gmail_client_secret": "synthetic-personal-secret",
    })
    params = parse_qs(urlsplit(gmail.authorization_url(settings, "state", "challenge")).query)
    assert params["client_id"] == ["synthetic-personal-client"]
    assert params["scope"] == [GMAIL_SCOPE]
    assert params["redirect_uri"] == ["https://test.example.org/api/v1/calendar/oauth/google/callback"]
    assert settings.google_calendar_client_id == "test-client"
    assert gmail.oauth_client(settings.model_copy(update={"gmail_client_secret": None})) is None
