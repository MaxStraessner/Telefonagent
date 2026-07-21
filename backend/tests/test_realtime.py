import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.main import app
from app.models import CallSession
from app.services.realtime import build_safety_identifier


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
    assert "create_calendar_booking erst nach dieser ausdrücklichen Bestätigung" in payload["instructions"]
    assert "keine politische, medizinische, juristische, finanzielle oder private Beratung" in payload["instructions"]
    assert payload["configuration_version"] >= 1
    assert payload["speed"] == 1.0


def test_client_secret_uses_short_lived_tenant_config(monkeypatch, client, db):
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    before = db.scalar(select(func.count(CallSession.id)))

    response = client.post("/api/v1/realtime/client-secret")

    assert response.status_code == 200
    assert response.json()["client_secret"] == "ek_test_ephemeral"
    assert response.json()["session_id"] == "sess_test"
    assert set(response.json()) == {"client_secret", "expires_at", "session_id", "model", "voice", "speed", "configuration_version", "call_session_id", "tenant_id"}
    assert "server-only-test-key" not in response.text
    assert FakeAsyncClient.last_payload["expires_after"] == {"anchor": "created_at", "seconds": 60}
    session = FakeAsyncClient.last_payload["session"]
    assert [item["name"] for item in session["tools"]] == [
        "list_appointment_types", "find_available_appointments", "create_calendar_booking"
    ]
    assert session["tool_choice"] == "auto"
    assert session["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert FakeAsyncClient.last_headers["OpenAI-Safety-Identifier"].startswith("tenant_")
    assert "Salon Haarkunst" not in FakeAsyncClient.last_headers["OpenAI-Safety-Identifier"]
    db.expire_all()
    assert db.scalar(select(func.count(CallSession.id))) == before + 1


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


def test_model_is_platform_controlled_and_voice_is_database_controlled(monkeypatch, client):
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings(
        openai_realtime_model="gpt-realtime-custom",
        openai_realtime_voice="cedar",
    )
    config = client.get("/api/v1/realtime/agent-config").json()
    secret = client.post("/api/v1/realtime/client-secret").json()
    assert config["model"] == secret["model"] == "gpt-realtime-custom"
    assert config["voice"] == secret["voice"] == "marin"
    assert FakeAsyncClient.last_payload["session"]["model"] == "gpt-realtime-custom"
    assert FakeAsyncClient.last_payload["session"]["audio"]["output"] == {"voice": "marin", "speed": 1.0}


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


def test_provider_timeout_is_controlled(monkeypatch, client):
    FakeAsyncClient.response = httpx.Response(
        200,
        json={"value": "ek_test", "expires_at": 1},
        request=httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets"),
    )
    FakeAsyncClient.error = httpx.ReadTimeout("late")
    monkeypatch.setattr("app.services.realtime.httpx.AsyncClient", FakeAsyncClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post("/api/v1/realtime/client-secret")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "realtime_provider_timeout"
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
