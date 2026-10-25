import asyncio
import json
import os
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from json import dumps
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db.session import bind_tenant_context, get_db
from app.main import app
from app.models import AgentCapability, AgentConfiguration, CalendarBooking, CallSession, TenantInboundRoute
from app.schemas.calls import CallAnalysisResult
from app.services.authentication import AuthenticationService
from app.services.call_analysis import CallAnalysisService
from app.services.call_lifecycle import CallLifecycleService
from app.services.call_processing import erase_call_content
from app.services.provisioning import ProvisioningService
from app.services.tenant_resolution import InboundRouteTenantResolver
from app.services.twilio import media_url, voice_url
from app.services.twilio_media import TwilioMediaBridge


def test_background_worker_discovers_work_without_user_context(monkeypatch):
    from app.services import call_processing
    engine = create_engine(RUNTIME_URL)
    visited = []
    async def visit(db, settings, tenant_id):
        visited.append(str(tenant_id))
        visible = db.scalars(text("SELECT DISTINCT tenant_id FROM call_sessions")).all()
        assert all(str(value) == str(tenant_id) for value in visible)
    async def stop_after_cycle(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(call_processing, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(call_processing, "process_tenant_calls", visit)
    monkeypatch.setattr(call_processing.asyncio, "sleep", stop_after_cycle)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call_processing.call_processing_loop(Settings()))
        assert {TENANT_A, TENANT_B} <= set(visited)
    finally:
        engine.dispose()

MIGRATOR_URL = os.environ.get("TEST_POSTGRES_MIGRATOR_URL")
RUNTIME_URL = os.environ.get("TEST_POSTGRES_RLS_URL")

pytestmark = pytest.mark.skipif(
    not MIGRATOR_URL or not RUNTIME_URL,
    reason=(
        "TEST_POSTGRES_MIGRATOR_URL und TEST_POSTGRES_RLS_URL sind nicht "
        "für den isolierten PostgreSQL-17-Test gesetzt."
    ),
)

TENANT_A = "10000000-0000-4000-8000-000000000001"
TENANT_B = "10000000-0000-4000-8000-000000000002"
SERVICE_A = "20000000-0000-4000-8000-000000000001"
SERVICE_B = "20000000-0000-4000-8000-000000000002"
USER_A = "30000000-0000-4000-8000-000000000001"
PLATFORM_USER = "30000000-0000-4000-8000-000000000002"
MEMBERSHIP_A = "40000000-0000-4000-8000-000000000001"
INBOUND_ROUTE_A = "50000000-0000-4000-8000-000000000001"
INBOUND_ROUTE_B = "50000000-0000-4000-8000-000000000002"
TELEPHONE_CALL_A = "80000000-0000-4000-8000-000000000001"
TELEPHONE_CALL_B = "80000000-0000-4000-8000-000000000002"
TWILIO_CALL_SID_A = "CA" + "a" * 32
TWILIO_CALL_SID_B = "CA" + "b" * 32
OAUTH_STATE_A = "60000000-0000-4000-8000-000000000001"
INVITATION_A = "70000000-0000-4000-8000-000000000001"
INVITATION_TOKEN_HASH = "a" * 64
PASSWORD = "postgres runtime authentication password"


@pytest.fixture(scope="module", autouse=True)
def postgres_rls_fixture():
    migrator = create_engine(MIGRATOR_URL)
    with migrator.begin() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'telefonagent_rls_runtime'
                    ) THEN
                        CREATE ROLE telefonagent_rls_runtime
                        LOGIN PASSWORD 'runtime-test-password';
                    END IF;
                END
                $$;
                """
            )
        )
        connection.execute(
            text(
                "GRANT CONNECT ON DATABASE telefonagent_auth_test "
                "TO telefonagent_rls_runtime"
            )
        )
        connection.execute(
            text(
                "GRANT USAGE ON SCHEMA public TO telefonagent_rls_runtime"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                "TO telefonagent_rls_runtime"
            )
        )
        for tenant_id, slug, name in (
            (TENANT_A, "rls-alpha", "RLS Alpha"),
            (TENANT_B, "rls-beta", "RLS Beta"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (
                        id, slug, name, industry, timezone, status
                    ) VALUES (
                        CAST(:id AS uuid), :slug, :name, 'services',
                        'Europe/Berlin', 'active'
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": tenant_id, "slug": slug, "name": name},
            )
        for service_id, tenant_id, name in (
            (SERVICE_A, TENANT_A, "Alpha Service"),
            (SERVICE_B, TENANT_B, "Beta Service"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO services (
                        id, tenant_id, name, description,
                        duration_minutes, is_active
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:tenant_id AS uuid), :name,
                        '', 30, true
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": service_id, "tenant_id": tenant_id, "name": name},
            )
        connection.execute(
            text(
                """
                INSERT INTO app_users (
                    id, username, normalized_username, password_hash,
                    email, normalized_email, display_name, is_active,
                    platform_role, must_change_password, is_platform_admin
                ) VALUES (
                    CAST(:id AS uuid), 'rls-owner', 'rls-owner', :password_hash,
                    'rls-owner@example.test', 'rls-owner@example.test',
                    'RLS Owner', true, NULL, false, false
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": USER_A, "password_hash": hash_password(PASSWORD)},
        )
        connection.execute(
            text(
                """
                INSERT INTO app_users (
                    id, username, normalized_username, password_hash,
                    email, normalized_email, display_name, is_active,
                    platform_role, must_change_password, is_platform_admin
                ) VALUES (
                    CAST(:id AS uuid), 'rls-platform', 'rls-platform',
                    :password_hash, 'rls-platform@example.test',
                    'rls-platform@example.test', 'RLS Platform', true,
                    'admin', false, true
                )
                ON CONFLICT (id) DO UPDATE
                SET platform_role = 'admin',
                    is_active = true,
                    password_hash = EXCLUDED.password_hash
                """
            ),
            {"id": PLATFORM_USER, "password_hash": hash_password(PASSWORD)},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_memberships (
                    id, tenant_id, user_id, role, is_active, is_primary_admin
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:user_id AS uuid), 'company_admin', true, true
                )
                ON CONFLICT (id) DO UPDATE
                SET role = 'company_admin', is_active = true, is_primary_admin = true
                """
            ),
            {
                "id": MEMBERSHIP_A,
                "tenant_id": TENANT_A,
                "user_id": USER_A,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO invitations (
                    id, tenant_id, created_by_user_id, email, normalized_email,
                    username, display_name, tenant_role, token_hash, expires_at,
                    delivery_status
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:creator_id AS uuid), 'invite@example.test',
                    'invite@example.test', 'invite', 'Invite', 'company_user',
                    :token_hash, now() + interval '72 hours', 'sent'
                )
                ON CONFLICT (id) DO UPDATE
                SET token_hash = EXCLUDED.token_hash,
                    expires_at = EXCLUDED.expires_at,
                    delivery_status = 'sent', accepted_at = NULL, revoked_at = NULL
                """
            ),
            {
                "id": INVITATION_A,
                "tenant_id": TENANT_A,
                "creator_id": USER_A,
                "token_hash": INVITATION_TOKEN_HASH,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_inbound_routes (
                    id, tenant_id, route_type, normalized_identifier, is_active
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    'phone_number', '+4930123456', true
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": INBOUND_ROUTE_A, "tenant_id": TENANT_A},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_inbound_routes (
                    id, tenant_id, route_type, normalized_identifier, is_active,
                    provider, provider_resource_id, provider_sync_status
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    'phone_number', '+4930654321', true,
                    'twilio', :phone_sid, 'synced'
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": INBOUND_ROUTE_B,
                "tenant_id": TENANT_B,
                "phone_sid": "PN" + "b" * 32,
            },
        )
        for call_id, tenant_id, call_sid in (
            (TELEPHONE_CALL_A, TENANT_A, TWILIO_CALL_SID_A),
            (TELEPHONE_CALL_B, TENANT_B, TWILIO_CALL_SID_B),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO call_sessions (
                        id, tenant_id, channel, status, provider_session_id
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                        'telephone', 'provisioned', :call_sid
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": call_id, "tenant_id": tenant_id, "call_sid": call_sid},
            )
        connection.execute(
            text(
                """
                INSERT INTO calendar_oauth_states (
                    id, tenant_id, user_id, provider, state_hash,
                    encrypted_code_verifier, expires_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:user_id AS uuid), 'google', 'known-state-hash',
                    'encrypted-test-verifier', now() + interval '10 minutes'
                )
                ON CONFLICT (id) DO UPDATE
                SET tenant_id = EXCLUDED.tenant_id,
                    user_id = EXCLUDED.user_id,
                    provider = EXCLUDED.provider,
                    state_hash = EXCLUDED.state_hash,
                    encrypted_code_verifier = EXCLUDED.encrypted_code_verifier,
                    expires_at = EXCLUDED.expires_at,
                    consumed_at = NULL
                """
            ),
            {
                "id": OAUTH_STATE_A,
                "tenant_id": TENANT_A,
                "user_id": USER_A,
            },
        )
    yield
    migrator.dispose()


def test_runtime_role_cannot_read_without_transaction_tenant_context():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM services")) == 0
    engine.dispose()


def test_runtime_role_sees_only_selected_tenant():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": TENANT_A},
        )
        assert connection.scalars(
            text("SELECT name FROM services ORDER BY name")
        ).all() == ["Alpha Service"]
    engine.dispose()


