"""Redacted, read-only telephony readiness checks. Never opens voice sessions."""
import argparse
import ipaddress
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from pydantic import ValidationError

from app.core.config import Settings
from app.services.twilio import stream_status_url, voice_url

ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = (
    "OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
    "TWILIO_STREAM_TOKEN_SECRET", "AUTH_HMAC_SECRET", "CALENDAR_TOKEN_ENCRYPTION_KEY",
    "GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET",
    "MICROSOFT_CALENDAR_CLIENT_ID", "MICROSOFT_CALENDAR_CLIENT_SECRET",
)


def public_https(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if host == "localhost" or "." not in host or host.endswith((".localhost", ".test", ".invalid", ".example", ".local")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True  # Syntax only; DNS/TLS reachability requires a separate check.
    except ValueError:
        return False


def configuration_report(settings: Settings, values: dict) -> dict:
    present = {name: bool(getattr(settings, name.lower())) for name in SECRET_NAMES}
    fernet_valid = False
    if settings.calendar_token_encryption_key:
        try:
            Fernet(settings.calendar_token_encryption_key.encode())
            fernet_valid = True
        except (ValueError, TypeError):
            pass
    test_url = str(values.get("TELEPHONY_TEST_PUBLIC_URL") or "").rstrip("/")
    test_number = str(values.get("TWILIO_TEST_PHONE_NUMBER") or "")
    return {
        "credentials_present": present,
        "account_sid_format_valid": bool(re.fullmatch(r"AC[0-9a-fA-F]{32}", settings.twilio_account_sid or "")),
        "auth_token_format_valid": bool(re.fullmatch(r"[0-9a-fA-F]{32}", settings.twilio_auth_token or "")),
        "stream_secret_minimum_met": len((settings.twilio_stream_token_secret or "").encode()) >= 32,
        "calendar_encryption_key_valid": fernet_valid,
        "telephony_complete": settings.telephony_configured,
        "google_oauth_complete": settings.google_calendar_configured,
        "microsoft_oauth_complete": settings.microsoft_calendar_configured,
        "realtime_2_1_selected": settings.openai_realtime_model == "gpt-realtime-2.1",
        "analysis_nano_selected": settings.openai_call_analysis_model == "gpt-5.4-nano",
        "transcription_enabled": settings.openai_realtime_transcription_enabled,
        "raw_event_logging_disabled": not settings.openai_realtime_log_raw_events,
        "app_public_https_syntax": public_https(settings.app_base_url),
        "frontend_public_https_syntax": public_https(settings.frontend_url),
        "test_number_explicitly_selected": bool(re.fullmatch(r"\+[1-9][0-9]{7,14}", test_number)),
        "test_public_origin_configured": public_https(test_url),
        "test_origin_differs_from_current": bool(test_url) and urlsplit(test_url).netloc.lower() != urlsplit(settings.app_base_url).netloc.lower(),
        "does_not_verify": ["credential_entitlements", "dns_tls_websocket_reachability", "calendar_oauth_grant", "production_configuration"],
    }


def _get(client: httpx.Client, url: str, **kwargs) -> tuple[dict | None, str]:
    try:
        response = client.get(url, **kwargs)
        if response.status_code != 200:
            return None, f"http_{response.status_code}"
        data = response.json()
        return (data, "ok") if isinstance(data, dict) else (None, "invalid_response")
    except (httpx.HTTPError, ValueError):
        # Never emit arbitrary URLs, response bodies, headers, or exception text.
        return None, "request_failed"


def provider_report(settings: Settings, values: dict, client: httpx.Client) -> dict:
    result = {"voice_sessions_opened": 0, "calls_created": 0, "provider_mutations": 0}
    if settings.openai_api_key:
        models, status = _get(client, "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {settings.openai_api_key}"})
        ids = {item.get("id") for item in (models or {}).get("data", []) if isinstance(item, dict)}
        result["openai"] = {"status": status, "realtime_model_listed": settings.openai_realtime_model in ids,
            "analysis_model_listed": settings.openai_call_analysis_model in ids, "live_1_listed": "gpt-live-1" in ids,
            "audio_session_and_billing_not_verified": True}
    else:
        result["openai"] = {"status": "missing_credentials"}
    sid, token = settings.twilio_account_sid, settings.twilio_auth_token
    if not sid or not token or not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid):
        result["twilio"] = {"status": "missing_or_invalid_credentials"}
        return result
    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    account, account_status = _get(client, base + ".json", auth=(sid, token))
    numbers, number_status = _get(client, base + "/IncomingPhoneNumbers.json", params={"PageSize": 100}, auth=(sid, token))
    rows = (numbers or {}).get("incoming_phone_numbers", [])
    rows = [row for row in rows if isinstance(row, dict)]
    selected = [row for row in rows if row.get("phone_number") == values.get("TWILIO_TEST_PHONE_NUMBER")]
    test_url = str(values.get("TELEPHONY_TEST_PUBLIC_URL") or "").rstrip("/")
    result["twilio"] = {
        "account_status": account_status, "account_active": (account or {}).get("status") == "active",
        "trial_account": (account or {}).get("type") == "Trial", "numbers_status": number_status,
        "numbers_in_page": len(rows), "inventory_truncated": bool((numbers or {}).get("next_page_uri")),
        "voice_capable_in_page": sum(bool(row.get("capabilities", {}).get("voice")) for row in rows),
        "voice_unrouted_in_page": sum(not any(row.get(key) for key in ("voice_url", "voice_application_sid", "trunk_sid")) for row in rows),
        "current_app_voice_routes_in_page": sum(row.get("voice_url") == voice_url(settings) for row in rows),
        "current_app_status_routes_in_page": sum(row.get("status_callback") == stream_status_url(settings) for row in rows),
        "selected_test_number_found": len(selected) == 1,
        "selected_test_number_voice_capable": len(selected) == 1 and bool(selected[0].get("capabilities", {}).get("voice")),
        "selected_test_voice_route_matches": len(selected) == 1 and public_https(test_url) and selected[0].get("voice_url") == test_url + "/api/v1/twilio/voice",
        "selected_test_status_route_matches": len(selected) == 1 and public_https(test_url) and selected[0].get("status_callback") == test_url + "/api/v1/twilio/stream-status",
        "selected_test_uses_no_app_or_trunk": len(selected) == 1 and not selected[0].get("voice_application_sid") and not selected[0].get("trunk_sid"),
    }
    return result


