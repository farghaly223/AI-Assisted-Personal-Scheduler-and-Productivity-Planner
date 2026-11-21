"""Validation of proposals, conflict detection on the current schedule, and statistics."""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.scheduler.algorithms.availability_calculator import expand_availability
from app.modules.scheduler.algorithms.busy_calculator import calculate_busy_blocks
from app.modules.scheduler.algorithms.schedule_health import HealthIssue, Subject, check_items
from app.modules.scheduler.algorithms.time_intervals import Interval, subtract_intervals
from app.modules.scheduler.models import Event, Task
from app.modules.scheduler.planning_models import ScheduleItem, UnscheduledTask
from app.modules.scheduler.services.planning_service import _load_inputs, current_version


def _commitments(inputs, start, end) -> list[Subject]:
    titles = {e.event_id: e.title for e in inputs.events} | {t.task_id: t.title for t in inputs.fixed}
    return [Subject(None, b.kind, b.source_id, titles.get(b.source_id), b.interval.start, b.interval.end,
                    is_fixed=True, occurrence_date=b.occurrence_date)
            for b in calculate_busy_blocks(inputs.events, inputs.fixed, start, end)]


def validate_proposal(session: Session, items) -> dict:
    user_id = session.info["user_id"]
    task_ids = {item.task_id for item in items if item.task_id}
    tasks = {t.task_id: t for t in session.scalars(sa.select(Task).where(Task.user_id == user_id, Task.task_id.in_(task_ids)))}
    if len(tasks) != len(task_ids):
        raise APIError(422, "invalid_request", "A task_id does not belong to a task of this user")
    start, end = min(i.start_at for i in items), max(i.end_at for i in items)
    inputs = _load_inputs(session, user_id, start, end)

    primary, issues = [], []
    for index, item in enumerate(items):
        task = tasks.get(item.task_id)
        primary.append(Subject(index, item.kind, item.task_id, task.title if task else item.label,
                               item.start_at, item.end_at, deadline=task.deadline if task else None,
                               is_fixed=bool(task and task.task_type == "FIXED")))
        if task and task.task_type == "FIXED" and (item.start_at, item.end_at) != (task.fixed_start_at, task.fixed_end_at):
            issues.append(HealthIssue("ERROR", "FIXED_TASK_MOVED", "A fixed task must keep its original time",
                                      index, task.task_id, task.title))
    availability = ([slot for slot in expand_availability(inputs.windows, start, end)] if inputs.windows else None)
    issues += check_items(primary, _commitments(inputs, start, end), availability)
    return {"is_valid": not any(i.severity == "ERROR" for i in issues),
            "issues": [{"severity": i.severity, "code": i.code, "message": i.message,
                        "item_index": i.ref, "task_id": i.source_id} for i in issues]}


def _version_rows(session: Session, version_id):
    title = sa.func.coalesce(Task.title, Event.title, ScheduleItem.label)
    return session.execute(
        sa.select(ScheduleItem, Task.task_type, Task.deadline, title)
        .outerjoin(Task, Task.task_id == ScheduleItem.task_id).outerjoin(Event, Event.event_id == ScheduleItem.event_id)
        .where(ScheduleItem.schedule_version_id == version_id, ScheduleItem.user_id == session.info["user_id"])
        .order_by(ScheduleItem.start_at, ScheduleItem.id)).all()


def _require_current(session: Session):
    version = current_version(session, session.info["user_id"])
    if version is None:
        raise APIError(404, "not_found", "There is no current schedule")
    return version


