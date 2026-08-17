from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from app.api.dependencies import TenantContext
from app.core.config import Settings, get_settings
from app.core.security import hash_password, normalize_username
from app.main import app
from app.models import (
    AppUser,
    CallChannel,
    CallSession,
    Tenant,
    TenantInboundRoute,
    TenantMembership,
    TenantRole,
    TenantStatus,
)
from app.services.agent_runtime import build_runtime_config
from app.services.conversation_tools import ConversationToolDispatcher
from app.services.tool_projections import outbound_wire_tools
from app.services.twilio import (
    TwilioNumber,
    TwilioServiceError,
    TwilioTelephonyService,
    issue_call_token,
    media_url,
    verify_call_token,
    voice_url,
)
from app.services.twilio_media import (
    PlaybackState,
    TwilioMediaBridge,
    TwilioMediaError,
    session_update,
)

ACCOUNT_SID = "AC" + "a" * 32
AUTH_TOKEN = "test-twilio-auth-token"
PHONE_SID = "PN" + "b" * 32
CALL_SID = "CA" + "c" * 32
STREAM_SID = "MZ" + "d" * 32
PHONE_NUMBER = "+493012345678"


class FakeTwilioProvider:
    def __init__(
        self,
        *,
        conflict: bool = False,
        voice_capable: bool = True,
        available: bool = True,
        sync_error: TwilioServiceError | None = None,
    ) -> None:
        self.number = TwilioNumber(
            sid=PHONE_SID,
            phone_number=PHONE_NUMBER,
            friendly_name="Berlin Test",
            voice_capable=voice_capable,
            voice_application_sid="AP" + "d" * 32 if conflict else None,
            trunk_sid=None,
            voice_url=None,
            voice_method=None,
        )
        self.available = available
        self.sync_error = sync_error
        self.updates: list[tuple[str, str, str]] = []

    def list_numbers(self) -> list[TwilioNumber]:
        return [self.number] if self.available else []

    def find_number(self, phone_number: str) -> TwilioNumber | None:
        if self.available and phone_number == self.number.phone_number:
            return self.number
        return None

    def fetch_number(self, sid: str) -> TwilioNumber:
        assert sid == PHONE_SID
        return self.number

    def configure_voice(self, sid: str, *, voice_url: str, status_callback: str) -> None:
        if self.sync_error:
            raise self.sync_error
        self.updates.append((sid, voice_url, status_callback))

    def validate_request(self, url: str, params: dict[str, str], signature: str) -> bool:
        return RequestValidator(AUTH_TOKEN).validate(url, params, signature)


def twilio_settings(**overrides: object) -> Settings:
    values = {
        "app_env": "test",
        "database_url": "sqlite:///./test.db",
        "app_base_url": "https://telefonagent-tunnel.example",
        "openai_api_key": "server-only-openai-key",
        "twilio_account_sid": ACCOUNT_SID,
        "twilio_auth_token": AUTH_TOKEN,
        "twilio_stream_token_secret": "stream-token-secret-with-more-than-32-bytes",
    }
    values.update(overrides)
    return Settings(**values)


def _tenant(db) -> Tenant:
    return db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))


def _cleanup(db, tenant_id) -> None:
    db.execute(delete(CallSession).where(
        CallSession.tenant_id == tenant_id,
        CallSession.channel == CallChannel.telephone,
    ))
    db.execute(delete(TenantInboundRoute).where(
        TenantInboundRoute.tenant_id == tenant_id,
        TenantInboundRoute.route_type == "phone_number",
    ))
    db.commit()


