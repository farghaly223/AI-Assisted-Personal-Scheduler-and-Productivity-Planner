"""Busy time: concrete event occurrences and FIXED tasks inside a time range."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from app.modules.scheduler.algorithms.time_intervals import Interval


@dataclass(frozen=True)
class BusyBlock:
    kind: str  # "EVENT" or "TASK"
    source_id: UUID
    interval: Interval
    occurrence_date: date | None = None  # only for weekly event occurrences


def _overlaps_range(interval: Interval, range_start: datetime, range_end: datetime) -> bool:
    return interval.start < range_end and range_start < interval.end


def _local_to_utc(day: date, wall_time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, wall_time, tzinfo=zone).astimezone(timezone.utc)


def _expand_weekly(event, range_start: datetime, range_end: datetime) -> list[BusyBlock]:
    zone = ZoneInfo(event.recurrence_timezone)
    first = event.recurrence_first_date
    until = event.recurrence_until_date
    offset = event.recurrence_end_day_offset
    # Jump close to the range instead of walking from the first date.
    local_range_start = range_start.astimezone(zone).date()
    skip_weeks = max(0, ((local_range_start - first).days - offset) // 7 - 1)
    day = first + timedelta(weeks=skip_weeks)
    blocks = []
    while until is None or day <= until:
        start = _local_to_utc(day, event.recurrence_start_time, zone)
        if start >= range_end:
            break
        end = _local_to_utc(day + timedelta(days=offset), event.recurrence_end_time, zone)
        if end > start:
            interval = Interval(start, end)
            if _overlaps_range(interval, range_start, range_end):
                blocks.append(BusyBlock("EVENT", event.event_id, interval, occurrence_date=day))
        day += timedelta(days=7)
    return blocks


def expand_events(events, range_start: datetime, range_end: datetime) -> list[BusyBlock]:
    blocks = []
    for event in events:
        if event.recurrence_type == "ONCE":
            interval = Interval(event.start_at.astimezone(timezone.utc), event.end_at.astimezone(timezone.utc))
            if _overlaps_range(interval, range_start, range_end):
                blocks.append(BusyBlock("EVENT", event.event_id, interval))
        elif event.recurrence_type == "WEEKLY":
            blocks.extend(_expand_weekly(event, range_start, range_end))
    return blocks


def expand_fixed_tasks(tasks, range_start: datetime, range_end: datetime) -> list[BusyBlock]:
    blocks = []
    for task in tasks:
        if task.task_type != "FIXED":
            continue
        interval = Interval(task.fixed_start_at.astimezone(timezone.utc), task.fixed_end_at.astimezone(timezone.utc))
        if _overlaps_range(interval, range_start, range_end):
            blocks.append(BusyBlock("TASK", task.task_id, interval))
    return blocks


def calculate_busy_blocks(events, tasks, range_start: datetime, range_end: datetime) -> list[BusyBlock]:
    blocks = expand_events(events, range_start, range_end) + expand_fixed_tasks(tasks, range_start, range_end)
    return sorted(blocks, key=lambda block: block.interval.start)