def read_conflicts(session: Session) -> dict:
    """Is the published schedule still valid against today's events, tasks and availability?"""
    user_id = session.info["user_id"]
    version = _require_current(session)
    rows = _version_rows(session, version.id)
    inputs = _load_inputs(session, user_id, version.window_start_at, version.window_end_at)
    busy = calculate_busy_blocks(inputs.events, inputs.fixed, version.window_start_at, version.window_end_at)

    primary, stored_fixed = [], []
    for row, task_type, deadline, title in rows:
        if row.kind == "TASK" and task_type == "FLEXIBLE":
            primary.append(Subject(row.id, "TASK", row.task_id, title, row.start_at, row.end_at, deadline=deadline))
        elif row.kind in ("TASK", "EVENT"):
            stored_fixed.append((row, title))
    availability = expand_availability(inputs.windows, version.window_start_at, version.window_end_at) if inputs.windows else None
    issues = check_items(primary, _commitments(inputs, version.window_start_at, version.window_end_at), availability)

    now_signatures = {(b.kind, b.source_id, b.occurrence_date, b.interval.start, b.interval.end) for b in busy}
    stored_signatures = {(row.kind, row.task_id or row.event_id, row.event_occurrence_date, row.start_at, row.end_at)
                         for row, _ in stored_fixed}
    for row, title in stored_fixed:
        if (row.kind, row.task_id or row.event_id, row.event_occurrence_date, row.start_at, row.end_at) not in now_signatures:
            issues.append(HealthIssue("WARNING", "COMMITMENT_CHANGED",
                                      "This commitment changed or was removed after the schedule was generated",
                                      row.id, row.task_id or row.event_id, title))
    titles = {e.event_id: e.title for e in inputs.events} | {t.task_id: t.title for t in inputs.fixed}
    for block in busy:
        if (block.kind, block.source_id, block.occurrence_date, block.interval.start, block.interval.end) not in stored_signatures:
            issues.append(HealthIssue("INFO", "NEW_COMMITMENT",
                                      "A commitment was added or changed after the schedule was generated",
                                      None, block.source_id, titles.get(block.source_id)))
    return {"version_id": version.id, "version_number": version.version_number,
            "checked_at": datetime.now(timezone.utc), "has_blocking": any(i.severity == "ERROR" for i in issues),
            "issues": [{"severity": i.severity, "code": i.code, "message": i.message,
                        "item_id": i.ref, "source_id": i.source_id, "title": i.title} for i in issues]}


def read_statistics(session: Session) -> dict:
    user_id = session.info["user_id"]
    version = _require_current(session)
    rows = _version_rows(session, version.id)
    minutes = lambda row: int((row.end_at - row.start_at).total_seconds() // 60)
    flexible = [row for row, task_type, _, _ in rows if row.kind == "TASK" and task_type == "FLEXIBLE"]
    committed = [row for row, task_type, _, _ in rows if not (row.kind == "TASK" and task_type == "FLEXIBLE")]
    placed_ids = {row.task_id for row in flexible}
    omitted_ids = {task_id for task_id in session.scalars(sa.select(UnscheduledTask.task_id).where(
        UnscheduledTask.schedule_version_id == version.id, UnscheduledTask.user_id == user_id)) if task_id}
    fully_unscheduled = len(omitted_ids - placed_ids)
    total = len(placed_ids) + fully_unscheduled

    windows = _load_inputs(session, user_id, version.window_start_at, version.window_end_at).windows
    availability = expand_availability(windows, version.window_start_at, version.window_end_at)
    free = subtract_intervals(availability, [Interval(row.start_at, row.end_at) for row, *_ in rows])
    return {"version_id": version.id, "version_number": version.version_number,
            "window_start_at": version.window_start_at, "window_end_at": version.window_end_at,
            "scheduled_tasks": len(placed_ids), "unscheduled_tasks": len(omitted_ids),
            "total_scheduled_minutes": sum(minutes(r) for r in flexible),
            "committed_minutes": sum(minutes(r) for r in committed),
            "available_minutes": sum(s.minutes for s in availability), "unused_minutes": sum(s.minutes for s in free),
            "locked_items": sum(1 for row, *_ in rows if row.is_locked and row.kind == "TASK" and row in flexible),
            "completion_ratio": round(len(placed_ids) / total, 4) if total else 1.0}