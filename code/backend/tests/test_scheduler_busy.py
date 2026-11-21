from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.modules.scheduler.algorithms.availability_calculator import calculate_available_slots
from app.modules.scheduler.algorithms.busy_calculator import calculate_busy_blocks
from app.modules.scheduler.algorithms.time_intervals import Interval


def utc(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def once(start, end):
    return SimpleNamespace(event_id=uuid4(), recurrence_type="ONCE", start_at=start, end_at=end,
                           recurrence_first_date=None, recurrence_until_date=None,
                           recurrence_start_time=None, recurrence_end_time=None,
                           recurrence_end_day_offset=None, recurrence_timezone=None)


def weekly(first, start, end, offset=0, tz="UTC", until=None):
    return SimpleNamespace(event_id=uuid4(), recurrence_type="WEEKLY", start_at=None, end_at=None,
                           recurrence_first_date=first, recurrence_until_date=until,
                           recurrence_start_time=start, recurrence_end_time=end,
                           recurrence_end_day_offset=offset, recurrence_timezone=tz)


def fixed_task(start, end):
    return SimpleNamespace(task_id=uuid4(), task_type="FIXED", fixed_start_at=start, fixed_end_at=end)


def flexible_task():
    return SimpleNamespace(task_id=uuid4(), task_type="FLEXIBLE", fixed_start_at=None, fixed_end_at=None)


# 16 and 23 November 2026 are Mondays.


def test_once_event_inside_range():
    blocks = calculate_busy_blocks([once(utc(23, 10), utc(23, 11))], [], utc(23, 0), utc(24, 0))
    assert [b.interval for b in blocks] == [Interval(utc(23, 10), utc(23, 11))]


def test_once_event_outside_range_is_ignored():
    assert calculate_busy_blocks([once(utc(25, 10), utc(25, 11))], [], utc(23, 0), utc(24, 0)) == []


def test_weekly_event_repeats_every_seven_days():
    event = weekly(date(2026, 11, 16), time(9), time(10))
    blocks = calculate_busy_blocks([event], [], utc(16, 0), utc(30, 0))
    assert [b.occurrence_date for b in blocks] == [date(2026, 11, 16), date(2026, 11, 23)]


def test_weekly_until_date_stops_the_rule():
    event = weekly(date(2026, 11, 16), time(9), time(10), until=date(2026, 11, 20))
    blocks = calculate_busy_blocks([event], [], utc(16, 0), utc(30, 0))
    assert [b.occurrence_date for b in blocks] == [date(2026, 11, 16)]


def test_overnight_weekly_event_reaches_into_next_day():
    event = weekly(date(2026, 11, 16), time(22), time(2), offset=1)
    blocks = calculate_busy_blocks([event], [], utc(24, 0), utc(25, 0))
    assert [b.interval for b in blocks] == [Interval(utc(23, 22), utc(24, 2))]


def test_weekly_event_uses_its_own_timezone():
    event = weekly(date(2026, 11, 16), time(9), time(10), tz="Africa/Cairo")
    blocks = calculate_busy_blocks([event], [], utc(23, 0), utc(24, 0))
    assert [b.interval for b in blocks] == [Interval(utc(23, 7), utc(23, 8))]


def test_only_fixed_tasks_are_busy():
    tasks = [fixed_task(utc(23, 14), utc(23, 15)), flexible_task()]
    blocks = calculate_busy_blocks([], tasks, utc(23, 0), utc(24, 0))
    assert [(b.kind, b.interval) for b in blocks] == [("TASK", Interval(utc(23, 14), utc(23, 15)))]


def test_busy_blocks_reduce_availability():
    window = SimpleNamespace(weekday=0, start_time=time(9), end_time=time(17), timezone="UTC",
                             valid_from=None, valid_until=None)
    event = weekly(date(2026, 11, 16), time(11), time(13))
    blocks = calculate_busy_blocks([event], [], utc(23, 0), utc(24, 0))
    slots = calculate_available_slots([window], [b.interval for b in blocks], utc(23, 0), utc(24, 0))
    assert slots == [Interval(utc(23, 9), utc(23, 11)), Interval(utc(23, 13), utc(23, 17))]