def test_platform_mode_can_read_control_plane_but_not_business_rows():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": PLATFORM_USER},
        )
        assert connection.scalar(text("SELECT count(*) FROM tenants")) >= 2
        assert connection.scalar(text("SELECT count(*) FROM services")) == 0
    engine.dispose()


def test_runtime_role_is_not_owner_and_cannot_bypass_rls():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT role.rolbypassrls,
                       table_owner.rolname = current_user AS owns_services
                FROM pg_roles role
                JOIN pg_class table_info ON table_info.relname = 'services'
                JOIN pg_roles table_owner ON table_owner.oid = table_info.relowner
                WHERE role.rolname = current_user
                """
            )
        ).one()
        assert row.rolbypassrls is False
        assert row.owns_services is False
    engine.dispose()


def test_invitation_policy_discloses_only_the_exact_hashed_public_token():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM invitations")) == 0
        connection.execute(
            text(
                "SELECT set_config('app.invitation_token_hash', :token_hash, true)"
            ),
            {"token_hash": "b" * 64},
        )
        assert connection.scalar(text("SELECT count(*) FROM invitations")) == 0
        connection.execute(
            text(
                "SELECT set_config('app.invitation_token_hash', :token_hash, true)"
            ),
            {"token_hash": INVITATION_TOKEN_HASH},
        )
        assert connection.scalar(text("SELECT email FROM invitations")) == (
            "invite@example.test"
        )
    engine.dispose()


def test_rls_blocks_cross_tenant_write():
    engine = create_engine(RUNTIME_URL)
    with pytest.raises(ProgrammingError):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": TENANT_A},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO services (
                        id, tenant_id, name, description,
                        duration_minutes, is_active
                    ) VALUES (
                        gen_random_uuid(), CAST(:foreign_tenant AS uuid),
                        'Blocked', '', 30, true
                    )
                    """
                ),
                {"foreign_tenant": TENANT_B},
            )
    engine.dispose()


