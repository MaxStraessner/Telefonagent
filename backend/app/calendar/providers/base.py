from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]


@dataclass(frozen=True)
class ProviderAccount:
    account_id: str
    email: str
    display_name: str


@dataclass(frozen=True)
class ProviderCalendar:
    external_id: str
    name: str
    timezone: str
    owner_name: str
    access_role: str
    is_primary: bool
    can_write: bool


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class EventData:
    title: str
    description: str
    start: datetime
    end: datetime
    timezone: str
    booking_id: str
    idempotency_key: str
    location: str = ""


@dataclass(frozen=True)
class CreatedEvent:
    event_id: str
    reference: str


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    location: str


class CalendarProvider(ABC):
    @abstractmethod
    def build_authorization_url(self, state: str, code_challenge: str) -> str: ...

    @abstractmethod
    async def exchange_authorization_code(self, code: str, code_verifier: str) -> OAuthTokens: ...

    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> OAuthTokens: ...

    @abstractmethod
    async def get_account_information(self, access_token: str) -> ProviderAccount: ...

    @abstractmethod
    async def list_calendars(self, access_token: str) -> list[ProviderCalendar]: ...

    @abstractmethod
    async def get_busy_intervals(
        self, access_token: str, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[BusyInterval]: ...

    @abstractmethod
    async def create_event(self, access_token: str, calendar_id: str, event: EventData) -> CreatedEvent: ...

    async def list_events(
        self, access_token: str, calendar_id: str, start: datetime, end: datetime
    ) -> list[ProviderEvent]:
        return []

    @abstractmethod
    async def revoke_connection(self, access_token: str, refresh_token: str | None) -> None: ...
