from app.calendar.errors import CalendarConfigurationError
from app.calendar.providers.base import CalendarProvider
from app.calendar.providers.google import GoogleCalendarProvider
from app.calendar.providers.microsoft import MicrosoftCalendarProvider
from app.core.config import Settings
from app.models import CalendarProviderName


def create_calendar_provider(provider: CalendarProviderName | str, settings: Settings) -> CalendarProvider:
    provider_name = CalendarProviderName(provider)
    if provider_name == CalendarProviderName.google and settings.google_calendar_configured:
        return GoogleCalendarProvider(
            settings.google_calendar_client_id or "",
            settings.google_calendar_client_secret or "",
            settings.google_calendar_redirect_uri or "",
        )
    if provider_name == CalendarProviderName.microsoft and settings.microsoft_calendar_configured:
        return MicrosoftCalendarProvider(
            settings.microsoft_calendar_client_id or "",
            settings.microsoft_calendar_client_secret or "",
            settings.microsoft_calendar_redirect_uri or "",
            settings.microsoft_calendar_tenant,
        )
    raise CalendarConfigurationError(
        "provider_not_configured",
        "Der ausgewählte Kalenderanbieter ist serverseitig noch nicht vollständig konfiguriert.",
    )
