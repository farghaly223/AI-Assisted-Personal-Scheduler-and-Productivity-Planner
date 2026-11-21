"""Saving schedule versions: generated plans and manual revisions (draft, then publish)."""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.scheduler.algorithms.availability_calculator import expand_availability
from app.modules.scheduler.algorithms.persistence_planner import ItemRow
from app.modules.scheduler.algorithms.schedule_validator import PlannedItem, detect_overlaps
from app.modules.scheduler.algorithms.task_allocator import UnscheduledTask as OmissionDraft
from app.modules.scheduler.models import Task
from app.modules.scheduler.planning_models import (
    Schedule, ScheduleItem, ScheduleValidationIssue, ScheduleVersion, UnscheduledTask)
from app.modules.scheduler.services.planning_service import Plan, compute_plan, current_version, plan_to_payload
from app.modules.scheduler.services.schedule_read_service import current_schedule_payload
from app.modules.user.models import UserPreferences
from app.modules.user.planning_models import AvailabilityWindow


def database_conflict(error: sa.exc.DBAPIError) -> APIError:
    """Turn a database rule violation (trigger/constraint) into a clean 409 without leaking SQL."""
    diag = getattr(error.orig, "diag", None)
    message = getattr(diag, "message_primary", None) or "The schedule violates a database rule"
    return APIError(409, "conflict", message)


def save_version(session: Session, *, parent_version_id, window_start, window_end, rows, omissions, issues,
                 origin: str, explanation: str | None, snapshot: dict) -> ScheduleVersion:
    """Insert a draft with its items/omissions/issues, then publish it."""
    user_id = session.info["user_id"]
    schedule = session.scalar(sa.select(Schedule).where(Schedule.user_id == user_id).with_for_update())
    if schedule is None:
        schedule = Schedule(user_id=user_id)
        session.add(schedule)
        session.flush()
    if schedule.current_version_id != parent_version_id:
        raise APIError(409, "conflict", "The schedule changed in the meantime; try again")

    number = (session.scalar(sa.select(sa.func.max(ScheduleVersion.version_number))
                             .where(ScheduleVersion.schedule_id == schedule.id)) or 0) + 1
    timezone_name = session.scalar(sa.select(UserPreferences.timezone).where(UserPreferences.user_id == user_id)) or "UTC"
    version = ScheduleVersion(
        user_id=user_id, schedule_id=schedule.id, version_number=number, parent_version_id=parent_version_id,
        window_start_at=window_start, window_end_at=window_end, timezone=timezone_name, origin=origin,
        explanation=explanation, input_snapshot=snapshot)
    session.add(version)
    session.flush()

    items = [ScheduleItem(user_id=user_id, schedule_version_id=version.id, logical_key=row.logical_key,
                          kind=row.kind, task_id=row.task_id, event_id=row.event_id,
                          event_occurrence_date=row.event_occurrence_date, label=row.label,
                          start_at=row.start_at, end_at=row.end_at, is_locked=row.is_locked) for row in rows]
    session.add_all(items)
    session.flush()

    for omission in omissions:
        session.add(UnscheduledTask(user_id=user_id, schedule_version_id=version.id, task_id=omission.task_id,
                                    reason_code=omission.reason_code, explanation=omission.explanation))
    for issue in issues:  # blocking ERRORs are rejected earlier; keep warnings for the record
        session.add(ScheduleValidationIssue(
            user_id=user_id, schedule_version_id=version.id, item_id=items[issue.item_index].id,
            related_item_id=items[issue.related_index].id if issue.related_index is not None else None,
            severity=issue.severity, code=issue.code, message=issue.message))
    session.flush()

    session.execute(sa.update(ScheduleVersion).where(ScheduleVersion.id == version.id).values(status="PUBLISHED"))
    session.flush()
    session.refresh(version)
    return version


def save_plan(session: Session, plan: Plan, *, origin: str = "GENERATED", explanation: str | None = None) -> ScheduleVersion:
    return save_version(
        session, parent_version_id=plan.parent_version_id, window_start=plan.window_start,
        window_end=plan.window_end, rows=plan.rows, omissions=plan.omissions, issues=plan.result.issues,
        origin=origin, explanation=explanation,
        snapshot={"algorithm": "earliest-deadline-first-v1", "range_start": plan.result.range_start.isoformat(),
                  "range_end": plan.result.range_end.isoformat(), "generated_at": plan.now.isoformat()})


