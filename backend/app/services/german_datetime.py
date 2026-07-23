import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo


class DateTimeResolutionStatus(StrEnum):
    concrete = "concrete"
    search_window = "search_window"
    clarification_required = "clarification_required"
    past = "past"
    out_of_horizon = "out_of_horizon"
    invalid = "invalid"


@dataclass(frozen=True)
class DateTimeResolution:
    status: DateTimeResolutionStatus
    timezone: str
    start: datetime | None = None
    end: datetime | None = None
    speech: str | None = None
    reason: str | None = None
    explicit_year: bool = False


WEEKDAYS = {
    "montag": 0,
    "dienstag": 1,
    "mittwoch": 2,
    "donnerstag": 3,
    "freitag": 4,
    "samstag": 5,
    "sonntag": 6,
}
WEEKDAY_LABELS = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)
MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}
MONTH_LABELS = (
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def _normalized(value: str) -> str:
    return " ".join(value.strip().casefold().replace(",", " ").split())


def _time_from_expression(value: str) -> tuple[time | None, bool]:
    match = re.search(
        r"(?:\b(?:um|gegen)\s+)(\d{1,2})(?:(?::|\.)(\d{2}))?\s*(?:uhr)?\b"
        r"|\b(\d{1,2})(?::|\.)(\d{2})\s*(?:uhr)?\b"
        r"|\b(\d{1,2})\s*uhr\b",
        value,
    )
    if not match:
        return None, False
    hour_value = next((item for item in (match.group(1), match.group(3), match.group(5)) if item), "")
    minute_value = next((item for item in (match.group(2), match.group(4)) if item), "0")
    hour, minute = int(hour_value), int(minute_value)
    if hour > 23 or minute > 59:
        return None, True
    return time(hour, minute), True


def _next_weekday(today: date, weekday: int) -> date:
    days = (weekday - today.weekday()) % 7
    return today + timedelta(days=days or 7)


def _date_from_expression(value: str, today: date) -> tuple[date | None, bool, str | None]:
    if re.search(r"\bübermorgen\b|\buebermorgen\b", value):
        return today + timedelta(days=2), False, None
    if re.search(r"\bmorgen\b", value):
        return today + timedelta(days=1), False, None
    if re.search(r"\bheute\b", value):
        return today, False, None

    weekday_match = next(
        ((label, weekday) for label, weekday in WEEKDAYS.items() if re.search(rf"\b{label}\b", value)),
        None,
    )
    if weekday_match:
        _label, weekday = weekday_match
        if "nächste woche" in value or "naechste woche" in value:
            following_monday = today + timedelta(days=7 - today.weekday())
            return following_monday + timedelta(days=weekday), False, None
        return _next_weekday(today, weekday), False, None

    month_pattern = "|".join(MONTHS)
    named = re.search(rf"\b(\d{{1,2}})\.?\s+({month_pattern})(?:\s+(\d{{4}}))?\b", value)
    numeric = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(?:(\d{4}))?\b", value)
    if named:
        day, month = int(named.group(1)), MONTHS[named.group(2)]
        explicit_year = bool(named.group(3))
        year = int(named.group(3)) if named.group(3) else today.year
    elif numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        explicit_year = bool(numeric.group(3))
        year = int(numeric.group(3)) if numeric.group(3) else today.year
    else:
        return None, False, "date_not_understood"
    try:
        candidate = date(year, month, day)
        if not explicit_year and candidate < today:
            candidate = date(year + 1, month, day)
    except ValueError:
        return None, explicit_year, "invalid_calendar_date"
    return candidate, explicit_year, None


def _aware_local(naive: datetime, zone: ZoneInfo) -> tuple[datetime | None, str | None]:
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    first_valid = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
    second_valid = second.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
    if not first_valid and not second_valid:
        return None, "nonexistent_local_time"
    if first_valid and second_valid and first.utcoffset() != second.utcoffset():
        return None, "ambiguous_local_time"
    return first if first_valid else second, None


def _speech_for(when: datetime, *, now: datetime, explicit_year: bool) -> str:
    include_year = explicit_year or when.year != now.year
    year = f" {when.year}" if include_year else ""
    return (
        f"{WEEKDAY_LABELS[when.weekday()]}, {when.day}. "
        f"{MONTH_LABELS[when.month]}{year} um {when:%H:%M} Uhr"
    )


def resolve_german_datetime(
    expression: str,
    *,
    now: datetime,
    timezone_name: str,
    horizon_days: int,
) -> DateTimeResolution:
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    value = _normalized(expression)
    if not value:
        return DateTimeResolution(
            DateTimeResolutionStatus.invalid,
            timezone_name,
            reason="empty_expression",
        )

    if "ende des monats" in value or "monatsende" in value:
        last_day = calendar.monthrange(local_now.year, local_now.month)[1]
        start_day = max(local_now.day, max(25, last_day - 6))
        start = datetime.combine(
            date(local_now.year, local_now.month, start_day),
            time.min,
            zone,
        )
        end = datetime.combine(
            date(local_now.year, local_now.month, last_day) + timedelta(days=1),
            time.min,
            zone,
        )
        return DateTimeResolution(
            DateTimeResolutionStatus.search_window,
            timezone_name,
            start=start,
            end=end,
            speech=f"zwischen dem {start_day}. und {last_day}. {MONTH_LABELS[local_now.month]}",
            reason="fuzzy_end_of_month",
        )

    requested_time, time_was_present = _time_from_expression(value)
    if time_was_present and requested_time is None:
        return DateTimeResolution(
            DateTimeResolutionStatus.invalid,
            timezone_name,
            reason="invalid_clock_time",
        )
    requested_date, explicit_year, date_error = _date_from_expression(value, local_now.date())
    if date_error or requested_date is None:
        return DateTimeResolution(
            DateTimeResolutionStatus.invalid,
            timezone_name,
            reason=date_error or "date_not_understood",
            explicit_year=explicit_year,
        )
    if requested_time is None:
        return DateTimeResolution(
            DateTimeResolutionStatus.clarification_required,
            timezone_name,
            reason="time_required",
            explicit_year=explicit_year,
        )
    resolved, local_time_error = _aware_local(
        datetime.combine(requested_date, requested_time),
        zone,
    )
    if local_time_error or resolved is None:
        return DateTimeResolution(
            DateTimeResolutionStatus.clarification_required,
            timezone_name,
            reason=local_time_error,
            explicit_year=explicit_year,
        )
    calendar_date_without_year = (
        not explicit_year
        and (
            any(month in value for month in MONTHS)
            or bool(re.search(r"\b\d{1,2}\.\d{1,2}\.(?!\d{4})", value))
        )
    )
    if resolved <= local_now and calendar_date_without_year:
        try:
            resolved, local_time_error = _aware_local(
                datetime.combine(requested_date.replace(year=requested_date.year + 1), requested_time),
                zone,
            )
        except ValueError:
            resolved, local_time_error = None, "invalid_calendar_date"
        if local_time_error or resolved is None:
            return DateTimeResolution(
                DateTimeResolutionStatus.invalid,
                timezone_name,
                reason=local_time_error,
                explicit_year=explicit_year,
            )
    if resolved <= local_now:
        return DateTimeResolution(
            DateTimeResolutionStatus.past,
            timezone_name,
            start=resolved,
            reason="resolved_time_is_past",
            explicit_year=explicit_year,
        )
    if resolved > local_now + timedelta(days=horizon_days):
        return DateTimeResolution(
            DateTimeResolutionStatus.out_of_horizon,
            timezone_name,
            start=resolved,
            reason="booking_horizon_exceeded",
            explicit_year=explicit_year,
        )
    return DateTimeResolution(
        DateTimeResolutionStatus.concrete,
        timezone_name,
        start=resolved,
        end=resolved,
        speech=_speech_for(resolved, now=local_now, explicit_year=explicit_year),
        explicit_year=explicit_year,
    )
