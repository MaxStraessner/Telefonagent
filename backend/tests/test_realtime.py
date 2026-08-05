from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.api.dependencies import TenantContext
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import CallChannel, CallSession, Tenant, TenantStatus
from app.schemas.api import RealtimeAttemptFinishRequest
from app.services.realtime import (
    ClientSecretGrant,
    build_safety_identifier,
    finish_attempt,
)


class FakeAsyncClient:
    response = httpx.Response(
        200,
        json={"value": "ek_test_ephemeral", "expires_at": 1_900_000_000, "session": {"id": "sess_test"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    error: Exception | None = None
    last_headers: dict[str, str] = {}
    last_payload: dict[str, object] = {}

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _url, *, headers, json):
        type(self).last_headers = headers
        type(self).last_payload = json
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def reset_fake_async_client():
    FakeAsyncClient.response = httpx.Response(
        200,
        json={"value": "ek_test_ephemeral", "expires_at": 1_900_000_000, "session": {"id": "sess_test"}},
        headers={"x-request-id": "req_test_provider"},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    FakeAsyncClient.error = None
    FakeAsyncClient.last_headers = {}
    FakeAsyncClient.last_payload = {}
    yield


def configured_settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite:///./test.db",
        "openai_api_key": "server-only-test-key",
        "openai_safety_identifier_salt": "unit-test-installation",
    }
    values.update(overrides)
    return Settings(**values)


def test_agent_config_is_tenant_scoped_and_exposes_no_key(client):
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.get("/api/v1/realtime/agent-config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_name"] == "Salon Haarkunst Test"
    assert payload["assistant_name"] == "Lina"
    assert payload["model"] == "gpt-realtime-2.1"
    assert payload["voice"] == "marin"
    assert payload["vad"]["interrupt_response"] is True
    assert "server-only-test-key" not in response.text
    assert "finalize_appointment_booking erst nach dieser ausdrücklichen Bestätigung" in payload["instructions"]
    assert "keine politische, medizinische, juristische, finanzielle oder private Beratung" in payload["instructions"]
    assert payload["configuration_version"] >= 1
    assert payload["speed"] == 1.0


def test_client_secret_is_short_lived_and_session_config_stays_with_sdk(monkeypatch, client, db):
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    before = db.scalar(select(func.count(CallSession.id)))

    response = client.post("/api/v1/realtime/client-secret")

    assert response.status_code == 200
    assert response.json()["client_secret"] == "ek_test_ephemeral"
    assert response.json()["session_id"] == "sess_test"
    assert set(response.json()) == {"client_secret", "expires_at", "session_id", "model", "voice", "speed", "configuration_version", "call_session_id", "call_attempt_id", "tenant_id"}
    assert "server-only-test-key" not in response.text
    assert FakeAsyncClient.last_payload["expires_after"] == {"anchor": "created_at", "seconds": 60}
    assert "session" not in FakeAsyncClient.last_payload
    assert FakeAsyncClient.last_headers["OpenAI-Safety-Identifier"].startswith("tenant_")
    assert UUID(FakeAsyncClient.last_headers["X-Client-Request-Id"])
    assert "Salon Haarkunst" not in FakeAsyncClient.last_headers["OpenAI-Safety-Identifier"]
    db.expire_all()
    assert db.scalar(select(func.count(CallSession.id))) == before + 1


def test_session_bootstrap_returns_one_digest_bound_runtime_manifest(monkeypatch, client, db):
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()

    call_attempt_id = uuid4()
    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    manifest = payload["manifest"]
    secret = payload["secret"]
    assert UUID(secret["call_attempt_id"]) == call_attempt_id
    assert len(manifest["digest"]) == 64
    assert len(manifest["prompt_digest"]) == 64
    assert len(manifest["tools_digest"]) == 64
    assert manifest["timezone"] == "Europe/Berlin"
    assert manifest["vad"]["silence_duration_ms"] == 600
    assert manifest["vad"]["interrupt_response"] is True
    assert "recovery" not in manifest
    assert manifest["setting_targets"]["simple_mode"] == "ui_only"
    assert manifest["setting_targets"]["silence_duration_ms"] == "session"
    assert manifest["tool_names"] == [item["name"] for item in manifest["tools"]]
    call_session = db.get(CallSession, UUID(secret["call_session_id"]))
    db.refresh(call_session)
    assert call_session.status == "provisioned"
    assert call_session.call_attempt_id == call_attempt_id
    assert call_session.provider_session_id == "sess_test"
    assert call_session.provider_request_id == "req_test_provider"
    assert call_session.runtime_manifest_digest == manifest["digest"]
    assert call_session.runtime_manifest_snapshot["prompt_digest"] == manifest["prompt_digest"]
    assert "instructions" not in call_session.runtime_manifest_snapshot


def test_missing_api_key_returns_controlled_error(client):
    app.dependency_overrides[get_settings] = lambda: configured_settings(openai_api_key=None)
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "realtime_not_configured"


def test_blank_api_key_is_treated_as_missing(client):
    app.dependency_overrides[get_settings] = lambda: configured_settings(openai_api_key="   ")
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "realtime_not_configured"


def test_model_and_voice_are_returned_to_the_sdk_without_duplication_in_secret(monkeypatch, client):
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings(
        openai_realtime_model="gpt-realtime-custom",
        openai_realtime_voice="cedar",
    )
    config = client.get("/api/v1/realtime/agent-config").json()
    secret = client.post("/api/v1/realtime/client-secret").json()
    assert config["model"] == secret["model"] == "gpt-realtime-custom"
    assert config["voice"] == secret["voice"] == "marin"
    assert "session" not in FakeAsyncClient.last_payload


def test_provider_authentication_error_is_sanitized(monkeypatch, client):
    FakeAsyncClient.response = httpx.Response(
        401,
        json={"error": {"message": "secret provider detail"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "realtime_provider_authentication_failed"
    assert "secret provider detail" not in response.text


@pytest.mark.parametrize(
    ("param", "expected_code"),
    [
        ("session.model", "realtime_model_unavailable"),
        ("session.audio.output.voice", "realtime_voice_unavailable"),
    ],
)
def test_model_and_voice_provider_errors_are_structured(monkeypatch, client, param, expected_code):
    FakeAsyncClient.response = httpx.Response(
        400,
        json={"error": {"param": param, "message": "sensitive provider detail"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == expected_code
    assert "sensitive provider detail" not in response.text


def test_provider_timeout_is_controlled(monkeypatch, client, db):
    call_attempt_id = uuid4()
    FakeAsyncClient.response = httpx.Response(
        200,
        json={"value": "ek_test", "expires_at": 1},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    FakeAsyncClient.error = httpx.ReadTimeout("late")
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "realtime_provider_timeout"
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "failed"
    assert attempt.error_code == "realtime_provider_timeout"
    assert attempt.failure_retryable is True
    FakeAsyncClient.error = None


def test_invalid_provider_response_is_controlled(monkeypatch, client):
    FakeAsyncClient.response = httpx.Response(
        200,
        json={"value": "not-ephemeral", "expires_at": "soon"},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "realtime_provider_invalid_response"


@pytest.mark.parametrize(
    ("provider_status", "expected_code", "retryable"),
    [
        (400, "realtime_provider_rejected", False),
        (401, "realtime_provider_authentication_failed", False),
        (403, "realtime_provider_authentication_failed", False),
        (429, "realtime_provider_rate_limited", True),
        (500, "realtime_provider_rejected", True),
    ],
)
def test_provider_failures_terminalize_the_known_attempt(
    monkeypatch, client, db, provider_status, expected_code, retryable
):
    call_attempt_id = uuid4()
    FakeAsyncClient.response = httpx.Response(
        provider_status,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": f"provider_{provider_status}",
                "message": "redacted provider diagnosis",
            }
        },
        headers={"x-request-id": f"req_{provider_status}"},
        request=httpx.Request(
            "POST", "https://api.openai.com/v1/realtime/client_secrets"
        ),
    )
    monkeypatch.setattr(
        "app.services.realtime.httpx.AsyncClient", FakeAsyncClient
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()

    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == expected_code
    assert "redacted provider diagnosis" not in response.text
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "failed"
    assert attempt.ended_at is not None
    assert attempt.failure_phase == "provider_token"
    assert attempt.error_code == expected_code
    assert attempt.http_status == provider_status
    assert attempt.provider_request_id == f"req_{provider_status}"
    assert attempt.failure_retryable is retryable


def test_provider_network_error_terminalizes_the_attempt(
    monkeypatch, client, db
):
    call_attempt_id = uuid4()
    FakeAsyncClient.error = httpx.ConnectError("dns lookup failed")
    monkeypatch.setattr(
        "app.services.realtime.httpx.AsyncClient", FakeAsyncClient
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()

    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "realtime_provider_unavailable"
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "failed"
    assert attempt.error_code == "realtime_provider_unavailable"
    assert attempt.failure_retryable is True


def test_connected_and_finish_transitions_are_idempotent(
    monkeypatch, client, db
):
    call_attempt_id = uuid4()
    monkeypatch.setattr(
        "app.services.realtime.httpx.AsyncClient", FakeAsyncClient
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    bootstrap = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )
    assert bootstrap.status_code == 200

    assert client.post(
        f"/api/v1/realtime/call-attempts/{call_attempt_id}/connected"
    ).status_code == 204
    assert client.post(
        f"/api/v1/realtime/call-attempts/{call_attempt_id}/connected"
    ).status_code == 204
    finish_payload = {
        "status": "ended",
        "phase": "conversation",
        "retryable": False,
    }
    assert client.post(
        f"/api/v1/realtime/call-attempts/{call_attempt_id}/finish",
        json=finish_payload,
    ).status_code == 204
    assert client.post(
        f"/api/v1/realtime/call-attempts/{call_attempt_id}/finish",
        json={
            "status": "failed",
            "phase": "cleanup",
            "error_code": "late_failure",
        },
    ).status_code == 204

    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "ended"
    assert attempt.connected_at is not None
    assert attempt.ended_at is not None
    assert attempt.error_code is None


def test_finish_before_bootstrap_prevents_token_delivery(
    monkeypatch, client, db
):
    call_attempt_id = uuid4()
    monkeypatch.setattr(
        "app.services.realtime.httpx.AsyncClient", FakeAsyncClient
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    finish = client.post(
        f"/api/v1/realtime/call-attempts/{call_attempt_id}/finish",
        json={
            "status": "cancelled",
            "phase": "session_bootstrap",
            "error_code": "realtime_start_cancelled",
        },
    )
    assert finish.status_code == 204

    bootstrap = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert bootstrap.status_code == 409
    assert FakeAsyncClient.last_headers == {}
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "cancelled"


def test_late_provider_response_cannot_reactivate_cancelled_attempt(
    monkeypatch, client, db
):
    call_attempt_id = uuid4()

    async def late_provider_response(context, _settings, returned_attempt_id):
        assert returned_attempt_id == call_attempt_id
        with SessionLocal() as other:
            tenant = other.get(Tenant, context.id)
            finish_attempt(
                other,
                TenantContext(id=context.id, tenant=tenant),
                call_attempt_id,
                RealtimeAttemptFinishRequest(
                    status="cancelled",
                    phase="signaling",
                    error_code="realtime_start_cancelled",
                ),
            )
        return ClientSecretGrant(
            value="ek_test_late",
            expires_at=1_900_000_000,
            provider_session_id="sess_late",
            provider_request_id="req_late",
        )

    monkeypatch.setattr(
        "app.services.realtime._request_client_secret",
        late_provider_response,
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()

    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert response.status_code == 409
    assert "ek_test_late" not in response.text
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == call_attempt_id
        )
    )
    assert attempt.status == "cancelled"
    assert attempt.provider_session_id is None


def test_lifecycle_endpoints_do_not_expose_another_tenant_attempt(client, db):
    other_tenant = Tenant(
        slug=f"other-{uuid4().hex[:8]}",
        name="Other Tenant",
        industry="services",
        timezone="Europe/Berlin",
        status=TenantStatus.active,
    )
    db.add(other_tenant)
    db.commit()
    other_attempt_id = uuid4()
    db.add(
        CallSession(
            tenant_id=other_tenant.id,
            call_attempt_id=other_attempt_id,
            channel=CallChannel.browser,
            status="provisioned",
        )
    )
    db.commit()

    connected = client.post(
        f"/api/v1/realtime/call-attempts/{other_attempt_id}/connected"
    )
    finished = client.post(
        f"/api/v1/realtime/call-attempts/{other_attempt_id}/finish",
        json={"status": "ended", "phase": "conversation"},
    )

    assert connected.status_code == 404
    assert finished.status_code == 404
    db.expire_all()
    attempt = db.scalar(
        select(CallSession).where(
            CallSession.call_attempt_id == other_attempt_id
        )
    )
    assert attempt.status == "provisioned"


def test_provider_technical_message_is_redacted_in_structured_log(
    monkeypatch, client
):
    warning_records: list[dict[str, object]] = []

    def capture_warning(_message, *, extra):
        warning_records.append(extra)

    monkeypatch.setattr(
        "app.services.realtime.logger.warning", capture_warning
    )
    call_attempt_id = uuid4()
    FakeAsyncClient.response = httpx.Response(
        400,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_session",
                "message": "credential sk-proj-1234567890 must not leak",
            }
        },
        request=httpx.Request(
            "POST", "https://api.openai.com/v1/realtime/client_secrets"
        ),
    )
    monkeypatch.setattr(
        "app.services.realtime.httpx.AsyncClient", FakeAsyncClient
    )
    app.dependency_overrides[get_settings] = lambda: configured_settings()

    response = client.post(
        "/api/v1/realtime/session-bootstrap",
        json={"call_attempt_id": str(call_attempt_id)},
    )

    assert response.status_code == 502
    records = [
        record
        for record in warning_records
        if record.get("event_name") == "provider_request_failed"
        and record.get("call_attempt_id") == str(call_attempt_id)
    ]
    assert records
    assert "sk-proj-1234567890" not in records[-1]["technical_message"]
    assert "[REDACTED_CREDENTIAL]" in records[-1]["technical_message"]


def test_realtime_settings_fall_back_to_safe_defaults():
    settings = Settings(
        openai_realtime_model=" ",
        openai_realtime_voice="",
        openai_realtime_max_session_minutes=999,
        openai_realtime_transcription_enabled="invalid",
        openai_realtime_log_raw_events="invalid",
        openai_safety_identifier_salt=" ",
    )
    assert settings.openai_realtime_model == "gpt-realtime-2.1"
    assert settings.openai_realtime_voice == "marin"
    assert settings.openai_realtime_max_session_minutes == 10
    assert settings.openai_realtime_transcription_enabled is True
    assert settings.openai_realtime_log_raw_events is False
    assert settings.openai_safety_identifier_salt == "telefonagent-local-installation"


def test_safety_identifier_is_stable_and_pseudonymous(client):
    context_response = client.get("/api/v1/tenant").json()
    from app.api.dependencies import TenantContext
    from app.db.session import SessionLocal
    from app.repositories.tenant import get_tenant_by_slug

    with SessionLocal() as session:
        tenant = get_tenant_by_slug(session, "salon-haarkunst-test")
        context = TenantContext(id=tenant.id, tenant=tenant)
        first = build_safety_identifier(context, configured_settings())
        second = build_safety_identifier(context, configured_settings())
    assert first == second
    assert context_response["name"] not in first
    assert context_response["id"] not in first
