"""Preview generation: read the caller's inputs, run the pure algorithms, return the result. Saves nothing."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.scheduler.algorithms.schedule_builder import build_schedule
from app.modules.scheduler.models import Event, Task
from app.modules.scheduler.planning_models import TaskOutcome, TaskRequirement
from app.modules.user.planning_models import AvailabilityWindow

MAX_FLEXIBLE_TASKS = 1000


def _closed_task_ids(session: Session, user_id) -> set:
    """Tasks whose latest outcome is COMPLETED or SKIPPED (REOPENED makes a task schedulable again)."""
    latest = session.execute(
        sa.select(TaskOutcome.task_id, TaskOutcome.outcome)
        .where(TaskOutcome.user_id == user_id, TaskOutcome.task_id.is_not(None))
        .distinct(TaskOutcome.task_id)
        .order_by(TaskOutcome.task_id, TaskOutcome.occurred_at.desc(), TaskOutcome.id.desc())
    ).all()
    return {task_id for task_id, outcome in latest if outcome in ("COMPLETED", "SKIPPED")}


def generate_preview(session: Session, start: datetime, end: datetime) -> dict:
    user_id = session.info["user_id"]
    now = datetime.now(timezone.utc)

    windows = session.scalars(sa.select(AvailabilityWindow).where(AvailabilityWindow.user_id == user_id)).all()

    once_in_range = sa.and_(Event.recurrence_type == "ONCE", Event.start_at < end, Event.end_at > start)
    weekly_rules = sa.and_(Event.recurrence_type == "WEEKLY",
                           Event.recurrence_first_date <= (end + timedelta(days=2)).date())
    events = session.scalars(sa.select(Event).where(Event.user_id == user_id, sa.or_(once_in_range, weekly_rules))).all()

    flexible = session.scalars(
        sa.select(Task).where(Task.user_id == user_id, Task.task_type == "FLEXIBLE")
        .order_by(Task.deadline.asc().nulls_last(), Task.task_id).limit(MAX_FLEXIBLE_TASKS)).all()
    fixed = session.scalars(
        sa.select(Task).where(Task.user_id == user_id, Task.task_type == "FIXED",
                              Task.fixed_start_at < end, Task.fixed_end_at > start)).all()

    requirements = {r.task_id: r for r in session.scalars(
        sa.select(TaskRequirement).where(TaskRequirement.user_id == user_id,
                                         TaskRequirement.task_id.in_([t.task_id for t in flexible])))}

    result = build_schedule(windows=windows, events=events, tasks=flexible + fixed, range_start=start,
                            range_end=end, now=now, requirements=requirements,
                            excluded_task_ids=_closed_task_ids(session, user_id))

    return {
        "range_start": start, "range_end": end, "generated_at": now,
        "is_publishable": result.is_publishable,
        "items": [{"kind": i.kind, "source_id": i.source_id, "title": i.title, "start_at": i.start,
                   "end_at": i.end, "is_fixed": i.is_fixed, "session_number": i.session_number}
                  for i in result.items],
        "unscheduled": [{"task_id": u.task_id, "reason_code": u.reason_code, "explanation": u.explanation}
                        for u in result.unscheduled],
        "issues": [{"severity": x.severity, "code": x.code, "message": x.message,
                    "item_index": x.item_index, "related_index": x.related_index} for x in result.issues],
        "statistics": asdict(result.statistics),
    }