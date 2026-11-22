"""Read-only adapter for scheduler tables.

This module is the ONLY file in the energy module allowed to import from
`app.modules.scheduler` (Task and Event models only).
It executes only SELECT queries, always filtered by verified owner (user_id),
with a bounded, constant number of queries.

Sprint 1 Status & Known Limitations:
- Task status is always UNSCHEDULED in this sprint; completed is returned as None
  for all tasks, representing unknown completion status.
- Known limitation: A FIXED task and a calendar event covering the same time
  can both be counted in the total daily workload cognitive demand.
- Titles, locations, and personal text are never read into energy objects.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.energy.algorithms.energy.workload_calculator import TaskLoad
from app.modules.scheduler.models import Event, Task


class TaskLoads(list[TaskLoad]):
    """List of TaskLoad objects with a truncation flag."""

    def __init__(self, items: list[TaskLoad] | None = None, *, truncated: bool = False):
        super().__init__(items or [])
        self.truncated = truncated


class EventIntervals(list[tuple[datetime, datetime]]):
    """List of (start, end) datetime intervals with a truncation flag."""

    def __init__(
        self, items: list[tuple[datetime, datetime]] | None = None, *, truncated: bool = False
    ):
        super().__init__(items or [])
        self.truncated = truncated


def load_task_loads(
    session: Session,
    user_id: UUID,
    day_start: datetime,
    day_end: datetime,
    *,
    limit: int = 500,
) -> TaskLoads:
    """Load user's task loads overlapping the local day window.

    - FIXED tasks: counted when [fixed_start_at, fixed_end_at) overlaps [day_start, day_end).
      Effective duration is min(estimated_duration_minutes, minutes of overlap with local day).
    - FLEXIBLE tasks: counted when deadline falls inside [day_start, day_end).
      FLEXIBLE tasks with no deadline are NOT counted.
    - Rows read capped at limit + 1; if exceeded, truncated is set to True.
    """
    stmt = (
        sa.select(
            Task.task_type,
            Task.estimated_duration_minutes,
            Task.priority,
            Task.fixed_start_at,
            Task.fixed_end_at,
            Task.deadline,
        )
        .where(
            Task.user_id == user_id,
            sa.or_(
                sa.and_(
                    Task.task_type == "FIXED",
                    Task.fixed_start_at < day_end,
                    Task.fixed_end_at > day_start,
                ),
                sa.and_(
                    Task.task_type == "FLEXIBLE",
                    Task.deadline.is_not(None),
                    Task.deadline >= day_start,
                    Task.deadline < day_end,
                ),
            ),
        )
        .limit(limit + 1)
    )

    rows = session.execute(stmt).all()
    is_truncated = len(rows) > limit
    candidates = rows[:limit]

    items: list[TaskLoad] = []
    for task_type, est_dur, priority, fixed_start, fixed_end, deadline in candidates:
        if task_type == "FIXED":
            if fixed_start is not None and fixed_end is not None:
                overlap_start = max(fixed_start, day_start)
                overlap_end = min(fixed_end, day_end)
                overlap_seconds = max(0.0, (overlap_end - overlap_start).total_seconds())
                overlap_minutes = int(round(overlap_seconds / 60.0))
                eff_duration = max(1, min(est_dur, overlap_minutes))
                items.append(
                    TaskLoad(
                        duration_minutes=eff_duration,
                        priority=priority,
                        completed=None,
                    )
                )
        elif task_type == "FLEXIBLE":
            if deadline is not None and day_start <= deadline < day_end:
                items.append(
                    TaskLoad(
                        duration_minutes=est_dur,
                        priority=priority,
                        completed=None,
                    )
                )

    return TaskLoads(items, truncated=is_truncated)


def load_event_intervals(
    session: Session,
    user_id: UUID,
    day_start: datetime,
    day_end: datetime,
    *,
    target_date: date | None = None,
    limit: int = 500,
) -> EventIntervals:
    """Load user's event intervals overlapping the local day window.

    - ONCE events: overlapping [day_start, day_end).
    - WEEKLY events: SQL prefilter by recurrence_first_date and recurrence_until_date,
      then expand in Python in the event's recurrence_timezone using fold=0 for DST
      transitions (nonexistent times shift forward, ambiguous times pick first occurrence).
    - Rows read capped at limit + 1 per category; if exceeded, truncated is set to True.
    """
    if target_date is None:
        target_date = day_start.date()

    cand_max_date = target_date + timedelta(days=2)
    cand_min_date = target_date - timedelta(days=2)

    # 1. Query ONCE events
    stmt_once = (
        sa.select(Event.start_at, Event.end_at)
        .where(
            Event.user_id == user_id,
            Event.recurrence_type == "ONCE",
            Event.start_at < day_end,
            Event.end_at > day_start,
        )
        .limit(limit + 1)
    )
    once_rows = session.execute(stmt_once).all()
    once_truncated = len(once_rows) > limit
    once_candidates = once_rows[:limit]

    intervals: list[tuple[datetime, datetime]] = []
    for s_at, e_at in once_candidates:
        if s_at is not None and e_at is not None and e_at > s_at:
            intervals.append((s_at, e_at))

    # 2. Query WEEKLY events with SQL prefilter
    stmt_weekly = (
        sa.select(
            Event.recurrence_first_date,
            Event.recurrence_until_date,
            Event.recurrence_start_time,
            Event.recurrence_end_time,
            Event.recurrence_end_day_offset,
            Event.recurrence_timezone,
        )
        .where(
            Event.user_id == user_id,
            Event.recurrence_type == "WEEKLY",
            Event.recurrence_first_date <= cand_max_date,
            sa.or_(
                Event.recurrence_until_date.is_(None),
                (Event.recurrence_until_date + Event.recurrence_end_day_offset) >= cand_min_date,
            ),
        )
        .limit(limit + 1)
    )
    weekly_rows = session.execute(stmt_weekly).all()
    weekly_truncated = len(weekly_rows) > limit
    weekly_candidates = weekly_rows[:limit]

    is_truncated = once_truncated or weekly_truncated

    # 3. Expand WEEKLY occurrences in event's timezone
    for (
        first_date,
        until_date,
        start_time,
        end_time,
        end_day_offset,
        zone_name,
    ) in weekly_candidates:
        if (
            first_date is None
            or start_time is None
            or end_time is None
            or end_day_offset is None
            or zone_name is None
        ):
            continue

        try:
            ev_tz = ZoneInfo(zone_name)
        except Exception:
            continue

        target_weekday = first_date.weekday()
        day_start_ev = day_start.astimezone(ev_tz)
        day_end_ev = day_end.astimezone(ev_tz)

        # Range of local start dates in ev_tz that could produce an occurrence
        # whose interval [start, end) overlaps [day_start, day_end)
        earliest_start_date = day_start_ev.date() - timedelta(days=end_day_offset)
        latest_start_date = day_end_ev.date()

        current_d = earliest_start_date
        while current_d <= latest_start_date:
            if current_d.weekday() == target_weekday and current_d >= first_date:
                if until_date is None or current_d <= until_date:
                    start_naive = datetime.combine(current_d, start_time)
                    end_naive = datetime.combine(
                        current_d + timedelta(days=end_day_offset), end_time
                    )

                    # Deterministic DST rule: fold=0
                    # Nonexistent local times shift forward, ambiguous local times use the first occurrence
                    start_utc = start_naive.replace(tzinfo=ev_tz, fold=0).astimezone(timezone.utc)
                    end_utc = end_naive.replace(tzinfo=ev_tz, fold=0).astimezone(timezone.utc)

                    if end_utc > start_utc and start_utc < day_end and end_utc > day_start:
                        intervals.append((start_utc, end_utc))

            current_d += timedelta(days=1)

    return EventIntervals(intervals, truncated=is_truncated)
