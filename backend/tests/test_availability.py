from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.calendar.providers import BusyInterval
from app.models import BookingConfiguration, CalendarAppointmentType, CalendarBusinessHour, CalendarLocationType
from app.services.availability import calculate_available_slots, merge_busy_intervals

BERLIN = ZoneInfo("Europe/Berlin")


def rules(**overrides):
    values = {
        "timezone": "Europe/Berlin",
        "slot_interval_minutes": 15,
        "minimum_notice_minutes": 0,
        "maximum_booking_horizon_days": 60,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "maximum_suggestions_per_request": 10,
    }
    values.update(overrides)
    return BookingConfiguration(**values)


def appointment(**overrides):
    values = {
        "name": "Beratung",
        "duration_minutes": 30,
        "buffer_before_minutes": None,
        "buffer_after_minutes": None,
        "location_type": CalendarLocationType.phone,
        "location_text": "",
        "is_active": True,
    }
    values.update(overrides)
    return CalendarAppointmentType(**values)


def hours(*windows):
    return [
        CalendarBusinessHour(weekday=weekday, start_time=start, end_time=end, is_active=True)
        for weekday, start, end in windows
    ]


def dt(day: int, hour: int, minute: int = 0):
    return datetime(2026, 8, day, hour, minute, tzinfo=BERLIN)


def slots(config=None, appointment_type=None, business_hours=None, busy=None, start=None, end=None, now=None, **kwargs):
    return calculate_available_slots(
        config or rules(),
        business_hours or hours((0, time(9), time(12))),
        appointment_type or appointment(),
        busy or [],
        start or dt(3, 8),
        end or dt(3, 18),
        now=now or dt(2, 8),
        **kwargs,
    )


def test_simple_slots_are_grid_aligned_and_chronological():
    result = slots(maximum_results=3)
    assert [item[0].astimezone(BERLIN).strftime("%H:%M") for item in result] == ["09:00", "09:15", "09:30"]


def test_multiple_business_windows_same_day_are_supported():
    result = slots(
        business_hours=hours((0, time(8), time(10)), (0, time(13), time(14))),
        appointment_type=appointment(duration_minutes=60),
    )
    local = [item[0].astimezone(BERLIN).strftime("%H:%M") for item in result]
    assert local == ["08:00", "08:15", "08:30", "08:45", "09:00", "13:00"]


def test_overlapping_and_adjacent_busy_intervals_are_merged():
    merged = merge_busy_intervals(
        [
            BusyInterval(dt(3, 9).astimezone(ZoneInfo("UTC")), dt(3, 10).astimezone(ZoneInfo("UTC"))),
            BusyInterval(dt(3, 9, 30).astimezone(ZoneInfo("UTC")), dt(3, 10, 30).astimezone(ZoneInfo("UTC"))),
            BusyInterval(dt(3, 10, 30).astimezone(ZoneInfo("UTC")), dt(3, 11).astimezone(ZoneInfo("UTC"))),
        ]
    )
    assert len(merged) == 1
    assert merged[0].start == dt(3, 9).astimezone(ZoneInfo("UTC"))
    assert merged[0].end == dt(3, 11).astimezone(ZoneInfo("UTC"))


def test_buffers_before_and_after_existing_and_new_appointments_apply():
    result = slots(
        config=rules(buffer_before_minutes=15, buffer_after_minutes=15),
        appointment_type=appointment(duration_minutes=30, buffer_before_minutes=10, buffer_after_minutes=20),
        business_hours=hours((0, time(8), time(12))),
        busy=[BusyInterval(dt(3, 9).astimezone(ZoneInfo("UTC")), dt(3, 9, 30).astimezone(ZoneInfo("UTC")))],
    )
    local = [item[0].astimezone(BERLIN).strftime("%H:%M") for item in result]
    assert "08:30" not in local
    assert "09:45" not in local
    assert local[0] == "10:00"


def test_minimum_notice_and_booking_horizon_clamp_search():
    result = slots(
        config=rules(minimum_notice_minutes=120, maximum_booking_horizon_days=1),
        business_hours=hours((0, time(8), time(18))),
        now=dt(3, 8),
        start=dt(3, 8),
        end=dt(5, 18),
    )
    assert result[0][0].astimezone(BERLIN).strftime("%H:%M") == "10:00"
    assert all(item[0] < dt(4, 8).astimezone(ZoneInfo("UTC")) for item in result)


def test_all_day_busy_event_blocks_the_complete_business_day():
    result = slots(
        busy=[BusyInterval(dt(3, 0).astimezone(ZoneInfo("UTC")), dt(4, 0).astimezone(ZoneInfo("UTC")))],
    )
    assert result == []


def test_multiple_calendar_busy_intervals_are_combined_without_event_details():
    result = slots(
        business_hours=hours((0, time(9), time(11))),
        busy=[
            BusyInterval(dt(3, 9).astimezone(ZoneInfo("UTC")), dt(3, 9, 30).astimezone(ZoneInfo("UTC"))),
            BusyInterval(dt(3, 10).astimezone(ZoneInfo("UTC")), dt(3, 10, 30).astimezone(ZoneInfo("UTC"))),
        ],
    )
    assert [item[0].astimezone(BERLIN).strftime("%H:%M") for item in result] == ["09:30", "10:30"]


@pytest.mark.parametrize("timezone_name", ["Europe/Berlin", "America/New_York", "Asia/Tokyo"])
def test_different_account_timezones_keep_local_business_hours(timezone_name):
    zone = ZoneInfo(timezone_name)
    monday = datetime(2026, 8, 3, 0, 0, tzinfo=zone)
    result = calculate_available_slots(
        rules(timezone=timezone_name),
        hours((0, time(9), time(10))),
        appointment(),
        [],
        monday,
        monday + timedelta(days=1),
        now=monday - timedelta(days=1),
    )
    assert result[0][0].astimezone(zone).strftime("%H:%M") == "09:00"


def test_summer_time_transition_skips_nonexistent_local_times():
    start = datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN)
    result = calculate_available_slots(
        rules(slot_interval_minutes=60),
        hours((6, time(1), time(5))),
        appointment(duration_minutes=60),
        [],
        start,
        start + timedelta(days=1),
        now=start - timedelta(days=1),
    )
    assert [item[0].astimezone(BERLIN).hour for item in result] == [1, 3, 4]


def test_appointment_that_does_not_fit_window_is_not_returned():
    assert slots(
        business_hours=hours((0, time(9), time(9, 45))),
        appointment_type=appointment(duration_minutes=60),
    ) == []


def test_preferred_day_and_time_filter_without_inventing_slots():
    result = slots(
        business_hours=hours((0, time(8), time(20))),
        preferred_day=date(2026, 8, 3),
        preferred_time_range="afternoon",
        maximum_results=2,
    )
    assert [item[0].astimezone(BERLIN).hour for item in result] == [12, 12]
