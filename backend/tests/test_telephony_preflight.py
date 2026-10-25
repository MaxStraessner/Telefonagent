import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.telephony_preflight import compose_settings, configuration_report, provider_report, public_https


@pytest.mark.parametrize("url", ["http://lab.company.org", "https://localhost", "https://127.0.0.1", "https://192.168.1.5", "https://lab.example.test", "https://user:password@lab.company.org", "https://lab.company.org?token=private"])
def test_preflight_rejects_nonpublic_or_credentialed_origins(url):
    assert not public_https(url)


def test_preflight_never_serializes_credentials_or_selected_number():
    secrets = {"openai_api_key": "sk-" + "s" * 40, "twilio_account_sid": "AC" + "a" * 32,
        "twilio_auth_token": "b" * 32, "twilio_stream_token_secret": "secret-stream-" * 4}
    settings = Settings(_env_file=None, **secrets)
    number = "+493000000001"
    rendered = json.dumps(configuration_report(settings, {"TWILIO_TEST_PHONE_NUMBER": number}))
    assert all(value not in rendered for value in [*secrets.values(), number])
    assert '"test_number_explicitly_selected": true' in rendered


def test_preflight_provider_check_only_reads_fixed_provider_endpoints():
    settings = Settings(_env_file=None, openai_api_key="sk-private", twilio_account_sid="AC" + "a" * 32, twilio_auth_token="b" * 32)
    visited = []
    def transport(request):
        visited.append((request.method, request.url.host, request.url.path))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-realtime-2.1"}, {"id": "gpt-5.4-nano"}]})
        if request.url.path.endswith("IncomingPhoneNumbers.json"):
            return httpx.Response(200, json={"incoming_phone_numbers": [{"phone_number": "+493000000001", "capabilities": {"voice": True}}]})
        return httpx.Response(200, json={"status": "active", "type": "Full", "auth_token": "must-not-be-serialized"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        report = provider_report(settings, {}, client)
    assert len(visited) == 3 and all(method == "GET" for method, _, _ in visited)
    assert {host for _, host, _ in visited} == {"api.openai.com", "api.twilio.com"}
    assert all("Calls" not in path and "live" not in path and "realtime" not in path for _, _, path in visited)
    assert report["openai"]["realtime_model_listed"] and report["twilio"]["account_active"]
    assert "must-not-be-serialized" not in json.dumps(report)
    assert "+493000000001" not in json.dumps(report)


def test_preflight_does_not_print_provider_error_body():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "private-provider-detail"}))) as client:
        report = provider_report(Settings(_env_file=None, openai_api_key="sk-private"), {}, client)
    assert report["openai"]["status"] == "http_401"
    assert "private-provider-detail" not in json.dumps(report)


def test_preflight_includes_migration_profile_and_redacts_validation_inputs(monkeypatch, tmp_path):
    def compose(args, **kwargs):
        assert args[args.index("--profile") + 1] == "migration"
        return SimpleNamespace(returncode=0, stdout=json.dumps({"services": {
            "backend": {"environment": {"TWILIO_STREAM_TOKEN_SECRET": "private-too-short"}},
            "migrate": {"command": ["alembic", "upgrade", "head"], "environment": {}}}}))
    monkeypatch.setattr("app.telephony_preflight.subprocess.run", compose)
    settings, report = compose_settings(tmp_path, tmp_path / ".env")
    assert settings is None and report["settings_valid"] is False
    assert "private-too-short" not in json.dumps(report)
