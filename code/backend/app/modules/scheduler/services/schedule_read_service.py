"""Reading the current schedule version and its daily/weekly views."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.scheduler.models import Event, Task
from app.modules.scheduler.planning_models import ScheduleItem, UnscheduledTask
from app.modules.scheduler.services.planning_service import current_version
from app.modules.user.models import UserPreferences


def user_timezone(session: Session) -> str:
    return session.scalar(sa.select(UserPreferences.timezone)
                          .where(UserPreferences.user_id == session.info["user_id"])) or "UTC"


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def _item_dicts(session: Session, version_id, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    title = sa.func.coalesce(Task.title, Event.title, ScheduleItem.label)
    statement = (sa.select(ScheduleItem, title)
                 .outerjoin(Task, Task.task_id == ScheduleItem.task_id)
                 .outerjoin(Event, Event.event_id == ScheduleItem.event_id)
                 .where(ScheduleItem.schedule_version_id == version_id,
                        ScheduleItem.user_id == session.info["user_id"])
                 .order_by(ScheduleItem.start_at, ScheduleItem.id))
    if start is not None:
        statement = statement.where(ScheduleItem.end_at > start, ScheduleItem.start_at < end)
    return [{"id": row.id, "kind": row.kind, "task_id": row.task_id, "event_id": row.event_id, "title": name,
             "start_at": row.start_at, "end_at": row.end_at, "is_locked": row.is_locked,
             "event_occurrence_date": row.event_occurrence_date}
            for row, name in session.execute(statement).all()]


def current_schedule_payload(session: Session) -> dict:
    version = current_version(session, session.info["user_id"])
    if version is None:
        raise APIError(404, "not_found", "There is no current schedule")
    omitted = session.execute(
        sa.select(UnscheduledTask, Task.title).outerjoin(Task, Task.task_id == UnscheduledTask.task_id)
        .where(UnscheduledTask.schedule_version_id == version.id, UnscheduledTask.user_id == session.info["user_id"])
        .order_by(UnscheduledTask.created_at, UnscheduledTask.id)).all()
    return {
        "version": {"version_id": version.id, "version_number": version.version_number, "origin": version.origin,
                    "status": version.status, "window_start_at": version.window_start_at,
                    "window_end_at": version.window_end_at, "timezone": version.timezone,
                    "published_at": version.published_at, "explanation": version.explanation},
        "items": _item_dicts(session, version.id),
        "unscheduled": [{"task_id": row.task_id, "title": name, "reason_code": row.reason_code,
                         "explanation": row.explanation} for row, name in omitted],
    }


def day_payload(session: Session, day: date) -> dict:
    zone_name = user_timezone(session)
    zone = ZoneInfo(zone_name)
    version = current_version(session, session.info["user_id"])
    items = (_item_dicts(session, version.id, _local_midnight(day, zone), _local_midnight(day + timedelta(days=1), zone))
             if version else [])
    return {"date": day, "timezone": zone_name, "version_id": version.id if version else None, "items": items}


def week_payload(session: Session, week_start: date) -> dict:
    zone_name = user_timezone(session)
    zone = ZoneInfo(zone_name)
    version = current_version(session, session.info["user_id"])
    days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        items = (_item_dicts(session, version.id, _local_midnight(day, zone),
                             _local_midnight(day + timedelta(days=1), zone)) if version else [])
        days.append({"date": day, "timezone": zone_name, "version_id": version.id if version else None, "items": items})
    return {"week_start": week_start, "week_end": week_start + timedelta(days=6), "timezone": zone_name,
            "version_id": version.id if version else None, "days": days}