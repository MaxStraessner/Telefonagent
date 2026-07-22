from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx

from app.calendar.errors import CalendarProviderError
from app.calendar.providers.base import (
    BusyInterval,
    CalendarProvider,
    CreatedEvent,
    EventData,
    OAuthTokens,
    ProviderAccount,
    ProviderCalendar,
    ProviderEvent,
)
from app.calendar.providers.http import ensure_success, parse_provider_datetime, provider_network_error

MICROSOFT_SCOPES = ["openid", "profile", "email", "offline_access", "User.Read", "Calendars.ReadWrite"]


class MicrosoftCalendarProvider(CalendarProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, tenant: str = "common"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.tenant = tenant or "common"
        self.identity_base = f"https://login.microsoftonline.com/{quote(self.tenant, safe='')}/oauth2/v2.0"

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "response_mode": "query",
                "scope": " ".join(MICROSOFT_SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.identity_base}/authorize?{query}"

    async def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(f"{self.identity_base}/token", data=data)
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        ensure_success(response, "microsoft")
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str):
            raise CalendarProviderError("provider_invalid_response", "Microsoft hat kein gültiges Zugangstoken geliefert.")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
        scopes = str(payload.get("scope") or " ".join(MICROSOFT_SCOPES)).split()
        refresh_token = payload.get("refresh_token")
        return OAuthTokens(access_token, refresh_token if isinstance(refresh_token, str) else None, expires_at, scopes)

    async def exchange_authorization_code(self, code: str, code_verifier: str) -> OAuthTokens:
        return await self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(MICROSOFT_SCOPES),
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        return await self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(MICROSOFT_SCOPES),
            }
        )

    async def get_account_information(self, access_token: str) -> ProviderAccount:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(
                    "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        ensure_success(response, "microsoft")
        payload = response.json()
        account_id = str(payload.get("id") or "")
        if not account_id:
            raise CalendarProviderError("provider_invalid_response", "Microsoft hat keine Kontokennung geliefert.")
        email = str(payload.get("mail") or payload.get("userPrincipalName") or "")
        return ProviderAccount(account_id, email, str(payload.get("displayName") or ""))

    async def list_calendars(self, access_token: str) -> list[ProviderCalendar]:
        calendars: list[ProviderCalendar] = []
        url: str | None = (
            "https://graph.microsoft.com/v1.0/me/calendars"
            "?$select=id,name,owner,isDefaultCalendar,canEdit&$top=100"
        )
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                while url:
                    response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
                    ensure_success(response, "microsoft")
                    payload = response.json()
                    for item in payload.get("value", []):
                        owner = item.get("owner") or {}
                        can_write = bool(item.get("canEdit", False))
                        calendars.append(
                            ProviderCalendar(
                                external_id=str(item["id"]),
                                name=str(item.get("name") or "Kalender"),
                                timezone="UTC",
                                owner_name=str(owner.get("name") or owner.get("address") or ""),
                                access_role="writer" if can_write else "reader",
                                is_primary=bool(item.get("isDefaultCalendar", False)),
                                can_write=can_write,
                            )
                        )
                    next_url = payload.get("@odata.nextLink")
                    url = str(next_url) if next_url else None
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        return calendars

    async def get_busy_intervals(
        self, access_token: str, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[BusyInterval]:
        intervals: list[BusyInterval] = []
        params = {
            "startDateTime": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endDateTime": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "$select": "start,end,showAs,isAllDay",
            "$top": "1000",
        }
        headers = {"Authorization": f"Bearer {access_token}", "Prefer": 'outlook.timezone="UTC"'}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for calendar_id in calendar_ids:
                    url: str | None = (
                        f"https://graph.microsoft.com/v1.0/me/calendars/{quote(calendar_id, safe='')}/calendarView"
                    )
                    first = True
                    while url:
                        response = await client.get(url, params=params if first else None, headers=headers)
                        first = False
                        ensure_success(response, "microsoft")
                        payload = response.json()
                        for item in payload.get("value", []):
                            if str(item.get("showAs") or "busy").lower() == "free":
                                continue
                            start_value = (item.get("start") or {}).get("dateTime")
                            end_value = (item.get("end") or {}).get("dateTime")
                            if start_value and end_value:
                                intervals.append(
                                    BusyInterval(parse_provider_datetime(start_value), parse_provider_datetime(end_value))
                                )
                        next_url = payload.get("@odata.nextLink")
                        url = str(next_url) if next_url else None
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        return intervals

    async def create_event(self, access_token: str, calendar_id: str, event: EventData) -> CreatedEvent:
        body = {
            "subject": event.title,
            "body": {"contentType": "text", "content": event.description},
            "start": {"dateTime": event.start.astimezone(timezone.utc).replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": event.end.astimezone(timezone.utc).replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
            "showAs": "busy",
            "location": {"displayName": event.location},
            "transactionId": event.idempotency_key,
            "singleValueLegacyExtendedProperties": [
                {
                    "id": "String {66f5a359-4659-4830-9070-00040ec6ac6e} Name TelefonagentBookingId",
                    "value": event.booking_id,
                }
            ],
        }
        url = f"https://graph.microsoft.com/v1.0/me/calendars/{quote(calendar_id, safe='')}/events"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=body, headers={"Authorization": f"Bearer {access_token}"})
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        ensure_success(response, "microsoft")
        payload = response.json()
        event_id = str(payload.get("id") or "")
        if not event_id:
            raise CalendarProviderError("provider_invalid_response", "Microsoft hat keine Ereigniskennung geliefert.")
        return CreatedEvent(event_id, str(payload.get("webLink") or event_id))

    async def list_events(self, access_token: str, calendar_id: str, start: datetime, end: datetime) -> list[ProviderEvent]:
        url: str | None = f"https://graph.microsoft.com/v1.0/me/calendars/{quote(calendar_id, safe='')}/calendarView"
        params = {
            "startDateTime": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endDateTime": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "$select": "id,subject,start,end,location,isCancelled",
            "$top": "1000",
        }
        headers = {"Authorization": f"Bearer {access_token}", "Prefer": 'outlook.timezone="UTC"'}
        events: list[ProviderEvent] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                first = True
                while url:
                    response = await client.get(url, params=params if first else None, headers=headers)
                    first = False
                    ensure_success(response, "microsoft")
                    payload = response.json()
                    for item in payload.get("value", []):
                        if item.get("isCancelled"):
                            continue
                        start_value = (item.get("start") or {}).get("dateTime")
                        end_value = (item.get("end") or {}).get("dateTime")
                        if start_value and end_value:
                            events.append(ProviderEvent(
                                event_id=str(item.get("id") or ""),
                                title=str(item.get("subject") or "Belegter Termin"),
                                start=parse_provider_datetime(start_value),
                                end=parse_provider_datetime(end_value),
                                location=str((item.get("location") or {}).get("displayName") or ""),
                            ))
                    next_url = payload.get("@odata.nextLink")
                    url = str(next_url) if next_url else None
        except httpx.HTTPError as exc:
            raise provider_network_error("microsoft", exc) from exc
        return events

    async def revoke_connection(self, access_token: str, refresh_token: str | None) -> None:
        # Microsoft exposes no narrow delegated OAuth token-revocation endpoint. Local encrypted
        # credentials are deleted; operators can additionally revoke app consent in Entra/My Apps.
        return None
