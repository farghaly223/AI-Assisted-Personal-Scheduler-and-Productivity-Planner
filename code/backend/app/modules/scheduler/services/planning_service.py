"""Shared planning pipeline: read the caller's data, run the pure algorithms, prepare database rows."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.scheduler.algorithms.persistence_planner import (
    ItemRow, StoredItem, partial_allocation_omissions, select_carried_items, to_item_rows, widen_window)
from app.modules.scheduler.algorithms.schedule_builder import ScheduleResult, build_schedule
from app.modules.scheduler.algorithms.task_allocator import UnscheduledTask
from app.modules.scheduler.models import Event, Task
from app.modules.scheduler.planning_models import (
    Schedule, ScheduleItem as ScheduleItemRow, ScheduleVersion, TaskOutcome, TaskRequirement)
from app.modules.user.planning_models import AvailabilityWindow

MAX_FLEXIBLE_TASKS = 1000
MAX_WINDOW_PASSES = 4


@dataclass
class Plan:
    result: ScheduleResult
    window_start: datetime
    window_end: datetime
    parent_version_id: UUID | None
    rows: list[ItemRow]
    omissions: list[UnscheduledTask]
    now: datetime


def current_version(session: Session, user_id) -> ScheduleVersion | None:
    return session.scalar(
        sa.select(ScheduleVersion).join(Schedule, Schedule.current_version_id == ScheduleVersion.id)
        .where(Schedule.user_id == user_id))


def _latest_outcomes(session: Session, user_id) -> dict:
    rows = session.execute(
        sa.select(TaskOutcome.task_id, TaskOutcome.outcome)
        .where(TaskOutcome.user_id == user_id, TaskOutcome.task_id.is_not(None))
        .distinct(TaskOutcome.task_id)
        .order_by(TaskOutcome.task_id, TaskOutcome.occurred_at.desc(), TaskOutcome.id.desc())
    ).all()
    return {task_id: outcome for task_id, outcome in rows}


def _load_inputs(session: Session, user_id, window_start: datetime, window_end: datetime) -> SimpleNamespace:
    windows = session.scalars(sa.select(AvailabilityWindow).where(AvailabilityWindow.user_id == user_id)).all()
    once = sa.and_(Event.recurrence_type == "ONCE", Event.start_at < window_end, Event.end_at > window_start)
    weekly = sa.and_(Event.recurrence_type == "WEEKLY",
                     Event.recurrence_first_date <= (window_end + timedelta(days=2)).date())
    events = session.scalars(sa.select(Event).where(Event.user_id == user_id, sa.or_(once, weekly))).all()
    flexible = session.scalars(
        sa.select(Task).where(Task.user_id == user_id, Task.task_type == "FLEXIBLE")
        .order_by(Task.deadline.asc().nulls_last(), Task.task_id).limit(MAX_FLEXIBLE_TASKS)).all()
    fixed = session.scalars(
        sa.select(Task).where(Task.user_id == user_id, Task.task_type == "FIXED",
                              Task.fixed_start_at < window_end, Task.fixed_end_at > window_start)).all()
    requirements = {r.task_id: r for r in session.scalars(
        sa.select(TaskRequirement).where(TaskRequirement.user_id == user_id,
                                         TaskRequirement.task_id.in_([t.task_id for t in flexible])))}
    return SimpleNamespace(windows=windows, events=events, flexible=flexible, fixed=fixed, requirements=requirements)


def _parent_items(session: Session, version_id, window_start, window_end) -> list[StoredItem]:
    if version_id is None:
        return []
    rows = session.scalars(sa.select(ScheduleItemRow).where(
        ScheduleItemRow.schedule_version_id == version_id,
        ScheduleItemRow.end_at > window_start, ScheduleItemRow.start_at < window_end)).all()
    return [StoredItem(r.kind, r.task_id, r.event_id, r.event_occurrence_date, r.start_at, r.end_at,
                       r.is_locked, r.logical_key) for r in rows]


def compute_plan(session: Session, start: datetime, end: datetime, now: datetime | None = None) -> Plan:
    user_id = session.info["user_id"]
    now = now or datetime.now(timezone.utc)
    parent = current_version(session, user_id)
    parent_id = parent.id if parent else None
    outcomes = _latest_outcomes(session, user_id)
    completed = {task_id for task_id, outcome in outcomes.items() if outcome == "COMPLETED"}
    excluded = {task_id for task_id, outcome in outcomes.items() if outcome in ("COMPLETED", "SKIPPED")}

    window = (start, end)
    for _ in range(MAX_WINDOW_PASSES):
        inputs = _load_inputs(session, user_id, *window)
        parent_items = _parent_items(session, parent_id, *window)
        carried = select_carried_items(parent_items, {t.task_id: t for t in inputs.flexible}, completed, *window)
        result = build_schedule(windows=inputs.windows, events=inputs.events, tasks=inputs.flexible + inputs.fixed,
                                range_start=start, range_end=end, now=now, requirements=inputs.requirements,
                                excluded_task_ids=excluded, busy_range=window, carried_items=carried)
        widened = widen_window(window[0], window[1], result.items)
        if widened == window:
            break
        window = widened
    else:
        raise APIError(409, "conflict", "The planning window could not be stabilised; shorten the range")

    estimates = {t.task_id: t.estimated_duration_minutes for t in inputs.flexible}
    omissions = list(result.unscheduled) + partial_allocation_omissions(
        result.items, estimates, {u.task_id for u in result.unscheduled})
    return Plan(result, window[0], window[1], parent_id, to_item_rows(result.items, parent_items), omissions, now)


def plan_to_payload(plan: Plan) -> dict:
    result = plan.result
    return {
        "range_start": result.range_start, "range_end": result.range_end, "generated_at": plan.now,
        "is_publishable": result.is_publishable,
        "items": [{"kind": i.kind, "source_id": i.source_id, "title": i.title, "start_at": i.start,
                   "end_at": i.end, "is_fixed": i.is_fixed, "session_number": i.session_number}
                  for i in result.items],
        "unscheduled": [{"task_id": u.task_id, "reason_code": u.reason_code, "explanation": u.explanation}
                        for u in plan.omissions],
        "issues": [{"severity": x.severity, "code": x.code, "message": x.message,
                    "item_index": x.item_index, "related_index": x.related_index} for x in result.issues],
        "statistics": asdict(result.statistics),
    }