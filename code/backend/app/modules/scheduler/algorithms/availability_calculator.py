"""Availability calculator: weekly availability windows minus busy intervals."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.modules.scheduler.algorithms.time_intervals import Interval, merge_intervals, subtract_intervals

# Assumption to confirm with the database owner:
# True  -> availability_windows.weekday 0 = Monday (like Python's weekday())
# False -> 0 = Sunday
MONDAY_IS_ZERO = True


def _python_weekday(db_weekday: int) -> int:
    """Convert the stored weekday (0-6) to Python's weekday() (Monday = 0)."""
    return db_weekday if MONDAY_IS_ZERO else (db_weekday - 1) % 7


def _valid_on(window, day) -> bool:
    if window.valid_from is not None and day < window.valid_from:
        return False
    if window.valid_until is not None and day > window.valid_until:
        return False
    return True


def expand_availability(windows, range_start: datetime, range_end: datetime) -> list[Interval]:
    """Turn weekly availability rules into concrete intervals inside the range."""
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise ValueError("Range endpoints must be timezone-aware")
    found = []
    for window in windows:
        zone = ZoneInfo(window.timezone)
        # Start one day early so an overnight window from the day before is not lost.
        day = range_start.astimezone(zone).date() - timedelta(days=1)
        last_day = range_end.astimezone(zone).date()
        while day <= last_day:
            if day.weekday() == _python_weekday(window.weekday) and _valid_on(window, day):
                overnight = window.end_time < window.start_time
                end_day = day + timedelta(days=1) if overnight else day
                start = datetime.combine(day, window.start_time, tzinfo=zone).astimezone(timezone.utc)
                end = datetime.combine(end_day, window.end_time, tzinfo=zone).astimezone(timezone.utc)
                clipped_start = max(start, range_start)
                clipped_end = min(end, range_end)
                if clipped_start < clipped_end:
                    found.append(Interval(clipped_start, clipped_end))
            day += timedelta(days=1)
    return merge_intervals(found)


def calculate_available_slots(windows, busy: list[Interval], range_start: datetime,
                              range_end: datetime, minimum_minutes: int = 1) -> list[Interval]:
    """Available time = availability windows minus busy intervals."""
    free = expand_availability(windows, range_start, range_end)
    available = subtract_intervals(free, busy)
    return [slot for slot in available if slot.minutes >= minimum_minutes]