def test_composite_foreign_key_rejects_cross_tenant_relation():
    engine = create_engine(RUNTIME_URL)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": TENANT_A},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO calendar_appointment_types (
                        id, tenant_id, service_id, name, description,
                        duration_minutes, location_type, location_text, is_active
                    ) VALUES (
                        gen_random_uuid(), CAST(:tenant_id AS uuid),
                        CAST(:foreign_service AS uuid), 'Invalid', '',
                        30, 'phone', '', true
                    )
                    """
                ),
                {"tenant_id": TENANT_A, "foreign_service": SERVICE_B},
            )
    engine.dispose()


def test_authentication_establishes_rls_context_for_runtime_role():
    engine = create_engine(RUNTIME_URL)
    settings = Settings(
        app_env="test",
        database_url=RUNTIME_URL,
        cors_origins="http://testserver",
        auth_hmac_secret="postgres-test-auth-secret-with-thirty-two-bytes",
    )
    with Session(engine, expire_on_commit=False) as db:
        authenticated, secrets = AuthenticationService(db, settings).login(
            "rls-owner", PASSWORD, "127.0.0.1"
        )
        assert str(authenticated.tenant.id) == TENANT_A
        restored = AuthenticationService(db, settings).authenticate(secrets.token)
        assert str(restored.tenant.id) == TENANT_A
        assert db.scalars(
            text("SELECT name FROM services ORDER BY name")
        ).all() == ["Alpha Service"]
        assert db.scalar(text("SELECT current_setting('app.platform_role')")) == ""
    engine.dispose()


def test_platform_authentication_sets_access_level_without_implicit_tenant():
    engine = create_engine(RUNTIME_URL)
    settings = Settings(
        app_env="test",
        database_url=RUNTIME_URL,
        cors_origins="http://testserver",
        auth_hmac_secret="postgres-test-auth-secret-with-thirty-two-bytes",
    )
    with Session(engine, expire_on_commit=False) as db:
        authenticated, secrets = AuthenticationService(db, settings).login(
            "rls-platform", PASSWORD, "127.0.0.2"
        )
        assert authenticated.tenant is None
        restored = AuthenticationService(db, settings).authenticate(secrets.token)
        assert restored.tenant is None
        assert db.scalar(text("SELECT current_setting('app.platform_role')")) == "admin"
        assert db.scalar(text("SELECT current_setting('app.tenant_id')")) == ""
        assert db.scalar(text("SELECT count(*) FROM services")) == 0
    engine.dispose()


def test_security_definer_resolvers_establish_tenant_without_bypassing_rls():
    engine = create_engine(RUNTIME_URL)
    with Session(engine, expire_on_commit=False) as db:
        resolved = InboundRouteTenantResolver(db).resolve(
            "phone_number", "+49 30 123456"
        )
        assert str(resolved.id) == TENANT_A
        assert db.scalars(text("SELECT name FROM services")).all() == [
            "Alpha Service"
        ]
    with engine.begin() as connection:
        assert str(
            connection.scalar(
                text(
                    "SELECT resolve_calendar_oauth_tenant("
                    "'known-state-hash', 'google')"
                )
            )
        ) == TENANT_A
    engine.dispose()


def test_twilio_resolvers_keep_two_numbers_and_call_sids_tenant_isolated():
    engine = create_engine(RUNTIME_URL)
    with Session(engine, expire_on_commit=False) as db:
        alpha = InboundRouteTenantResolver(db).resolve(
            "phone_number", "+49 30 123456"
        )
        assert str(alpha.id) == TENANT_A
    with Session(engine, expire_on_commit=False) as db:
        beta = InboundRouteTenantResolver(db).resolve(
            "phone_number", "+49 30 654321"
        )
        assert str(beta.id) == TENANT_B
    with engine.begin() as connection:
        assert str(
            connection.scalar(
                text("SELECT resolve_telephone_call_tenant(:call_sid)"),
                {"call_sid": TWILIO_CALL_SID_A},
            )
        ) == TENANT_A
        assert str(
            connection.scalar(
                text("SELECT resolve_telephone_call_tenant(:call_sid)"),
                {"call_sid": TWILIO_CALL_SID_B},
            )
        ) == TENANT_B
    engine.dispose()


@pytest.mark.parametrize("mode", ["agent", "answering_machine"])
def test_signed_webhook_to_automatic_analysis_and_history_with_real_rls(mode, monkeypatch):
    """Actual HTTP/WS and database layers; only provider I/O is simulated."""
    from app.services import call_analysis, call_processing
    suffix = uuid4().hex[:10]
    migrator = create_engine(MIGRATOR_URL)
    runtime = create_engine(RUNTIME_URL)
    runtime_sessions = sessionmaker(runtime, expire_on_commit=False, autoflush=False)
    username = f"qa-{suffix}"
    password = "synthetic QA process-chain password"
    phone = "+4930" + str(int(suffix, 16))[:9]
    call_sid, stream_sid = "CA" + uuid4().hex, "MZ" + uuid4().hex
    settings = Settings(app_env="test", database_url=RUNTIME_URL, cors_origins="http://testserver",
        app_base_url="https://qa.example.test", auth_hmac_secret="qa-test-hmac-at-least-thirty-two-bytes",
        openai_api_key="synthetic-never-used-key", twilio_account_sid="AC" + "1" * 32,
        twilio_auth_token="synthetic-twilio-test-token", twilio_stream_token_secret="qa-call-token-secret-with-at-least-thirty-two-bytes")
    with Session(migrator, expire_on_commit=False) as db:
        tenant, _ = ProvisioningService(db).provision_tenant(slug=username, name="Synthetic QA " + mode,
            industry="services", timezone_name="Europe/Berlin", username=username, display_name="QA Admin", email=None, password=password)
        config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant.id))
        config.operating_mode = mode
        config.message_greeting = "Guten Tag. Welche Nachricht möchten Sie hinterlassen?"
        db.add(AgentCapability(tenant_id=tenant.id, capability_key="calendar_booking", is_active=True))
        db.add(TenantInboundRoute(tenant_id=tenant.id, route_type="phone_number", normalized_identifier=phone, is_active=True))
        db.commit()
        tenant_id = tenant.id
    def database():
        with runtime_sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    sent = []
    class FakeOpenAI:
        def __init__(self):
            self.queue = asyncio.Queue()
            self.queue.put_nowait({"type": "session.created"})
        async def send(self, raw):
            event = json.loads(raw)
            sent.append(event)
            if event["type"] == "session.update":
                self.queue.put_nowait({"type": "session.updated"})
            if event["type"] == "input_audio_buffer.append":
                for incoming in [
                    {"type": "response.output_audio_transcript.done", "item_id": "greeting", "transcript": "Guten Tag. Wie kann ich helfen?"},
                    {"type": "input_audio_buffer.speech_started", "item_id": "caller", "audio_start_ms": 1200},
                    {"type": "input_audio_buffer.committed", "item_id": "caller", "previous_item_id": "greeting"},
                    {"type": "conversation.item.input_audio_transcription.delta", "item_id": "caller", "delta": "Ich bin Anna"},
                    {"type": "conversation.item.input_audio_transcription.completed", "item_id": "caller", "transcript": "Ich bin Anna Beispiel. Bitte rufen Sie mich morgen zurück. Meine Telefonnummer ist 0170 0000001."},
                    {"type": "response.function_call_arguments.done", "call_id": "qa-tool", "name": "list_bookable_services", "arguments": "{}"},
                    {"type": "response.output_audio.delta", "item_id": "reply", "delta": "AA=="},
                ]:
                    self.queue.put_nowait(incoming)
        async def recv(self):
            return json.dumps(await self.queue.get())
    @asynccontextmanager
    async def connector(*args):
        yield FakeOpenAI()
    class Bridge(TwilioMediaBridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, connector=connector)
    monkeypatch.setattr("app.api.v1.twilio.TwilioMediaBridge", Bridge)
    def no_external_actions(*args, **kwargs):
        raise AssertionError("Calendar provider must not be called in this scenario")
    monkeypatch.setattr("app.services.calendar_booking.create_calendar_provider", no_external_actions)
    requests = []
    class FakeResponses:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, *, json, headers):
            requests.append(json)
            import httpx
            return httpx.Response(200, json={"output_text": dumps({
                "caller_name": "Anna Beispiel", "category": "callback", "summary": "Anna bittet um Rückruf am nächsten Tag.",
                "result": "Nachricht aufgenommen.", "action_required": True, "action_items": ["Zurückrufen"], "priority": "normal",
                "main_concern": "Rückruf", "contact_phone": "0170 0000001", "contact_email": None,
                "callback_required": True, "appointment_request": None})})
    monkeypatch.setattr(call_analysis.httpx, "AsyncClient", FakeResponses)
    monkeypatch.setattr(call_processing, "SessionLocal", runtime_sessions)
    try:
        with TestClient(app) as client:
            params = {"AccountSid": settings.twilio_account_sid, "CallSid": call_sid, "To": phone, "From": "+491700000001"}
            validator = RequestValidator(settings.twilio_auth_token)
            response = client.post("/api/v1/twilio/voice", data=params, headers={"X-Twilio-Signature": validator.compute_signature(voice_url(settings), params)})
            root = ET.fromstring(response.text)
            assert root.find("Say") is not None and "transkribiert" in root.find("Say").text
            token = root.find(".//Parameter").attrib["value"]
            # A setting change between webhook and media handshake must only
            # affect future calls, including when Twilio retries the webhook.
            with Session(migrator) as db:
                config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant_id))
                config.operating_mode = "agent" if mode == "answering_machine" else "answering_machine"
                config.version += 1
                db.commit()
            duplicate = client.post("/api/v1/twilio/voice", data=params, headers={"X-Twilio-Signature": validator.compute_signature(voice_url(settings), params)})
            assert ET.fromstring(duplicate.text).find(".//Stream") is not None
            with client.websocket_connect("/api/v1/twilio/media", headers={"X-Twilio-Signature": validator.compute_signature(media_url(settings), {})}) as ws:
                ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
                ws.send_json({"event": "start", "sequenceNumber": "1", "streamSid": stream_sid, "start": {
                    "accountSid": settings.twilio_account_sid, "callSid": call_sid, "streamSid": stream_sid, "tracks": ["inbound"],
                    "customParameters": {"call_token": token}, "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
                ws.send_json({"event": "media", "sequenceNumber": "2", "streamSid": stream_sid, "media": {"track": "inbound", "payload": "AA=="}})
                while ws.receive_json().get("event") != "media":
                    pass
                # Check incremental persistence before the transport is ended.
                with runtime_sessions() as db:
                    bind_tenant_context(db, tenant_id)
                    saved = db.scalar(select(CallSession).where(CallSession.provider_session_id == call_sid))
                    assert saved.transcript[-1]["text"].startswith("Ich bin Anna Beispiel")
                    call_id = saved.id
                    assert saved.status == "connected" and saved.analysis_status == "pending"
                # A competing media connection must not finish the owned stream.
                with client.websocket_connect("/api/v1/twilio/media", headers={"X-Twilio-Signature": validator.compute_signature(media_url(settings), {})}) as duplicate_ws:
                    duplicate_ws.send_json({"event": "start", "streamSid": stream_sid, "start": {
                        "accountSid": settings.twilio_account_sid, "callSid": call_sid, "streamSid": stream_sid,
                        "customParameters": {"call_token": token}, "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
                    with pytest.raises(WebSocketDisconnect) as rejected:
                        duplicate_ws.receive_json()
                    assert rejected.value.code == 1008
                with runtime_sessions() as db:
                    bind_tenant_context(db, tenant_id)
                    saved = db.get(CallSession, call_id)
                    assert saved.status == "connected" and saved.ended_at is None
                ws.send_json({"event": "stop", "sequenceNumber": "3", "streamSid": stream_sid})
            with runtime_sessions() as db:
                bind_tenant_context(db, tenant_id)
                saved = db.get(CallSession, call_id)
                assert saved.ended_at and saved.status == "ended"
                saved.analysis_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
            async def stop_cycle(*args):
                raise asyncio.CancelledError
            with monkeypatch.context() as scoped:
                scoped.setattr(call_processing.asyncio, "sleep", stop_cycle)
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(call_processing.call_processing_loop(settings))
            assert len(requests) == 1
            assert requests[0]["text"]["format"]["strict"] is True and requests[0]["store"] is False
            assert "Anna Beispiel" in requests[0]["input"]
            login = client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers={"Origin": "http://testserver", "X-Requested-With": "Telefonagent"})
            assert login.status_code == 200
            history = client.get("/api/v1/calls").json()
            assert [item["id"] for item in history] == [str(call_id)]
            detail = client.get(f"/api/v1/calls/{call_id}").json()
            assert detail["analysis_status"] == "completed" and detail["caller_name"] == "Anna Beispiel"
            assert detail["callback_required"] is True and detail["operating_mode"] == mode
            assert detail["voice_model"] == settings.openai_realtime_model
            assert detail["appointments"] == []
            assert client.get(f"/api/v1/calls/{TELEPHONE_CALL_A}").status_code == 404
            tool_output = next(event["item"]["output"] for event in sent if event["type"] == "conversation.item.create")
            if mode == "answering_machine":
                assert sent[0]["session"]["tools"] == []
                assert json.loads(tool_output)["error_code"] == "actions_disabled"
                assert detail["actions"] == []
            else:
                assert sent[0]["session"]["tools"]
                assert json.loads(tool_output)["success"] is True
                assert detail["actions"][0]["tool_name"] == "list_bookable_services"
            with runtime_sessions() as db:
                bind_tenant_context(db, tenant_id)
                assert db.scalar(select(CalendarBooking.id)) is None
    finally:
        app.dependency_overrides.clear()
        runtime.dispose()
        migrator.dispose()


@pytest.fixture
def pg_call():
    engine = create_engine(RUNTIME_URL)
    sessions = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with sessions() as db:
        bind_tenant_context(db, TENANT_A)
        call = CallSession(tenant_id=UUID(TENANT_A), channel="telephone", status="ended",
            operating_mode="answering_machine", started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc),
            analysis_status="pending", documentation_status="complete", inbox_status="open",
            transcript=[{"speaker": "customer", "text": "Synthetischer Rückrufwunsch"}])
        db.add(call)
        db.commit()
        call_id = call.id
    yield sessions, call_id
    with sessions() as db:
        bind_tenant_context(db, TENANT_A)
        db.delete(db.get(CallSession, call_id))
        db.commit()
    engine.dispose()


def test_postgres_call_content_and_writes_are_tenant_isolated(pg_call):
    sessions, call_id = pg_call
    with sessions() as db:
        bind_tenant_context(db, TENANT_B)
        assert db.get(CallSession, call_id) is None
        assert db.execute(text("UPDATE call_sessions SET summary = 'forbidden' WHERE id = :id"), {"id": call_id}).rowcount == 0
        db.rollback()
        with pytest.raises(ProgrammingError):
            db.add(CallSession(tenant_id=UUID(TENANT_A), channel="telephone", status="ended"))
            db.commit()
        db.rollback()
    with sessions() as db:
        bind_tenant_context(db, TENANT_A)
        assert db.get(CallSession, call_id).summary is None


def test_postgres_concurrent_analysis_claim_is_exclusive(pg_call, monkeypatch):
    sessions, call_id = pg_call
    invocations = []
    async def fake(*args):
        invocations.append(True)
        await asyncio.sleep(0.02)
        return CallAnalysisResult(caller_name=None, category="callback", summary="Rückruf", result="Nachricht", action_required=True, action_items=[], priority="normal")
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", fake)
    async def analyze():
        with sessions() as db:
            bind_tenant_context(db, TENANT_A)
            return await CallAnalysisService(db, Settings(), UUID(TENANT_A)).analyze(call_id)
    async def both():
        await asyncio.gather(analyze(), analyze())
    asyncio.run(both())
    assert len(invocations) == 1
    with sessions() as db:
        bind_tenant_context(db, TENANT_A)
        saved = db.get(CallSession, call_id)
        assert saved.analysis_status == "completed" and saved.analysis_attempts == 1


def test_postgres_deletion_cannot_be_undone_by_late_analysis_or_finish(pg_call, monkeypatch):
    sessions, call_id = pg_call
    async def fake(*args):
        with sessions() as other:
            bind_tenant_context(other, TENANT_A)
            erase_call_content(other.get(CallSession, call_id))
            other.commit()
        return CallAnalysisResult(caller_name="Must not return", category="callback", summary="Must not return", result="", action_required=True, action_items=[], priority="normal")
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", fake)
    with sessions() as db:
        bind_tenant_context(db, TENANT_A)
        asyncio.run(CallAnalysisService(db, Settings(), UUID(TENANT_A)).analyze(call_id))
        CallLifecycleService(db, UUID(TENANT_A)).finish(call_id, transcript=[{"speaker": "customer", "text": "Must not return"}])
        db.refresh(db.get(CallSession, call_id))
        saved = db.get(CallSession, call_id)
        assert saved.content_deleted_at and saved.transcript == [] and saved.summary is None
        assert saved.caller_name is None and saved.documentation_status == "deleted"


def test_postgres_migration_0017_roundtrip_preserves_0016_data_and_rls(monkeypatch):
    from alembic import command
    from alembic.config import Config
    monkeypatch.setenv("DATABASE_URL", MIGRATOR_URL)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", MIGRATOR_URL)
    get_settings.cache_clear()
    engine = create_engine(MIGRATOR_URL)
    query = text("SELECT id, tenant_id, channel, status, provider_session_id, transcript::text, summary, analysis_status FROM call_sessions ORDER BY id")
    policies = text("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname IN ('call_sessions', 'agent_configurations') ORDER BY relname")
    with engine.connect() as connection:
        before = connection.execute(query).all()
        security = connection.execute(policies).all()
    try:
        command.downgrade(Config("alembic.ini"), "0016")
        command.upgrade(Config("alembic.ini"), "head")
        with engine.connect() as connection:
            assert connection.execute(query).all() == before
            assert connection.execute(policies).all() == security
            assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
            assert connection.scalar(text("SELECT count(*) FROM pg_proc WHERE proname = 'call_processing_tenant_ids' AND prosecdef AND proretset")) == 1
        runtime = create_engine(RUNTIME_URL)
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM call_sessions")) == 0
            assert connection.scalar(text("SELECT count(*) FROM call_processing_tenant_ids()")) >= 2
        runtime.dispose()
    finally:
        engine.dispose()
        get_settings.cache_clear()
