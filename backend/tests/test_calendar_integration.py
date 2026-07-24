import asyncio
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.api.dependencies import TenantContext, UserContext, get_tenant_context, get_user_context
from app.calendar.errors import CalendarProviderError
from app.calendar.providers import (
    BusyInterval,
    CalendarProvider,
    CreatedEvent,
    OAuthTokens,
    ProviderAccount,
    ProviderCalendar,
    ProviderEvent,
)
from app.core.config import Settings, get_settings
from app.core.encryption import CalendarTokenCipher
from app.main import app
from app.models import (
    AppUser,
    AvailabilitySnapshot,
    BookingConfiguration,
    BookingConversation,
    CalendarAppointmentType,
    CalendarBooking,
    CalendarBusinessHour,
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarLocationType,
    CalendarOAuthState,
    CalendarProviderName,
    CallChannel,
    CallSession,
    ExternalCalendar,
    Service,
    Tenant,
    TenantRole,
    ToolExecution,
)
from app.services.calendar_connections import test_connection as run_connection_test
from app.services.calendar_connections import valid_access_token

TEST_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def calendar_settings(**overrides):
    values = {
        "database_url": "sqlite:///./test.db",
        "calendar_token_encryption_key": TEST_KEY,
        "google_calendar_client_id": "google-client",
        "google_calendar_client_secret": "google-secret",
        "google_calendar_redirect_uri": "http://testserver/api/v1/calendar/oauth/google/callback",
        "microsoft_calendar_client_id": "microsoft-client",
        "microsoft_calendar_client_secret": "microsoft-secret",
        "microsoft_calendar_redirect_uri": "http://testserver/api/v1/calendar/oauth/microsoft/callback",
        "frontend_url": "http://frontend.test",
    }
    values.update(overrides)
    return Settings(**values)


class FakeProvider(CalendarProvider):
    def __init__(self):
        self.busy: list[BusyInterval] = []
        self.created_events = []
        self.refreshes = 0
        self.revoked = False
        self.fail_create = False
        self.oauth_refresh_token: str | None = "oauth-refresh"
        self.account_email = "calendar@example.test"
        self.calendars = [ProviderCalendar("external-1", "Hauptkalender", "Europe/Berlin", "Owner", "owner", True, True)]
        self.last_busy_calendar_ids: list[str] | None = None
        self.events: list[ProviderEvent] = []

    def build_authorization_url(self, state, code_challenge):
        return f"https://provider.test/auth?state={state}&challenge={code_challenge}"

    async def exchange_authorization_code(self, code, code_verifier):
        assert code == "valid-code"
        assert code_verifier
        return OAuthTokens("oauth-access", self.oauth_refresh_token, datetime.now(timezone.utc) + timedelta(hours=1), ["calendar"])

    async def refresh_access_token(self, refresh_token):
        assert refresh_token == "refresh-token"
        self.refreshes += 1
        return OAuthTokens("refreshed-access", "refreshed-refresh", datetime.now(timezone.utc) + timedelta(hours=1), ["calendar"])

    async def get_account_information(self, access_token):
        return ProviderAccount("provider-account", self.account_email, "Kalenderkonto")

    async def list_calendars(self, access_token):
        return self.calendars

    async def get_busy_intervals(self, access_token, calendar_ids, start, end):
        assert calendar_ids
        self.last_busy_calendar_ids = calendar_ids
        return list(self.busy)

    async def create_event(self, access_token, calendar_id, event):
        if self.fail_create:
            raise CalendarProviderError("provider_unavailable", "temporär", transient=True)
        self.created_events.append(event)
        return CreatedEvent(f"event-{len(self.created_events)}", "provider-reference")

    async def list_events(self, access_token, calendar_id, start, end):
        return list(self.events)

    async def revoke_connection(self, access_token, refresh_token):
        self.revoked = True


@pytest.fixture()
def calendar_env(db, monkeypatch):
    for model in (
        CalendarBooking,
        ExternalCalendar,
        CalendarOAuthState,
        CalendarConnection,
        CalendarAppointmentType,
        CalendarBusinessHour,
        BookingConfiguration,
    ):
        db.execute(delete(model))
    db.commit()
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    owner = db.scalar(select(AppUser).where(AppUser.email == "owner@telefonagent.local"))
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(id=tenant.id, tenant=tenant)
    app.dependency_overrides[get_user_context] = lambda: UserContext(id=owner.id, email=owner.email, role=TenantRole.owner)
    settings = calendar_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    provider = FakeProvider()
    for path in (
        "app.services.calendar_oauth.create_calendar_provider",
        "app.services.calendar_connections.create_calendar_provider",
        "app.services.availability.create_calendar_provider",
        "app.services.calendar_booking.create_calendar_provider",
        "app.services.calendar_agenda.create_calendar_provider",
    ):
        monkeypatch.setattr(path, lambda *_args, **_kwargs: provider)
    yield tenant, owner, settings, provider
    app.dependency_overrides.clear()
    for model in (
        CalendarBooking,
        ExternalCalendar,
        CalendarOAuthState,
        CalendarConnection,
        CalendarAppointmentType,
        CalendarBusinessHour,
        BookingConfiguration,
    ):
        db.execute(delete(model))
    db.commit()