def compose_settings(root: Path, env_file: Path) -> tuple[Settings | None, dict]:
    # Capture resolved Compose internally: it contains secrets and must not be logged.
    try:
        completed = subprocess.run(["docker-compose", "--env-file", str(env_file), "-f", str(root / "docker-compose.yml"), "--profile", "migration", "config", "--format", "json"], capture_output=True, text=True, check=False, timeout=30)
        if completed.returncode:
            return None, {"compose_valid": False}
        services = json.loads(completed.stdout)["services"]
        backend = services["backend"]["environment"]
        settings = Settings(_env_file=None, **{key.lower(): value for key, value in backend.items()})
        migration = services["migrate"]
        return settings, {
            "compose_valid": True, "runtime_has_no_migration_url": not backend.get("MIGRATION_DATABASE_URL"),
            "migration_command_is_upgrade_head": migration.get("command") == ["alembic", "upgrade", "head"],
            "migration_url_present": bool(migration["environment"].get("MIGRATION_DATABASE_URL")),
            "runtime_bootstrap_disabled": not settings.dev_bootstrap_enabled,
        }
    except ValidationError as exc:
        return None, {"compose_valid": True, "settings_valid": False, "validation_fields": [list(error["loc"]) for error in exc.errors(include_input=False, include_context=False, include_url=False)]}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None, {"compose_valid": False, "error": "compose_or_settings_unavailable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--verify-providers", action="store_true", help="Only GET /models, account and owned numbers; no voice connections.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    values = {**dotenv_values(args.env_file), **os.environ}
    settings, composition = compose_settings(ROOT, args.env_file)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "local_env_exists": args.env_file.is_file(), "compose": composition, "real_call_readiness": "not_approved"}
    if settings:
        report["configuration"] = configuration_report(settings, values)
        if args.verify_providers:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                report["providers"] = provider_report(settings, values, client)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if settings else 1


if __name__ == "__main__":
    raise SystemExit(main())
