import base64
import hashlib
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
)
from app.calendar.providers.http import ensure_success, parse_provider_datetime, provider_network_error

GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
]


class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": " ".join(GOOGLE_SCOPES),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    async def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post("https://oauth2.googleapis.com/token", data=data)
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc
        ensure_success(response, "google")
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str):
            raise CalendarProviderError("provider_invalid_response", "Google hat kein gültiges Zugangstoken geliefert.")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
        scopes = str(payload.get("scope") or " ".join(GOOGLE_SCOPES)).split()
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
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        return await self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )

    async def get_account_information(self, access_token: str) -> ProviderAccount:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc
        ensure_success(response, "google")
        payload = response.json()
        account_id = str(payload.get("sub") or "")
        if not account_id:
            raise CalendarProviderError("provider_invalid_response", "Google hat keine Kontokennung geliefert.")
        return ProviderAccount(account_id, str(payload.get("email") or ""), str(payload.get("name") or ""))

    async def list_calendars(self, access_token: str) -> list[ProviderCalendar]:
        calendars: list[ProviderCalendar] = []
        page_token: str | None = None
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                while True:
                    params = {"maxResults": "250", "showDeleted": "false"}
                    if page_token:
                        params["pageToken"] = page_token
                    response = await client.get(
                        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                        params=params,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    ensure_success(response, "google")
                    payload = response.json()
                    for item in payload.get("items", []):
                        access_role = str(item.get("accessRole") or "reader")
                        calendars.append(
                            ProviderCalendar(
                                external_id=str(item["id"]),
                                name=str(item.get("summaryOverride") or item.get("summary") or "Kalender"),
                                timezone=str(item.get("timeZone") or "UTC"),
                                owner_name="",
                                access_role=access_role,
                                is_primary=bool(item.get("primary", False)),
                                can_write=access_role in {"writer", "owner"},
                            )
                        )
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        return calendars
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc

    async def get_busy_intervals(
        self, access_token: str, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[BusyInterval]:
        body = {
            "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeZone": "UTC",
            "items": [{"id": calendar_id} for calendar_id in calendar_ids],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://www.googleapis.com/calendar/v3/freeBusy",
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc
        ensure_success(response, "google")
        intervals: list[BusyInterval] = []
        for calendar in response.json().get("calendars", {}).values():
            if calendar.get("errors"):
                raise CalendarProviderError("provider_request_failed", "Ein ausgewählter Google Kalender ist nicht lesbar.")
            intervals.extend(
                BusyInterval(parse_provider_datetime(item["start"]), parse_provider_datetime(item["end"]))
                for item in calendar.get("busy", [])
            )
        return intervals

    async def create_event(self, access_token: str, calendar_id: str, event: EventData) -> CreatedEvent:
        digest = hashlib.sha256(event.idempotency_key.encode("utf-8")).digest()[:20]
        provider_event_id = base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")
        body = {
            "id": provider_event_id,
            "summary": event.title,
            "description": event.description,
            "start": {"dateTime": event.start.isoformat(), "timeZone": event.timezone},
            "end": {"dateTime": event.end.isoformat(), "timeZone": event.timezone},
            "transparency": "opaque",
            "extendedProperties": {
                "private": {"telefonagent_booking_id": event.booking_id, "telefonagent_idempotency_key": event.idempotency_key}
            },
        }
        url = f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=body, headers={"Authorization": f"Bearer {access_token}"})
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc
        if response.status_code == 409:
            return CreatedEvent(provider_event_id, provider_event_id)
        ensure_success(response, "google")
        payload = response.json()
        event_id = str(payload.get("id") or "")
        if not event_id:
            raise CalendarProviderError("provider_invalid_response", "Google hat keine Ereigniskennung geliefert.")
        return CreatedEvent(event_id, str(payload.get("htmlLink") or event_id))

    async def revoke_connection(self, access_token: str, refresh_token: str | None) -> None:
        token = refresh_token or access_token
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post("https://oauth2.googleapis.com/revoke", data={"token": token})
        except httpx.HTTPError as exc:
            raise provider_network_error("google", exc) from exc
        if response.status_code not in {200, 400}:
            ensure_success(response, "google")
