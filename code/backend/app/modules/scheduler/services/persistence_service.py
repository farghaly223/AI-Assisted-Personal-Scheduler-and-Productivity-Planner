"""Saving a computed plan as a new schedule version (draft, then publish)."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.scheduler.planning_models import (
    Schedule, ScheduleItem, ScheduleValidationIssue, ScheduleVersion, UnscheduledTask)
from app.modules.scheduler.services.planning_service import Plan, compute_plan, current_version, plan_to_payload
from app.modules.user.models import UserPreferences


def database_conflict(error: sa.exc.DBAPIError) -> APIError:
    """Turn a database rule violation (trigger/constraint) into a clean 409 without leaking SQL."""
    diag = getattr(error.orig, "diag", None)
    message = getattr(diag, "message_primary", None) or "The schedule violates a database rule"
    return APIError(409, "conflict", message)


def save_plan(session: Session, plan: Plan, *, origin: str = "GENERATED", explanation: str | None = None) -> ScheduleVersion:
    user_id = session.info["user_id"]
    schedule = session.scalar(sa.select(Schedule).where(Schedule.user_id == user_id).with_for_update())
    if schedule is None:
        schedule = Schedule(user_id=user_id)
        session.add(schedule)
        session.flush()
    if schedule.current_version_id != plan.parent_version_id:
        raise APIError(409, "conflict", "The schedule changed while it was being generated; try again")

    number = (session.scalar(sa.select(sa.func.max(ScheduleVersion.version_number))
                             .where(ScheduleVersion.schedule_id == schedule.id)) or 0) + 1
    timezone_name = session.scalar(sa.select(UserPreferences.timezone).where(UserPreferences.user_id == user_id)) or "UTC"
    version = ScheduleVersion(
        user_id=user_id, schedule_id=schedule.id, version_number=number, parent_version_id=plan.parent_version_id,
        window_start_at=plan.window_start, window_end_at=plan.window_end, timezone=timezone_name, origin=origin,
        explanation=explanation,
        input_snapshot={"algorithm": "earliest-deadline-first-v1",
                        "range_start": plan.result.range_start.isoformat(),
                        "range_end": plan.result.range_end.isoformat(),
                        "generated_at": plan.now.isoformat()})
    session.add(version)
    session.flush()

    items = [ScheduleItem(user_id=user_id, schedule_version_id=version.id, logical_key=row.logical_key,
                          kind=row.kind, task_id=row.task_id, event_id=row.event_id,
                          event_occurrence_date=row.event_occurrence_date, start_at=row.start_at,
                          end_at=row.end_at, is_locked=row.is_locked) for row in plan.rows]
    session.add_all(items)
    session.flush()

    for omission in plan.omissions:
        session.add(UnscheduledTask(user_id=user_id, schedule_version_id=version.id, task_id=omission.task_id,
                                    reason_code=omission.reason_code, explanation=omission.explanation))
    for issue in plan.result.issues:  # blocking ERRORs were rejected earlier; keep warnings for the record
        session.add(ScheduleValidationIssue(
            user_id=user_id, schedule_version_id=version.id, item_id=items[issue.item_index].id,
            related_item_id=items[issue.related_index].id if issue.related_index is not None else None,
            severity=issue.severity, code=issue.code, message=issue.message))
    session.flush()

    session.execute(sa.update(ScheduleVersion).where(ScheduleVersion.id == version.id).values(status="PUBLISHED"))
    session.flush()
    session.refresh(version)
    return version


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