from datetime import datetime, timezone

import pytest

from app.services.german_datetime import DateTimeResolutionStatus, resolve_german_datetime


def resolve(expression: str, now: datetime, horizon: int = 60):
    return resolve_german_datetime(
        expression,
        now=now,
        timezone_name="Europe/Berlin",
        horizon_days=horizon,
    )


def test_relative_days_and_year_change_are_timezone_aware():
    result = resolve("übermorgen um 14 Uhr", datetime(2026, 12, 30, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.concrete
    assert result.start.isoformat() == "2027-01-01T14:00:00+01:00"
    assert "2027" in result.speech


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Freitag um 10 Uhr", "2026-07-24T10:00:00+02:00"),
        ("nächsten Freitag um 10 Uhr", "2026-07-24T10:00:00+02:00"),
        ("Dienstag nächste Woche um 9:30", "2026-07-28T09:30:00+02:00"),
    ],
)
def test_weekdays_are_resolved_deterministically(expression, expected):
    result = resolve(expression, datetime(2026, 7, 23, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.concrete
    assert result.start.isoformat() == expected


def test_date_without_year_uses_next_future_date():
    result = resolve(
        "15. Januar um 11 Uhr",
        datetime(2026, 12, 20, 8, tzinfo=timezone.utc),
        horizon=60,
    )
    assert result.status == DateTimeResolutionStatus.concrete
    assert result.start.isoformat() == "2027-01-15T11:00:00+01:00"
    assert result.explicit_year is False
    assert "2027" in result.speech


def test_date_without_year_on_same_day_after_requested_time_moves_to_next_year():
    result = resolve(
        "23. Juli um 9 Uhr",
        datetime(2026, 7, 23, 12, tzinfo=timezone.utc),
        horizon=400,
    )
    assert result.status == DateTimeResolutionStatus.concrete
    assert result.start.year == 2027


def test_current_year_is_not_spoken_without_need():
    result = resolve("morgen um 14 Uhr", datetime(2026, 7, 23, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.concrete
    assert "2026" not in result.speech


def test_end_of_month_is_a_search_window_not_an_invented_slot():
    result = resolve("Ende des Monats", datetime(2026, 7, 10, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.search_window
    assert result.start.isoformat() == "2026-07-25T00:00:00+02:00"
    assert result.end.isoformat() == "2026-08-01T00:00:00+02:00"


def test_missing_time_requires_clarification():
    result = resolve("morgen", datetime(2026, 7, 23, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.clarification_required
    assert result.reason == "time_required"


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("31. Februar um 10 Uhr", "invalid_calendar_date"),
        ("morgen um 25 Uhr", "invalid_clock_time"),
        ("irgendwann bald", "date_not_understood"),
    ],
)
def test_invalid_values_are_not_guessed(expression, reason):
    result = resolve(expression, datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
    assert result.status == DateTimeResolutionStatus.invalid
    assert result.reason == reason


def test_explicit_past_and_booking_horizon_are_distinguished():
    past = resolve("1. Januar 2026 um 10 Uhr", datetime(2026, 7, 23, 8, tzinfo=timezone.utc))
    future = resolve(
        "1. Dezember 2026 um 10 Uhr",
        datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
        horizon=30,
    )
    assert past.status == DateTimeResolutionStatus.past
    assert future.status == DateTimeResolutionStatus.out_of_horizon


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("29. März 2026 um 2:30", "nonexistent_local_time"),
        ("25. Oktober 2026 um 2:30", "ambiguous_local_time"),
    ],
)
def test_dst_gap_and_overlap_require_clarification(expression, reason):
    result = resolve(expression, datetime(2026, 1, 1, 8, tzinfo=timezone.utc), horizon=365)
    assert result.status == DateTimeResolutionStatus.clarification_required
    assert result.reason == reason