def _second_tenant(db) -> Tenant:
    tenant = Tenant(
        slug=f"twilio-target-{uuid4().hex[:8]}",
        name="Twilio Zielunternehmen",
        industry="test",
        timezone="Europe/Berlin",
        status=TenantStatus.active,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_twilio_assignment_persists_and_syncs_central_webhook(db):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    provider = FakeTwilioProvider()
    service = TwilioTelephonyService(db, twilio_settings(), provider)

    result = service.assign(tenant, "+49 30 12345678")

    assert result.sync_status == "synced"
    assert result.phone_number == PHONE_NUMBER
    assert result.provider_synced_url == voice_url(twilio_settings())
    assert provider.updates == [(
        PHONE_SID,
        "https://telefonagent-tunnel.example/api/v1/twilio/voice",
        "https://telefonagent-tunnel.example/api/v1/twilio/stream-status",
    )]
    listed = service.list_numbers()[0]
    assert listed.assigned_company_id == tenant.id
    assert listed.routing_status == "synced"
    _cleanup(db, tenant.id)


def test_twilio_assignment_keeps_blocked_provider_conflict(db):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)

    provider = FakeTwilioProvider(conflict=True)
    result = TwilioTelephonyService(db, twilio_settings(), provider).assign(
        tenant, PHONE_NUMBER
    )

    assert result.sync_status == "blocked"
    assert result.error_code == "provider_routing_conflict"
    assert provider.updates == []
    _cleanup(db, tenant.id)


@pytest.mark.parametrize("value", ["03012345678", "+012345678", "+49abc"])
def test_twilio_assignment_rejects_invalid_e164_without_provider_lookup(db, value):
    tenant = _tenant(db)
    provider = FakeTwilioProvider()

    with pytest.raises(TwilioServiceError) as rejected:
        TwilioTelephonyService(db, twilio_settings(), provider).assign(tenant, value)

    assert rejected.value.code == "invalid_phone_number"


def test_twilio_assignment_rejects_missing_or_non_voice_number(db):
    tenant = _tenant(db)

    with pytest.raises(TwilioServiceError) as missing:
        TwilioTelephonyService(
            db, twilio_settings(), FakeTwilioProvider(available=False)
        ).assign(tenant, PHONE_NUMBER)
    with pytest.raises(TwilioServiceError) as non_voice:
        TwilioTelephonyService(
            db, twilio_settings(), FakeTwilioProvider(voice_capable=False)
        ).assign(tenant, PHONE_NUMBER)

    assert missing.value.code == "phone_number_not_found"
    assert non_voice.value.code == "number_not_voice_capable"


def test_twilio_assignment_can_be_removed_and_reassigned(db):
    source = _tenant(db)
    target = _second_tenant(db)
    _cleanup(db, source.id)
    provider = FakeTwilioProvider()
    source_service = TwilioTelephonyService(db, twilio_settings(), provider)
    target_service = TwilioTelephonyService(db, twilio_settings(), provider)

    source_service.assign(source, PHONE_NUMBER)
    removed = source_service.remove(source)
    assigned = target_service.assign(target, PHONE_NUMBER)

    assert removed.phone_number is None
    assert assigned.phone_number == PHONE_NUMBER
    assert db.scalar(select(func.count(TenantInboundRoute.id)).where(
        TenantInboundRoute.normalized_identifier == PHONE_NUMBER
    )) == 1
    assert len(provider.updates) == 2
    _cleanup(db, target.id)
    db.delete(target)
    db.commit()


def test_twilio_assignment_conflict_names_company_and_explicit_transfer_is_atomic(db):
    source = _tenant(db)
    target = _second_tenant(db)
    _cleanup(db, source.id)
    provider = FakeTwilioProvider()
    service = TwilioTelephonyService(db, twilio_settings(), provider)
    service.assign(source, PHONE_NUMBER)

    with pytest.raises(TwilioServiceError) as conflict:
        service.assign(target, PHONE_NUMBER)

    assert conflict.value.code == "number_already_assigned"
    assert conflict.value.details == {
        "assigned_company_id": str(source.id),
        "assigned_company_name": source.name,
    }

    transferred = service.assign(target, PHONE_NUMBER, transfer=True)
    route = db.scalar(select(TenantInboundRoute).where(
        TenantInboundRoute.normalized_identifier == PHONE_NUMBER
    ))
    assert transferred.phone_number == PHONE_NUMBER
    assert route.tenant_id == target.id
    assert db.scalar(select(func.count(TenantInboundRoute.id)).where(
        TenantInboundRoute.normalized_identifier == PHONE_NUMBER
    )) == 1
    _cleanup(db, target.id)
    db.delete(target)
    db.commit()


