import asyncio
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from app.calendar.providers.google import GOOGLE_SCOPES, GoogleCalendarProvider
from app.calendar.providers.microsoft import MICROSOFT_SCOPES, MicrosoftCalendarProvider


class FakeClient:
    responses = []
    requests = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.responses.pop(0)

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.responses.pop(0)


def test_google_authorization_uses_exact_scopes_and_pkce():
    provider = GoogleCalendarProvider("client", "secret", "https://app.test/google/callback")
    query = parse_qs(urlparse(provider.build_authorization_url("state", "challenge")).query)
    assert set(query["scope"][0].split()) == set(GOOGLE_SCOPES)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]


def test_microsoft_authorization_supports_common_accounts_and_pkce():
    provider = MicrosoftCalendarProvider("client", "secret", "https://app.test/microsoft/callback", "common")
    url = provider.build_authorization_url("state", "challenge")
    query = parse_qs(urlparse(url).query)
    assert "/common/oauth2/v2.0/authorize" in url
    assert set(query["scope"][0].split()) == set(MICROSOFT_SCOPES)
    assert query["code_challenge_method"] == ["S256"]


def test_google_freebusy_returns_only_intervals_not_calendar_content(monkeypatch):
    FakeClient.responses = [httpx.Response(200, json={"calendars": {"one": {"busy": [{"start": "2026-08-03T07:00:00Z", "end": "2026-08-03T08:00:00Z"}]}}})]
    FakeClient.requests = []
    monkeypatch.setattr("app.calendar.providers.google.httpx.AsyncClient", FakeClient)
    provider = GoogleCalendarProvider("client", "secret", "https://app.test/callback")
    intervals = asyncio.run(provider.get_busy_intervals("token", ["one"], datetime(2026, 8, 3, tzinfo=timezone.utc), datetime(2026, 8, 4, tzinfo=timezone.utc)))
    assert len(intervals) == 1
    request_body = FakeClient.requests[0][2]["json"]
    assert set(request_body) == {"timeMin", "timeMax", "timeZone", "items"}


def test_microsoft_calendar_view_ignores_free_events_and_keeps_instances(monkeypatch):
    FakeClient.responses = [httpx.Response(200, json={"value": [
        {"showAs": "free", "start": {"dateTime": "2026-08-03T07:00:00Z"}, "end": {"dateTime": "2026-08-03T08:00:00Z"}},
        {"showAs": "busy", "start": {"dateTime": "2026-08-03T09:00:00Z"}, "end": {"dateTime": "2026-08-03T10:00:00Z"}},
        {"showAs": "oof", "isAllDay": True, "start": {"dateTime": "2026-08-04T00:00:00Z"}, "end": {"dateTime": "2026-08-05T00:00:00Z"}},
    ]})]
    FakeClient.requests = []
    monkeypatch.setattr("app.calendar.providers.microsoft.httpx.AsyncClient", FakeClient)
    provider = MicrosoftCalendarProvider("client", "secret", "https://app.test/callback")
    intervals = asyncio.run(provider.get_busy_intervals("token", ["calendar"], datetime(2026, 8, 3, tzinfo=timezone.utc), datetime(2026, 8, 6, tzinfo=timezone.utc)))
    assert len(intervals) == 2
    assert "/calendarView" in FakeClient.requests[0][1]


def test_provider_event_payloads_are_busy_and_idempotent(monkeypatch):
    from app.calendar.providers import EventData

    event = EventData(
        title="Kundentermin: Test",
        description="Terminart: Beratung\nKunde: Test\nTelefon: 123\nQuelle: Telefonagent\nBuchungsnummer: booking",
        start=datetime(2026, 8, 3, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc),
        timezone="Europe/Berlin",
        booking_id="booking",
        idempotency_key="idempotency-123",
    )
    FakeClient.responses = [httpx.Response(200, json={"id": "google-event", "htmlLink": "link"})]
    FakeClient.requests = []
    monkeypatch.setattr("app.calendar.providers.google.httpx.AsyncClient", FakeClient)
    asyncio.run(GoogleCalendarProvider("client", "secret", "redirect").create_event("token", "calendar", event))
    google_body = FakeClient.requests[0][2]["json"]
    assert google_body["transparency"] == "opaque"
    assert google_body["extendedProperties"]["private"]["telefonagent_booking_id"] == "booking"

    FakeClient.responses = [httpx.Response(201, json={"id": "microsoft-event", "webLink": "link"})]
    FakeClient.requests = []
    monkeypatch.setattr("app.calendar.providers.microsoft.httpx.AsyncClient", FakeClient)
    asyncio.run(MicrosoftCalendarProvider("client", "secret", "redirect").create_event("token", "calendar", event))
    microsoft_body = FakeClient.requests[0][2]["json"]
    assert microsoft_body["showAs"] == "busy"
    assert microsoft_body["transactionId"] == "idempotency-123"