def create_connected_calendar(db, tenant, owner, settings, *, can_write=True):
    cipher = CalendarTokenCipher(settings.calendar_token_encryption_key)
    connection = CalendarConnection(
        tenant_id=tenant.id,
        created_by_user_id=owner.id,
        provider=CalendarProviderName.google,
        provider_account_id=f"account-{uuid4()}",
        account_email="calendar@example.test",
        display_name="Kalenderkonto",
        encrypted_access_token=cipher.encrypt("access-token"),
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        granted_scopes=["calendar"],
        connection_status=CalendarConnectionStatus.connected,
    )
    db.add(connection)
    db.flush()
    calendar = ExternalCalendar(
        tenant_id=tenant.id,
        calendar_connection_id=connection.id,
        external_calendar_id="external-1",
        calendar_name="Hauptkalender",
        calendar_timezone="Europe/Berlin",
        owner_name="Owner",
        access_role="owner" if can_write else "reader",
        is_primary=True,
        can_write=can_write,
        is_selected_for_availability=True,
        is_selected_for_booking=True,
    )
    service = Service(
        tenant_id=tenant.id,
        name=f"Beratung {uuid4().hex[:5]}",
        description="Persönliche Beratung",
        duration_minutes=30,
        is_active=True,
    )
    db.add(service)
    db.flush()
    appointment = CalendarAppointmentType(
        tenant_id=tenant.id,
        service_id=service.id,
        name=service.name,
        description="Persönliche Beratung",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        location_type=CalendarLocationType.phone,
        location_text="",
        is_active=True,
    )
    config = BookingConfiguration(
        tenant_id=tenant.id,
        timezone="Europe/Berlin",
        slot_interval_minutes=15,
        minimum_notice_minutes=0,
        maximum_booking_horizon_days=60,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        maximum_suggestions_per_request=3,
    )
    db.add_all([calendar, appointment, config])
    db.add(CalendarBusinessHour(tenant_id=tenant.id, weekday=0, start_time=time(9), end_time=time(12), is_active=True))
    db.commit()
    return connection, calendar, appointment


def prepare_conversation(
    client,
    db,
    tenant,
    appointment,
    *,
    prefix: str,
    hour: int = 9,
    customer_name: str = "Max Mustermann",
):
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    assert client.post("/api/v1/calendar/tools/resolve-service", json={
        "session_id": str(call.id), "tool_call_id": f"{prefix}-service",
        "service_name": appointment.name,
    }).json()["success"] is True
    assert client.post("/api/v1/calendar/tools/resolve-booking-datetime", json={
        "session_id": str(call.id), "tool_call_id": f"{prefix}-datetime",
        "expression": f"3. August 2026 um {hour} Uhr",
    }).json()["status"] == "concrete"
    checked = client.post("/api/v1/calendar/tools/check-appointment-availability/session", json={
        "session_id": str(call.id), "tool_call_id": f"{prefix}-availability",
        "appointment_type_id": str(appointment.id),
    })
    assert checked.status_code == 200, checked.text
    assert checked.json()["available"] is True
    prepared = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": f"{prefix}-prepare",
        "customer_name": customer_name, "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert prepared.status_code == 200, prepared.text
    return call, prepared.json()


def delete_conversation_records(db, call):
    db.execute(delete(ToolExecution).where(ToolExecution.call_session_id == call.id))
    db.execute(delete(AvailabilitySnapshot).where(AvailabilitySnapshot.call_session_id == call.id))
    db.execute(delete(BookingConversation).where(BookingConversation.call_session_id == call.id))
    db.execute(delete(CalendarBooking).where(CalendarBooking.conversation_session_id == call.id))
    db.delete(call)
    db.commit()


def test_provider_configuration_never_exposes_secrets(client, calendar_env):
    response = client.get("/api/v1/calendar/connections")
    assert response.status_code == 200
    assert all(item["configured"] for item in response.json()["providers"])
    assert "google-secret" not in response.text
    assert "microsoft-secret" not in response.text
    assert TEST_KEY not in response.text