def test_twilio_transfer_rolls_back_as_one_database_change(db, monkeypatch):
    source = _tenant(db)
    target = _second_tenant(db)
    _cleanup(db, source.id)
    provider = FakeTwilioProvider()
    service = TwilioTelephonyService(db, twilio_settings(), provider)
    service.assign(source, PHONE_NUMBER)
    original_commit = db.commit

    def fail_commit():
        raise IntegrityError("forced transfer failure", {}, Exception("forced"))

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(TwilioServiceError) as rejected:
        service.assign(target, PHONE_NUMBER, transfer=True)
    monkeypatch.setattr(db, "commit", original_commit)

    assert rejected.value.code == "number_already_assigned"
    route = db.scalar(select(TenantInboundRoute).where(
        TenantInboundRoute.normalized_identifier == PHONE_NUMBER
    ))
    assert route.tenant_id == source.id
    _cleanup(db, source.id)
    db.delete(target)
    db.commit()


def test_twilio_assignment_keeps_provider_sync_error_as_route_status(db):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    provider = FakeTwilioProvider(sync_error=TwilioServiceError(
        "provider_sync_failed", "Provider nicht erreichbar."
    ))

    result = TwilioTelephonyService(db, twilio_settings(), provider).assign(
        tenant, PHONE_NUMBER
    )

    assert result.sync_status == "error"
    assert result.error_code == "provider_sync_failed"
    _cleanup(db, tenant.id)


def test_platform_admin_assigns_twilio_number_without_exposing_credentials(
    monkeypatch, client, db
):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    provider = FakeTwilioProvider()
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        "app.api.v1.platform.require_twilio_provider", lambda _settings: provider
    )

    numbers = client.get("/api/v1/platform/telephony/twilio/numbers")
    assigned = client.put(
        f"/api/v1/platform/companies/{tenant.id}/telephony/twilio",
        json={"phone_number": "+49 30 12345678", "transfer": False},
    )

    assert numbers.status_code == 200
    assert numbers.json()[0]["sid"] == PHONE_SID
    assert assigned.status_code == 200
    assert assigned.json()["sync_status"] == "synced"
    assert AUTH_TOKEN not in numbers.text + assigned.text
    removed = client.delete(
        f"/api/v1/platform/companies/{tenant.id}/telephony/twilio"
    )
    assert removed.status_code == 200
    assert removed.json()["phone_number"] is None
    assert len(provider.updates) == 1
    _cleanup(db, tenant.id)


def test_twilio_management_requires_platform_authentication(anonymous_client, db):
    tenant = _tenant(db)
    response = anonymous_client.get(
        f"/api/v1/platform/companies/{tenant.id}/telephony"
    )
    assert response.status_code == 401
    assignment = anonymous_client.put(
        f"/api/v1/platform/companies/{tenant.id}/telephony/twilio",
        json={"phone_number": PHONE_NUMBER},
    )
    removal = anonymous_client.delete(
        f"/api/v1/platform/companies/{tenant.id}/telephony/twilio"
    )
    assert assignment.status_code == 401
    assert removal.status_code == 401


def test_twilio_management_forbids_company_admin(anonymous_client, db):
    tenant = _tenant(db)
    username = f"twilio-company-admin-{uuid4().hex[:8]}"
    password = "company admin secure test password"
    user = AppUser(
        username=username,
        normalized_username=normalize_username(username),
        password_hash=hash_password(password),
        display_name="Twilio Company Admin",
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=TenantRole.company_admin,
        is_active=True,
        is_primary_admin=False,
    )
    db.add(membership)
    db.commit()

    login = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://testserver", "X-Requested-With": "Telefonagent"},
    )
    assert login.status_code == 200
    assert anonymous_client.get(
        f"/api/v1/platform/companies/{tenant.id}/telephony"
    ).status_code == 403
    assert anonymous_client.put(
        f"/api/v1/platform/companies/{tenant.id}/telephony/twilio",
        json={"phone_number": PHONE_NUMBER},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": anonymous_client.cookies.get("telefonagent_csrf"),
        },
    ).status_code == 403

    anonymous_client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": anonymous_client.cookies.get("telefonagent_csrf"),
        },
    )
    db.delete(membership)
    db.delete(user)
    db.commit()


