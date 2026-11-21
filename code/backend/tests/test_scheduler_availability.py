from datetime import date, datetime, time, timezone
from types import SimpleNamespace

from app.modules.scheduler.algorithms.availability_calculator import calculate_available_slots
from app.modules.scheduler.algorithms.time_intervals import Interval


def utc(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def window(weekday, start, end, tz="UTC", valid_from=None, valid_until=None):
    return SimpleNamespace(weekday=weekday, start_time=start, end_time=end, timezone=tz,
                           valid_from=valid_from, valid_until=valid_until)


# 23 November 2026 is a Monday.
MONDAY_RANGE = (utc(23, 0), utc(24, 0))


def test_simple_monday_window():
    slots = calculate_available_slots([window(0, time(9), time(17))], [], *MONDAY_RANGE)
    assert slots == [Interval(utc(23, 9), utc(23, 17))]


def test_busy_time_splits_the_window():
    busy = [Interval(utc(23, 11), utc(23, 13))]
    slots = calculate_available_slots([window(0, time(9), time(17))], busy, *MONDAY_RANGE)
    assert slots == [Interval(utc(23, 9), utc(23, 11)), Interval(utc(23, 13), utc(23, 17))]


def test_other_weekday_gives_nothing():
    assert calculate_available_slots([window(1, time(9), time(17))], [], *MONDAY_RANGE) == []


def test_overnight_window_wraps_to_next_day():
    slots = calculate_available_slots([window(0, time(22), time(2))], [], utc(23, 0), utc(25, 0))
    assert slots == [Interval(utc(23, 22), utc(24, 2))]


def test_valid_until_excludes_the_window():
    w = window(0, time(9), time(17), valid_until=date(2026, 11, 22))
    assert calculate_available_slots([w], [], *MONDAY_RANGE) == []


def test_timezone_is_converted_to_utc():
    slots = calculate_available_slots([window(0, time(9), time(17), tz="Africa/Cairo")], [], utc(23, 0), utc(24, 0))
    assert slots == [Interval(utc(23, 7), utc(23, 15))]


def test_minimum_minutes_filters_short_gaps():
    busy = [Interval(utc(23, 9, 30), utc(23, 17))]
    slots = calculate_available_slots([window(0, time(9), time(17))], busy, *MONDAY_RANGE, minimum_minutes=45)
    assert slots == []