def test_oauth_callback_is_single_use_encrypted_and_synchronizes_calendars(client, db, calendar_env):
    _, _, _, _provider = calendar_env
    started = client.post("/api/v1/calendar/oauth/google/start")
    assert started.status_code == 200
    authorization_url = started.json()["authorization_url"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    stored_state = db.scalar(select(CalendarOAuthState))
    assert state not in stored_state.state_hash
    assert "oauth" not in stored_state.encrypted_code_verifier

    callback = client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"state": state, "code": "valid-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "calendar_oauth=success" in callback.headers["location"]
    connection = db.scalar(select(CalendarConnection))
    db.refresh(connection)
    assert connection.encrypted_access_token != "oauth-access"
    assert db.scalar(select(ExternalCalendar)).calendar_name == "Hauptkalender"

    repeated = client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"state": state, "code": "valid-code"},
        follow_redirects=False,
    )
    assert "oauth_state_invalid" in repeated.headers["location"]


def test_invalid_oauth_state_and_access_denial_are_controlled(client, calendar_env):
    invalid = client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"state": "invalid", "code": "valid-code"},
        follow_redirects=False,
    )
    assert "oauth_state_invalid" in invalid.headers["location"]
    started = client.post("/api/v1/calendar/oauth/google/start").json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    denied = client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert "oauth_access_denied" in denied.headers["location"]


def test_reauthorization_updates_existing_provider_account(client, db, calendar_env):
    for _attempt in range(2):
        started = client.post("/api/v1/calendar/oauth/google/start").json()
        state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
        callback = client.get(
            "/api/v1/calendar/oauth/google/callback",
            params={"state": state, "code": "valid-code"},
            follow_redirects=False,
        )
        assert "calendar_oauth=success" in callback.headers["location"]

    connections = list(db.scalars(select(CalendarConnection)))
    assert len(connections) == 1
    assert connections[0].provider_account_id == "provider-account"
    assert connections[0].connection_status == CalendarConnectionStatus.connected


def test_reauthorization_without_refresh_token_preserves_existing_token(client, db, calendar_env):
    _, _, settings, provider = calendar_env
    for refresh_token in ("oauth-refresh", None):
        provider.oauth_refresh_token = refresh_token
        state = parse_qs(urlparse(client.post("/api/v1/calendar/oauth/google/start").json()["authorization_url"]).query)["state"][0]
        response = client.get(
            "/api/v1/calendar/oauth/google/callback",
            params={"state": state, "code": "valid-code"},
            follow_redirects=False,
        )
        assert "calendar_oauth=success" in response.headers["location"]

    connection = db.scalar(select(CalendarConnection))
    assert CalendarTokenCipher(settings.calendar_token_encryption_key).decrypt(connection.encrypted_refresh_token) == "oauth-refresh"


def test_google_alias_email_updates_the_same_provider_account(client, db, calendar_env):
    _, _, _, provider = calendar_env
    for email in ("max.straessner@gmail.com", "max.straessner@googlemail.com"):
        provider.account_email = email
        state = parse_qs(urlparse(client.post("/api/v1/calendar/oauth/google/start").json()["authorization_url"]).query)["state"][0]
        response = client.get(
            "/api/v1/calendar/oauth/google/callback",
            params={"state": state, "code": "valid-code"},
            follow_redirects=False,
        )
        assert "calendar_oauth=success" in response.headers["location"]

    connections = list(db.scalars(select(CalendarConnection)))
    assert len(connections) == 1
    assert connections[0].provider_account_id == "provider-account"
    assert connections[0].account_email == "max.straessner@googlemail.com"