def create_schedule(session: Session, start, end, *, regenerate: bool) -> dict:
    user_id = session.info["user_id"]
    existing = current_version(session, user_id)
    if regenerate and existing is None:
        raise APIError(409, "conflict", "There is no current schedule to regenerate; use POST /scheduler/generate")
    if not regenerate and existing is not None:
        raise APIError(409, "conflict", "A schedule already exists; use POST /scheduler/regenerate")
    plan = compute_plan(session, start, end)
    blocking = [issue for issue in plan.result.issues if issue.severity == "ERROR"]
    if blocking:
        raise APIError(409, "conflict",
                       f"The schedule has {len(blocking)} blocking conflict(s); inspect them with POST /scheduler/generate/preview")
    try:
        version = save_plan(session, plan)
    except sa.exc.DBAPIError as error:
        raise database_conflict(error) from None
    return plan_to_payload(plan) | {"version_id": version.id, "version_number": version.version_number,
                                    "status": version.status, "published_at": version.published_at}


def _inside_availability(session: Session, user_id, start: datetime, end: datetime) -> bool:
    windows = session.scalars(sa.select(AvailabilityWindow).where(AvailabilityWindow.user_id == user_id)).all()
    if not windows:
        return True  # the database only enforces availability when windows exist
    return any(slot.start <= start and end <= slot.end for slot in expand_availability(windows, start, end))


def revise_current_version(session: Session, item_id, *, explanation: str, new_start: datetime | None = None,
                           locked: bool | None = None) -> dict:
    """Manual change: copy the current version with one flexible placement moved/locked/unlocked."""
    user_id = session.info["user_id"]
    parent = current_version(session, user_id)
    if parent is None:
        raise APIError(404, "not_found", "There is no current schedule")
    rows = session.scalars(sa.select(ScheduleItem).where(
        ScheduleItem.schedule_version_id == parent.id, ScheduleItem.user_id == user_id
    ).order_by(ScheduleItem.start_at, ScheduleItem.id)).all()
    target = next((row for row in rows if row.id == item_id), None)
    if target is None:
        raise APIError(404, "not_found", "Resource was not found")
    task = (session.scalar(sa.select(Task).where(Task.task_id == target.task_id, Task.user_id == user_id))
            if target.task_id else None)
    if target.kind != "TASK" or task is None or task.task_type != "FLEXIBLE":
        raise APIError(422, "invalid_request", "Only flexible task placements can be moved, locked or unlocked")

    start, end, is_locked = target.start_at, target.end_at, target.is_locked
    if new_start is not None:
        if target.is_locked:
            raise APIError(422, "invalid_request", "Unlock the item before moving it")
        if new_start < datetime.now(timezone.utc):
            raise APIError(422, "invalid_request", "An item cannot be moved into the past")
        start, end = new_start, new_start + (target.end_at - target.start_at)
        if task.deadline is not None and end > task.deadline:
            raise APIError(409, "conflict", "The new time passes the task's deadline")
        if not _inside_availability(session, user_id, start, end):
            raise APIError(409, "conflict", "The new time is outside the configured availability")
    if locked is not None:
        if locked == target.is_locked:
            raise APIError(409, "conflict", "The item is already " + ("locked" if locked else "unlocked"))
        is_locked = locked

    new_rows = []
    for row in rows:
        if (row.kind == "TASK" and row.task_id is None) or (row.kind == "EVENT" and row.event_id is None):
            continue  # its source was deleted; history keeps the old version
        if row.id == target.id:
            new_rows.append(ItemRow(row.kind, row.task_id, row.event_id, row.event_occurrence_date,
                                    start, end, is_locked, row.logical_key, row.label))
        else:
            new_rows.append(ItemRow(row.kind, row.task_id, row.event_id, row.event_occurrence_date,
                                    row.start_at, row.end_at, row.is_locked, row.logical_key, row.label))
    if detect_overlaps([PlannedItem(r.kind, r.task_id or r.event_id, r.start_at, r.end_at) for r in new_rows]):
        raise APIError(409, "conflict", "The new time overlaps another item")

    omissions = [OmissionDraft(u.task_id, u.reason_code, u.explanation or "") for u in session.scalars(
        sa.select(UnscheduledTask).where(UnscheduledTask.schedule_version_id == parent.id,
                                         UnscheduledTask.user_id == user_id)) if u.task_id is not None]
    try:
        save_version(session, parent_version_id=parent.id,
                     window_start=min(parent.window_start_at, start), window_end=max(parent.window_end_at, end),
                     rows=new_rows, omissions=omissions, issues=[], origin="MANUAL", explanation=explanation,
                     snapshot={"algorithm": "manual-edit-v1", "source_version_id": str(parent.id),
                               "item_logical_key": str(target.logical_key)})
    except sa.exc.DBAPIError as error:
        raise database_conflict(error) from None
    return current_schedule_payload(session)