def test_call_token_binds_tenant_session_and_call_sid():
    settings = twilio_settings()
    tenant_id = uuid4()
    call_session_id = uuid4()
    token = issue_call_token(
        settings,
        tenant_id=tenant_id,
        call_session_id=call_session_id,
        call_sid=CALL_SID,
        phone_number=PHONE_NUMBER,
    )

    assert verify_call_token(settings, token, call_sid=CALL_SID) == (
        tenant_id,
        call_session_id,
        PHONE_NUMBER,
    )


def test_signed_voice_webhook_is_idempotent_and_tenant_scoped(anonymous_client, db):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    db.add(TenantInboundRoute(
        tenant_id=tenant.id,
        route_type="phone_number",
        normalized_identifier=PHONE_NUMBER,
        is_active=True,
        provider="twilio",
        provider_resource_id=PHONE_SID,
        provider_sync_status="synced",
    ))
    db.commit()
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID, "To": PHONE_NUMBER}
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        voice_url(settings), params
    )

    first = anonymous_client.post(
        "/api/v1/twilio/voice",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    second = anonymous_client.post(
        "/api/v1/twilio/voice",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert first.status_code == second.status_code == 200
    assert media_url(settings) in first.text
    assert "call_token" in first.text
    assert db.scalar(select(func.count(CallSession.id)).where(
        CallSession.tenant_id == tenant.id,
        CallSession.channel == CallChannel.telephone,
        CallSession.provider_session_id == CALL_SID,
    )) == 1
    call = db.scalar(select(CallSession).where(CallSession.provider_session_id == CALL_SID))
    assert call.status == "provisioned"
    assert call.runtime_manifest_digest
    assert "instructions" not in (call.runtime_manifest_snapshot or {})
    _cleanup(db, tenant.id)


def test_invalid_twilio_signature_never_creates_a_call(anonymous_client, db):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    response = anonymous_client.post(
        "/api/v1/twilio/voice",
        data={"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID, "To": PHONE_NUMBER},
        headers={"X-Twilio-Signature": "invalid"},
    )

    assert response.status_code == 403
    assert db.scalar(select(func.count(CallSession.id)).where(
        CallSession.channel == CallChannel.telephone,
        CallSession.provider_session_id == CALL_SID,
    )) == 0


def test_media_websocket_rejects_invalid_signature(anonymous_client):
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    with pytest.raises(WebSocketDisconnect):
        with anonymous_client.websocket_connect(
            "/api/v1/twilio/media",
            headers={"X-Twilio-Signature": "invalid"},
        ):
            pass


@pytest.mark.parametrize(
    "events",
    [
        [{"event": "start", "sequenceNumber": "1", "start": {}}],
        [
            {"event": "connected", "protocol": "Call", "version": "1.0.0"},
            {"event": "connected", "protocol": "Call", "version": "1.0.0"},
        ],
        [
            {"event": "connected", "protocol": "Call", "version": "1.0.0"},
            {"event": "media", "sequenceNumber": "1", "streamSid": STREAM_SID},
        ],
        [{"event": "connected", "protocol": "invalid", "version": "1.0.0"}],
    ],
)
def test_media_websocket_rejects_invalid_initial_event_order(
    anonymous_client, events
):
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    signature = RequestValidator(AUTH_TOKEN).compute_signature(
        media_url(settings), {}
    )

    with anonymous_client.websocket_connect(
        "/api/v1/twilio/media",
        headers={"X-Twilio-Signature": signature},
    ) as websocket:
        for event in events:
            websocket.send_json(event)
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_text()
        assert disconnected.value.code == 1008


def test_media_websocket_accepts_real_connected_start_media_stop_sequence(
    monkeypatch, anonymous_client, db
):
    tenant = _tenant(db)
    _cleanup(db, tenant.id)
    db.add(TenantInboundRoute(
        tenant_id=tenant.id,
        route_type="phone_number",
        normalized_identifier=PHONE_NUMBER,
        is_active=True,
        provider="twilio",
        provider_resource_id=PHONE_SID,
        provider_sync_status="synced",
    ))
    db.commit()
    settings = twilio_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    voice_params = {
        "AccountSid": ACCOUNT_SID,
        "CallSid": CALL_SID,
        "To": PHONE_NUMBER,
    }
    voice_signature = RequestValidator(AUTH_TOKEN).compute_signature(
        voice_url(settings), voice_params
    )
    voice_response = anonymous_client.post(
        "/api/v1/twilio/voice",
        data=voice_params,
        headers={"X-Twilio-Signature": voice_signature},
    )
    token = ET.fromstring(voice_response.text).find(".//Parameter").attrib["value"]

    class FakeOpenAI:
        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.received_events = 0

        async def send(self, value: str) -> None:
            self.sent.append(json.loads(value))

        async def recv(self) -> str:
            self.received_events += 1
            if self.received_events == 1:
                return json.dumps({"type": "session.created"})
            if self.received_events == 2:
                return json.dumps({"type": "session.updated"})
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    fake_openai = FakeOpenAI()

    @asynccontextmanager
    async def fake_connector(_settings, _runtime):
        yield fake_openai

    class TestBridge(TwilioMediaBridge):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs, connector=fake_connector)

    monkeypatch.setattr("app.api.v1.twilio.TwilioMediaBridge", TestBridge)
    media_signature = RequestValidator(AUTH_TOKEN).compute_signature(
        media_url(settings), {}
    )
    with anonymous_client.websocket_connect(
        "/api/v1/twilio/media",
        headers={"X-Twilio-Signature": media_signature},
    ) as websocket:
        websocket.send_json({
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0",
        })
        websocket.send_json({
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": STREAM_SID,
            "start": {
                "accountSid": ACCOUNT_SID,
                "callSid": CALL_SID,
                "streamSid": STREAM_SID,
                "tracks": ["inbound"],
                "customParameters": {"call_token": token},
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
            },
        })
        websocket.send_json({
            "event": "media",
            "sequenceNumber": "2",
            "streamSid": STREAM_SID,
            "media": {"track": "inbound", "payload": "AA=="},
        })
        websocket.send_json({
            "event": "stop",
            "sequenceNumber": "3",
            "streamSid": STREAM_SID,
            "stop": {"accountSid": ACCOUNT_SID, "callSid": CALL_SID},
        })

    assert any(
        event.get("type") == "input_audio_buffer.append"
        and event.get("audio") == "AA=="
        for event in fake_openai.sent
    )
    assert [event["type"] for event in fake_openai.sent[:2]] == [
        "session.update",
        "response.create",
    ]
    assert all(
        "strict" not in tool
        for tool in fake_openai.sent[0]["session"]["tools"]
    )
    call = db.scalar(select(CallSession).where(
        CallSession.provider_session_id == CALL_SID
    ))
    assert call.status == "ended"
    _cleanup(db, tenant.id)


def test_media_bridge_rejects_duplicate_start_after_initialization(db):
    tenant = _tenant(db)
    context = TenantContext(id=tenant.id, tenant=tenant)
    runtime = build_runtime_config(db, context, twilio_settings(), test_mode=False)

    class FakeTwilio:
        async def receive_text(self) -> str:
            return json.dumps({
                "event": "start",
                "sequenceNumber": "2",
                "streamSid": STREAM_SID,
                "start": {},
            })

    bridge = TwilioMediaBridge(
        FakeTwilio(),
        twilio_settings(),
        context,
        runtime,
        STREAM_SID,
        None,
        1,
    )

    with pytest.raises(TwilioMediaError) as rejected:
        asyncio.run(bridge._receive_twilio(None))

    assert rejected.value.code == "unsupported_twilio_event"


def test_media_session_uses_runtime_manifest_and_pcmu(db):
    tenant = _tenant(db)
    runtime = build_runtime_config(
        db, TenantContext(id=tenant.id, tenant=tenant), twilio_settings(), test_mode=False
    )

    event = session_update(runtime)

    assert event["session"]["instructions"] == runtime.manifest.instructions
    assert event["session"]["tools"] == outbound_wire_tools(runtime.manifest.tools)
    assert runtime.manifest.tools
    assert all("strict" in tool for tool in runtime.manifest.tools)
    assert all("strict" not in tool for tool in event["session"]["tools"])
    assert event["session"]["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert event["session"]["audio"]["output"]["format"] == {"type": "audio/pcmu"}


def test_openai_session_error_is_sanitized_and_preserves_safe_diagnostics(db):
    tenant = _tenant(db)
    context = TenantContext(id=tenant.id, tenant=tenant)
    runtime = build_runtime_config(db, context, twilio_settings(), test_mode=False)

    class FakeOpenAI:
        async def recv(self) -> str:
            return json.dumps({
                "type": "error",
                "error": {
                    "code": "unknown_parameter",
                    "param": "session.tools[0].strict",
                    "message": (
                        "Unknown parameter: 'session.tools[0].strict'. "
                        "Bearer sk-do-not-log-this-secret"
                    ),
                },
            })

    bridge = TwilioMediaBridge(
        None,
        twilio_settings(),
        context,
        runtime,
        STREAM_SID,
        None,
        1,
    )

    with pytest.raises(TwilioMediaError) as rejected:
        asyncio.run(bridge._await_session_updated(FakeOpenAI()))

    assert rejected.value.code == "openai_session_rejected"
    assert rejected.value.provider_code == "unknown_parameter"
    assert rejected.value.provider_param == "session.tools[0].strict"
    assert "do-not-log" not in (rejected.value.provider_message or "")
    assert "[redacted]" in (rejected.value.provider_message or "")


def test_playback_marks_track_only_acknowledged_audio_for_barge_in():
    playback = PlaybackState()
    audio = "A" * 216  # 160 decoded PCMU bytes, approximately 20 ms.
    first_mark = playback.add_audio("assistant-item", audio)
    playback.add_audio("assistant-item", audio)
    playback.acknowledge(first_mark)

    item_id, played_ms = playback.clear()

    assert item_id == "assistant-item"
    assert played_ms == 20


def test_dispatcher_refuses_booking_without_confirmed_user_utterance(db):
    tenant = _tenant(db)
    dispatcher = ConversationToolDispatcher(
        db,
        twilio_settings(),
        TenantContext(id=tenant.id, tenant=tenant),
        uuid4(),
    )

    result = asyncio.run(
        dispatcher.execute(
            "finalize_appointment_booking",
            {"confirmation_version": 1},
            call_id="call-confirmation",
            latest_confirmed_user_utterance=None,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "confirmation_utterance_missing"


def test_media_tool_result_keeps_call_id_and_creates_one_response(db):
    tenant = _tenant(db)
    context = TenantContext(id=tenant.id, tenant=tenant)
    runtime = build_runtime_config(db, context, twilio_settings(), test_mode=False)

    class FakeDispatcher:
        marked: list[str] = []

        async def execute(self, name, arguments, **values):
            assert name == "list_bookable_services"
            assert arguments == {}
            assert values["call_id"] == "call-tool-1"
            return {"success": True}

        def mark_result_sent(self, call_id):
            self.marked.append(call_id)

    class FakeOpenAI:
        sent: list[dict] = []

        async def send(self, value):
            self.sent.append(json.loads(value))

    dispatcher = FakeDispatcher()
    openai = FakeOpenAI()
    bridge = TwilioMediaBridge(
        None,
        twilio_settings(),
        context,
        runtime,
        "MZ-stream",
        dispatcher,
        1,
    )

    asyncio.run(
        bridge._execute_tool(openai, {
            "call_id": "call-tool-1",
            "name": "list_bookable_services",
            "arguments": "{}",
        })
    )

    assert openai.sent[0]["item"]["call_id"] == "call-tool-1"
    assert [item["type"] for item in openai.sent].count("response.create") == 1
    assert dispatcher.marked == ["call-tool-1"]