def test_expired_access_token_is_refreshed_and_reencrypted(db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    connection, _, _ = create_connected_calendar(db, tenant, owner, settings)
    connection.access_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    token = asyncio.run(valid_access_token(db, connection, settings))
    assert token == "refreshed-access"
    assert provider.refreshes == 1
    assert CalendarTokenCipher(TEST_KEY).decrypt(connection.encrypted_refresh_token) == "refreshed-refresh"


def test_connection_prefers_primary_calendar_when_nothing_is_selected(db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    connection, calendar, _ = create_connected_calendar(db, tenant, owner, settings)
    calendar.is_selected_for_availability = False
    provider.calendars = [
        ProviderCalendar("secondary", "Feiertage", "Europe/Berlin", "", "reader", False, False),
        ProviderCalendar("external-1", "Hauptkalender", "Europe/Berlin", "Owner", "owner", True, True),
    ]
    db.commit()

    found, readable, _, _ = asyncio.run(run_connection_test(db, connection, settings))

    assert found == 2
    assert readable == 1
    assert provider.last_busy_calendar_ids == ["external-1"]


def test_temporary_refresh_failure_sets_technical_error_not_reauthorization(db, calendar_env, monkeypatch):
    tenant, owner, settings, provider = calendar_env
    connection, _, _ = create_connected_calendar(db, tenant, owner, settings)
    connection.access_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    async def unavailable(_refresh_token):
        raise CalendarProviderError("provider_unavailable", "temporär", transient=True)

    monkeypatch.setattr(provider, "refresh_access_token", unavailable)
    with pytest.raises(Exception, match="aktualisiert"):
        asyncio.run(valid_access_token(db, connection, settings))
    db.refresh(connection)
    assert connection.connection_status == CalendarConnectionStatus.error
    assert connection.last_error_code == "provider_unavailable"


def test_undecryptable_token_requires_controlled_reauthorization(db, calendar_env):
    tenant, owner, settings, _ = calendar_env
    connection, _, _ = create_connected_calendar(db, tenant, owner, settings)
    connection.encrypted_access_token = CalendarTokenCipher("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDE=").encrypt("other-key-token")
    db.commit()

    with pytest.raises(Exception, match="entschl"):
        asyncio.run(valid_access_token(db, connection, settings))
    db.refresh(connection)
    assert connection.connection_status == CalendarConnectionStatus.reauthorization_required
    assert connection.last_error_code == "token_decryption_failed"


def test_disconnect_revokes_provider_and_removes_local_credentials(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    connection, _, _ = create_connected_calendar(db, tenant, owner, settings)

    response = client.delete(f"/api/v1/calendar/connections/{connection.id}")

    assert response.status_code == 204
    assert provider.revoked is True
    assert db.scalar(select(CalendarConnection).where(CalendarConnection.id == connection.id)) is None
    assert db.scalar(select(ExternalCalendar).where(ExternalCalendar.calendar_connection_id == connection.id)) is None


def test_calendar_selection_requires_one_writable_target_and_persists(client, db, calendar_env):
    tenant, owner, settings, _ = calendar_env
    _, calendar, _ = create_connected_calendar(db, tenant, owner, settings)
    invalid = client.put(
        "/api/v1/calendar/configuration/calendars",
        json={"calendars": [{"calendar_id": str(calendar.id), "is_selected_for_availability": True, "is_selected_for_booking": False}]},
    )
    assert invalid.status_code == 400
    calendar.can_write = False
    db.commit()
    unwritable = client.put(
        "/api/v1/calendar/configuration/calendars",
        json={"calendars": [{"calendar_id": str(calendar.id), "is_selected_for_availability": True, "is_selected_for_booking": True}]},
    )
    assert unwritable.json()["error"]["code"] == "booking_calendar_not_writable"
    calendar.can_write = True
    db.commit()
    saved = client.put(
        "/api/v1/calendar/configuration/calendars",
        json={"calendars": [{"calendar_id": str(calendar.id), "is_selected_for_availability": True, "is_selected_for_booking": True}]},
    )
    assert saved.status_code == 200
    assert saved.json()[0]["is_selected_for_booking"] is True


def test_configuration_and_appointment_types_are_real_crud_without_examples(client, calendar_env):
    configuration = client.get("/api/v1/calendar/configuration")
    assert configuration.status_code == 200
    assert configuration.json()["timezone"] == "Europe/Berlin"
    assert client.get("/api/v1/calendar/appointment-types").json() == []
    service_id = client.get("/api/v1/services").json()[0]["id"]
    created = client.post(
        "/api/v1/calendar/appointment-types",
        json={"service_id": service_id, "buffer_before_minutes": 5, "buffer_after_minutes": 10, "location_type": "phone", "location_text": "", "is_active": True},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    updated = client.put(
        f"/api/v1/calendar/appointment-types/{item_id}",
        json={"service_id": service_id, "buffer_before_minutes": 5, "buffer_after_minutes": 10, "location_type": "phone", "location_text": "", "is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert client.delete(f"/api/v1/calendar/appointment-types/{item_id}").status_code == 204


def test_service_crud_supports_deactivation_without_hard_delete(client, calendar_env):
    created = client.post(
        "/api/v1/services",
        json={"name": f"Damenhaarschnitt {uuid4().hex[:5]}", "description": "Waschen und Schneiden", "duration_minutes": 60, "is_active": True},
    )
    assert created.status_code == 201
    item = created.json()
    updated = client.put(
        f"/api/v1/services/{item['id']}",
        json={"name": item["name"], "description": item["description"], "duration_minutes": 60, "is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert any(service["id"] == item["id"] for service in client.get("/api/v1/services").json())


def test_availability_booking_idempotency_and_provider_confirmation(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    search = client.post(
        "/api/v1/calendar/tools/find-available-appointments",
        json={"appointment_type_id": str(appointment.id), "preferred_date": "2026-08-03", "preferred_time_of_day": "morning", "search_days": 1},
    )
    assert search.status_code == 200, search.text
    slot = search.json()["slots"][0]
    payload = {
        "slot_id": slot["slot_id"], "appointment_type_id": str(appointment.id),
        "customer_name": "Max Mustermann", "customer_phone": "+49123456",
        "customer_email": "max@example.test", "customer_notes": "Bitte anrufen",
        "idempotency_key": "call-12345678",
    }
    booked = client.post("/api/v1/calendar/tools/create-calendar-booking", json=payload)
    assert booked.status_code == 200
    assert booked.json()["success"] is True
    assert booked.json()["status"] == "confirmed"
    assert len(provider.created_events) == 1
    assert "Interne Termin ID:" in provider.created_events[0].description
    repeated = client.post("/api/v1/calendar/tools/create-calendar-booking", json=payload)
    assert repeated.json()["booking_id"] == booked.json()["booking_id"]
    assert len(provider.created_events) == 1


def test_new_agent_tools_validate_service_snapshot_and_confirmation(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    checked = client.post(
        "/api/v1/calendar/tools/check-appointment-availability",
        json={
            "service_id": str(appointment.service_id),
            "appointment_type_id": str(appointment.id),
            "requested_start": "2026-08-03T09:00:00+02:00",
            "timezone": "Europe/Berlin",
        },
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["available"] is True
    payload = {
        "service_id": str(appointment.service_id),
        "appointment_type_id": str(appointment.id),
        "customer_name": "Max Mustermann",
        "customer_phone": None,
        "customer_email": None,
        "start_at": "2026-08-03T09:00:00+02:00",
        "timezone": "Europe/Berlin",
        "idempotency_key": "new-agent-tool-1234",
        "confirmed": True,
    }
    booked = client.post("/api/v1/calendar/tools/create-appointment", json=payload)
    assert booked.json()["success"] is True
    assert booked.json()["external_event_id"] == "event-1"
    booking = db.scalar(select(CalendarBooking).where(CalendarBooking.id == UUID(booked.json()["booking_id"])))
    assert booking.service_name_snapshot == appointment.name
    assert booking.duration_minutes_snapshot == 30
    assert booking.sync_status == "synced"
    repeated = client.post("/api/v1/calendar/tools/create-appointment", json=payload)
    assert repeated.json()["booking_id"] == booked.json()["booking_id"]
    assert len(provider.created_events) == 1


def test_booking_datetime_is_resolved_in_tenant_timezone_and_stored_without_raw_text(
    client,
    db,
    calendar_env,
):
    tenant, owner, settings, _provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    resolved_service = client.post(
        "/api/v1/calendar/tools/resolve-service",
        json={
            "session_id": str(call.id),
            "tool_call_id": "resolve-date-service",
            "service_name": appointment.name,
        },
    )
    assert resolved_service.status_code == 200, resolved_service.text

    response = client.post(
        "/api/v1/calendar/tools/resolve-booking-datetime",
        json={
            "session_id": str(call.id),
            "tool_call_id": "resolve-date-1",
            "expression": "morgen um 14 Uhr",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    expected_date = datetime.now(ZoneInfo("Europe/Berlin")).date() + timedelta(days=1)
    assert body["status"] == "concrete"
    assert body["timezone"] == "Europe/Berlin"
    assert datetime.fromisoformat(body["start"]).date() == expected_date
    assert "2026" not in body["speech"]
    conversation = db.scalar(
        select(BookingConversation).where(BookingConversation.call_session_id == call.id)
    )
    assert conversation.datetime_resolution_status == "concrete"
    assert conversation.datetime_resolution_version == 1
    assert conversation.requested_start is not None
    assert not hasattr(conversation, "datetime_expression")

    db.execute(delete(ToolExecution).where(ToolExecution.call_session_id == call.id))
    db.delete(conversation)
    db.delete(call)
    db.commit()


def test_conversation_orchestration_bootstrap_snapshot_and_final_booking(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()

    bootstrap = client.post("/api/v1/calendar/conversation/bootstrap", json={"session_id": str(call.id)})
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["snapshot_status"] == "ready"

    resolved = client.post("/api/v1/calendar/tools/resolve-service", json={
        "session_id": str(call.id), "tool_call_id": "resolve-1", "service_name": appointment.name,
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["service_id"] == str(appointment.service_id)

    resolved_datetime = client.post("/api/v1/calendar/tools/resolve-booking-datetime", json={
        "session_id": str(call.id), "tool_call_id": "datetime-1",
        "expression": "3. August 2026 um 9 Uhr",
    })
    assert resolved_datetime.status_code == 200, resolved_datetime.text
    assert resolved_datetime.json()["status"] == "concrete"

    checked = client.post("/api/v1/calendar/tools/check-appointment-availability/session", json={
        "session_id": str(call.id), "tool_call_id": "availability-1",
        "appointment_type_id": str(appointment.id),
    })
    assert checked.status_code == 200, checked.text
    assert checked.json()["available"] is True
    assert checked.json()["preliminary"] is True

    prepared = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "prepare-1",
        "customer_name": "Max Mustermann", "customer_phone": "+49123456", "customer_email": None,
    })
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["state"] == "awaiting_confirmation"
    payload = {
        "session_id": str(call.id), "tool_call_id": "finalize-1",
        "confirmation_version": prepared.json()["confirmation_version"],
        "confirmation_utterance": "Ja, das passt.",
    }
    booked = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json=payload)
    assert booked.status_code == 200, booked.text
    assert booked.json()["success"] is True
    assert booked.json()["external_event_id"] == "event-1"
    repeated = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json=payload)
    assert repeated.json()["booking_id"] == booked.json()["booking_id"]
    assert len(provider.created_events) == 1

    db.execute(delete(ToolExecution).where(ToolExecution.call_session_id == call.id))
    db.execute(delete(AvailabilitySnapshot).where(AvailabilitySnapshot.call_session_id == call.id))
    db.execute(delete(BookingConversation).where(BookingConversation.call_session_id == call.id))
    db.execute(delete(CalendarBooking).where(CalendarBooking.conversation_session_id == call.id))
    db.delete(call)
    db.commit()


def test_conversation_booking_rejects_declined_confirmation(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    assert client.post("/api/v1/calendar/tools/resolve-service", json={
        "session_id": str(call.id), "tool_call_id": "decline-resolve", "service_name": appointment.name,
    }).json()["success"] is True
    assert client.post("/api/v1/calendar/tools/resolve-booking-datetime", json={
        "session_id": str(call.id), "tool_call_id": "decline-datetime",
        "expression": "3. August 2026 um 10 Uhr",
    }).json()["status"] == "concrete"
    assert client.post("/api/v1/calendar/tools/check-appointment-availability/session", json={
        "session_id": str(call.id), "tool_call_id": "decline-availability",
        "appointment_type_id": str(appointment.id),
    }).json()["available"] is True
    prepared = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "decline-prepare",
        "customer_name": "Max Mustermann", "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert prepared.status_code == 200, prepared.text
    response = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json={
        "session_id": str(call.id), "tool_call_id": "decline-finalize",
        "confirmation_version": prepared.json()["confirmation_version"],
        "confirmation_utterance": "Nein, bitte nicht eintragen.",
    })
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "booking_declined"
    assert provider.created_events == []
    assert db.scalar(select(CalendarBooking).where(CalendarBooking.conversation_session_id == call.id)) is None

    db.execute(delete(ToolExecution).where(ToolExecution.call_session_id == call.id))
    db.execute(delete(AvailabilitySnapshot).where(AvailabilitySnapshot.call_session_id == call.id))
    db.execute(delete(BookingConversation).where(BookingConversation.call_session_id == call.id))
    db.delete(call)
    db.commit()


def test_confirmation_is_versioned_bound_to_digest_and_contains_no_raw_utterance(
    client,
    db,
    calendar_env,
):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call, prepared = prepare_conversation(
        client,
        db,
        tenant,
        appointment,
        prefix="versioned",
    )
    duplicate = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "versioned-prepare-duplicate",
        "customer_name": "Max Mustermann", "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["confirmation_version"] == prepared["confirmation_version"]
    assert duplicate.json()["confirmation_digest"] == prepared["confirmation_digest"]

    changed = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "versioned-prepare-changed",
        "customer_name": "Erika Mustermann", "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["confirmation_version"] == prepared["confirmation_version"] + 1
    assert changed.json()["confirmation_digest"] != prepared["confirmation_digest"]

    stale = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json={
        "session_id": str(call.id), "tool_call_id": "versioned-stale",
        "confirmation_version": prepared["confirmation_version"],
        "confirmation_utterance": "Ja, bitte",
    })
    assert stale.json()["success"] is False
    assert stale.json()["error_code"] == "stale_confirmation"
    assert provider.created_events == []

    booked = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json={
        "session_id": str(call.id), "tool_call_id": "versioned-final",
        "confirmation_version": changed.json()["confirmation_version"],
        "confirmation_utterance": "Ja, bitte",
    })
    assert booked.json()["success"] is True
    conversation = db.scalar(
        select(BookingConversation).where(BookingConversation.call_session_id == call.id)
    )
    assert conversation.confirmation_classification == "confirmed"
    assert conversation.confirmation_digest == changed.json()["confirmation_digest"]
    assert conversation.confirmation_decided_at is not None
    assert conversation.confirmation_transition_reason == "confirmation_confirmed"
    assert not hasattr(conversation, "confirmation_utterance")
    assert len(provider.created_events) == 1

    repeated = client.post("/api/v1/calendar/tools/finalize-appointment-booking", json={
        "session_id": str(call.id), "tool_call_id": "versioned-final-repeat",
        "confirmation_version": changed.json()["confirmation_version"],
        "confirmation_utterance": "Machen Sie das",
    })
    assert repeated.json()["booking_id"] == booked.json()["booking_id"]
    assert len(provider.created_events) == 1
    delete_conversation_records(db, call)


def test_unclear_confirmation_requests_only_one_clarification(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call, prepared = prepare_conversation(
        client,
        db,
        tenant,
        appointment,
        prefix="unclear",
    )
    payload = {
        "session_id": str(call.id),
        "confirmation_version": prepared["confirmation_version"],
        "confirmation_utterance": "Vielleicht",
    }
    first = client.post(
        "/api/v1/calendar/tools/finalize-appointment-booking",
        json={**payload, "tool_call_id": "unclear-first"},
    )
    second = client.post(
        "/api/v1/calendar/tools/finalize-appointment-booking",
        json={**payload, "tool_call_id": "unclear-second"},
    )
    assert first.json()["error_code"] == "confirmation_unclear"
    assert second.json()["error_code"] == "confirmation_still_unclear"
    assert provider.created_events == []
    delete_conversation_records(db, call)


def test_realtime_booking_tools_reject_model_supplied_timezone_and_booking_overrides(
    client,
    db,
    calendar_env,
):
    tenant, owner, settings, _provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call, prepared = prepare_conversation(
        client,
        db,
        tenant,
        appointment,
        prefix="forbidden-overrides",
    )
    availability = client.post(
        "/api/v1/calendar/tools/check-appointment-availability/session",
        json={
            "session_id": str(call.id),
            "tool_call_id": "forbidden-availability",
            "appointment_type_id": str(appointment.id),
            "timezone": "UTC",
            "requested_start": "2030-01-01T00:00:00Z",
        },
    )
    finalize = client.post(
        "/api/v1/calendar/tools/finalize-appointment-booking",
        json={
            "session_id": str(call.id),
            "tool_call_id": "forbidden-finalize",
            "confirmation_version": prepared["confirmation_version"],
            "confirmation_utterance": "Ja",
            "service_id": str(uuid4()),
            "start_at": "2030-01-01T00:00:00Z",
            "timezone": "UTC",
        },
    )
    assert availability.status_code == 422
    assert finalize.status_code == 422
    delete_conversation_records(db, call)


def test_alternative_slot_must_be_signed_offered_and_is_rechecked_before_summary(
    client,
    db,
    calendar_env,
):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    provider.busy = [
        BusyInterval(
            datetime(2026, 8, 3, 7, tzinfo=timezone.utc),
            datetime(2026, 8, 3, 7, 30, tzinfo=timezone.utc),
        )
    ]
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    assert client.post("/api/v1/calendar/tools/resolve-service", json={
        "session_id": str(call.id), "tool_call_id": "alternative-service",
        "service_name": appointment.name,
    }).json()["success"] is True
    assert client.post("/api/v1/calendar/tools/resolve-booking-datetime", json={
        "session_id": str(call.id), "tool_call_id": "alternative-datetime",
        "expression": "3. August 2026 um 9 Uhr",
    }).json()["status"] == "concrete"
    checked = client.post("/api/v1/calendar/tools/check-appointment-availability/session", json={
        "session_id": str(call.id), "tool_call_id": "alternative-check",
        "appointment_type_id": str(appointment.id),
    })
    assert checked.status_code == 200, checked.text
    assert checked.json()["available"] is False
    offered = checked.json()["alternatives"][0]["slot_id"]

    tampered = client.post("/api/v1/calendar/tools/select-booking-slot", json={
        "session_id": str(call.id), "tool_call_id": "alternative-tampered",
        "slot_id": f"{offered}x",
    })
    assert tampered.status_code == 400
    selected = client.post("/api/v1/calendar/tools/select-booking-slot", json={
        "session_id": str(call.id), "tool_call_id": "alternative-select",
        "slot_id": offered,
    })
    assert selected.status_code == 200, selected.text
    prepared = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "alternative-prepare",
        "customer_name": "Max Mustermann", "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["state"] == "awaiting_confirmation"
    delete_conversation_records(db, call)


def test_selected_slot_is_refreshed_before_confirmation_summary(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    call = CallSession(tenant_id=tenant.id, channel=CallChannel.browser, status="active")
    db.add(call)
    db.commit()
    assert client.post("/api/v1/calendar/tools/resolve-service", json={
        "session_id": str(call.id), "tool_call_id": "recheck-service",
        "service_name": appointment.name,
    }).json()["success"] is True
    assert client.post("/api/v1/calendar/tools/resolve-booking-datetime", json={
        "session_id": str(call.id), "tool_call_id": "recheck-datetime",
        "expression": "3. August 2026 um 9 Uhr",
    }).json()["status"] == "concrete"
    checked = client.post("/api/v1/calendar/tools/check-appointment-availability/session", json={
        "session_id": str(call.id), "tool_call_id": "recheck-availability",
        "appointment_type_id": str(appointment.id),
    })
    assert checked.json()["available"] is True
    provider.busy = [
        BusyInterval(
            datetime(2026, 8, 3, 7, tzinfo=timezone.utc),
            datetime(2026, 8, 3, 7, 30, tzinfo=timezone.utc),
        )
    ]
    prepared = client.post("/api/v1/calendar/tools/prepare-appointment-confirmation", json={
        "session_id": str(call.id), "tool_call_id": "recheck-prepare",
        "customer_name": "Max Mustermann", "customer_phone": "+49123456",
        "customer_email": None,
    })
    assert prepared.status_code == 409
    assert prepared.json()["error"]["code"] == "slot_no_longer_available"
    conversation = db.scalar(
        select(BookingConversation).where(BookingConversation.call_session_id == call.id)
    )
    assert conversation.state.value == "alternatives_available"
    assert conversation.confirmation_digest is None
    assert provider.created_events == []
    delete_conversation_records(db, call)


def test_appointments_agenda_deduplicates_platform_and_provider_event(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    slot = client.post(
        "/api/v1/calendar/tools/find-available-appointments",
        json={"appointment_type_id": str(appointment.id), "preferred_date": "2026-08-03", "preferred_time_of_day": "morning", "search_days": 1},
    ).json()["slots"][0]
    booked = client.post("/api/v1/calendar/tools/create-calendar-booking", json={
        "slot_id": slot["slot_id"], "appointment_type_id": str(appointment.id), "customer_name": "Kunde",
        "customer_phone": "12345", "customer_email": "", "customer_notes": "", "idempotency_key": "agenda-12345678",
    }).json()
    start = datetime.fromisoformat(slot["start"])
    end = datetime.fromisoformat(slot["end"])
    provider.events = [
        ProviderEvent(booked["external_event_id"], "Doppelt", start, end, ""),
        ProviderEvent("external-only", "Teammeeting", start + timedelta(hours=2), end + timedelta(hours=2), "Büro"),
    ]
    response = client.get(
        "/api/v1/calendar/appointments",
        params={"start": "2026-08-03T00:00:00+02:00", "end": "2026-08-04T00:00:00+02:00"},
    )
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert len(entries) == 2
    assert {item["kind"] for item in entries} == {"platform", "external"}


def test_slot_is_rechecked_and_conflict_returns_new_alternatives(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    slot = client.post(
        "/api/v1/calendar/tools/find-available-appointments",
        json={"appointment_type_id": str(appointment.id), "preferred_date": "2026-08-03", "preferred_time_of_day": "morning", "search_days": 1},
    ).json()["slots"][0]
    provider.busy = [BusyInterval(datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"]))]
    response = client.post(
        "/api/v1/calendar/tools/create-calendar-booking",
        json={"slot_id": slot["slot_id"], "appointment_type_id": str(appointment.id), "customer_name": "Kunde", "customer_phone": "12345", "customer_email": "", "customer_notes": "", "idempotency_key": "conflict-12345678"},
    )
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "slot_no_longer_available"
    assert provider.created_events == []


def test_provider_failure_never_confirms_booking(client, db, calendar_env):
    tenant, owner, settings, provider = calendar_env
    _, _, appointment = create_connected_calendar(db, tenant, owner, settings)
    slot = client.post(
        "/api/v1/calendar/tools/find-available-appointments",
        json={"appointment_type_id": str(appointment.id), "preferred_date": "2026-08-03", "preferred_time_of_day": "morning", "search_days": 1},
    ).json()["slots"][0]
    provider.fail_create = True
    response = client.post(
        "/api/v1/calendar/tools/create-calendar-booking",
        json={"slot_id": slot["slot_id"], "appointment_type_id": str(appointment.id), "customer_name": "Kunde", "customer_phone": "12345", "customer_email": "", "customer_notes": "", "idempotency_key": "failure-12345678"},
    )
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "calendar_event_creation_failed"
    assert db.scalar(select(CalendarBooking)).status.value == "failed"


def test_tenant_ids_from_client_cannot_cross_account_boundaries(client, db, calendar_env):
    tenant, owner, settings, _ = calendar_env
    _, calendar, _ = create_connected_calendar(db, tenant, owner, settings)
    second = Tenant(slug=f"second-{uuid4().hex[:6]}", name="Second", industry="test", timezone="Europe/Berlin", status="active")
    db.add(second)
    db.commit()
    calendar.tenant_id = second.id
    db.commit()
    response = client.put(
        "/api/v1/calendar/configuration/calendars",
        json={"calendars": [{"calendar_id": str(calendar.id), "is_selected_for_availability": True, "is_selected_for_booking": True}]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "calendar_not_found"
    db.delete(calendar)
    db.delete(second)
    db.commit()
