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
    if response.status_code == 429:
        code, transient = "provider_rate_limited", True
    elif response.status_code in {401, 403}:
        code, transient = "reauthorization_required", False
    elif response.status_code >= 500:
        code, transient = "provider_unavailable", True
    else:
        code, transient = "provider_request_failed", False
    logger.warning(
        "Calendar provider request failed",
        extra={"calendar_provider": provider, "provider_status": response.status_code, "calendar_error_code": code},
    )
    raise CalendarProviderError(code, "Der Kalenderanbieter konnte die Anfrage nicht verarbeiten.", transient=transient)


def provider_network_error(provider: str, exc: httpx.HTTPError) -> CalendarProviderError:
    logger.warning(
        "Calendar provider network request failed",
        extra={"calendar_provider": provider, "provider_error_type": type(exc).__name__},
    )
    return CalendarProviderError(
        "provider_unavailable", "Der Kalenderanbieter ist derzeit nicht erreichbar.", transient=True
    )
