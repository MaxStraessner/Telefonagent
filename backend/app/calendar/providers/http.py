import logging
from datetime import datetime, timezone

import httpx

from app.calendar.errors import CalendarProviderError

logger = logging.getLogger(__name__)


def parse_provider_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_success(response: httpx.Response, provider: str) -> None:
    if response.is_success:
        return
    error_reason = ""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            error_reason = str(error.get("status") or error.get("message") or "").lower()
            details = error.get("errors") or []
            if details and isinstance(details, list) and isinstance(details[0], dict):
                error_reason = str(details[0].get("reason") or error_reason).lower()
        elif isinstance(error, str):
            error_reason = error.lower()
    except (ValueError, TypeError):
        pass
    requires_reauthorization = False
    if response.status_code == 429:
        code, transient = "provider_rate_limited", True
    elif response.status_code == 401:
        code, transient, requires_reauthorization = "reauthorization_required", False, True
    elif response.status_code == 403 and error_reason in {
        "autherror", "invalidcredentials", "insufficientpermissions", "permission_denied",
    }:
        code, transient, requires_reauthorization = "reauthorization_required", False, True
    elif response.status_code == 403:
        code, transient = "provider_access_denied", False
    elif error_reason in {"invalid_grant", "invalid_client", "access_revoked", "refresh_token_invalid"}:
        code, transient, requires_reauthorization = "reauthorization_required", False, True
    elif response.status_code >= 500:
        code, transient = "provider_unavailable", True
    else:
        code, transient = "provider_request_failed", False
    logger.warning(
        "Calendar provider request failed",
        extra={"calendar_provider": provider, "provider_status": response.status_code, "calendar_error_code": code},
    )
    raise CalendarProviderError(
        code,
        "Der Kalenderanbieter konnte die Anfrage nicht verarbeiten.",
        transient=transient,
        reauthorization_required=requires_reauthorization,
    )


def provider_network_error(provider: str, exc: httpx.HTTPError) -> CalendarProviderError:
    logger.warning(
        "Calendar provider network request failed",
        extra={"calendar_provider": provider, "provider_error_type": type(exc).__name__},
    )
    return CalendarProviderError(
        "provider_unavailable", "Der Kalenderanbieter ist derzeit nicht erreichbar.", transient=True
    )
