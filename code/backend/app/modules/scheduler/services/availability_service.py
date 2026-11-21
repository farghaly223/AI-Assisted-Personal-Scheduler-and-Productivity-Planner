"""Reads the caller's scheduling inputs and calculates available time."""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.scheduler.algorithms.availability_calculator import calculate_available_slots
from app.modules.scheduler.algorithms.busy_calculator import calculate_busy_blocks
from app.modules.scheduler.models import Event, Task
from app.modules.user.planning_models import AvailabilityWindow


def get_availability(session: Session, start: datetime, end: datetime, minimum_minutes: int) -> dict:
    user_id = session.info["user_id"]

    windows = session.scalars(
        sa.select(AvailabilityWindow).where(AvailabilityWindow.user_id == user_id)
    ).all()

    once_in_range = sa.and_(Event.recurrence_type == "ONCE", Event.start_at < end, Event.end_at > start)
    # Weekly rules are expanded in Python, so fetch every rule that began before the range ends.
    weekly_rules = sa.and_(Event.recurrence_type == "WEEKLY",
                           Event.recurrence_first_date <= (end + timedelta(days=2)).date())
    events = session.scalars(
        sa.select(Event).where(Event.user_id == user_id, sa.or_(once_in_range, weekly_rules))
    ).all()

    fixed_tasks = session.scalars(
        sa.select(Task).where(Task.user_id == user_id, Task.task_type == "FIXED",
                              Task.fixed_start_at < end, Task.fixed_end_at > start)
    ).all()

    busy = calculate_busy_blocks(events, fixed_tasks, start, end)
    slots = calculate_available_slots(windows, [block.interval for block in busy], start, end, minimum_minutes)

    return {
        "range_start": start,
        "range_end": end,
        "slots": [{"start_at": s.start, "end_at": s.end, "duration_minutes": s.minutes} for s in slots],
        "busy": [{"kind": b.kind, "source_id": b.source_id, "start_at": b.interval.start,
                  "end_at": b.interval.end, "occurrence_date": b.occurrence_date} for b in busy],
        "total_available_minutes": sum(s.minutes for s in slots),
    }