"""Sleep schedule persistence and business logic; ownership filtered and concurrency safe."""

from uuid import UUID
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.modules.energy.models import SleepSchedule
from app.modules.energy.schemas import SleepScheduleCreate


def get_sleep_schedule(session: Session, user_id: UUID) -> SleepSchedule:
    row = session.scalar(
        sa.select(SleepSchedule).where(SleepSchedule.user_id == user_id)
    )
    if row is None:
        raise APIError(404, "not_found", "Resource was not found")
    return row


def upsert_sleep_schedule(session: Session, user_id: UUID, payload: SleepScheduleCreate) -> SleepSchedule:
    data = payload.model_dump()
    stmt = (
        insert(SleepSchedule)
        .values(
            user_id=user_id,
            bedtime=data["bedtime"],
            wake_time=data["wake_time"],
            timezone=data["timezone"],
            repeat_days=data["repeat_days"],
            bedtime_reminder_enabled=data["bedtime_reminder_enabled"],
            reminder_minutes_before=data["reminder_minutes_before"],
            wake_alarm_enabled=data["wake_alarm_enabled"],
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_sleep_schedules_user_id",
            set_={
                "bedtime": data["bedtime"],
                "wake_time": data["wake_time"],
                "timezone": data["timezone"],
                "repeat_days": data["repeat_days"],
                "bedtime_reminder_enabled": data["bedtime_reminder_enabled"],
                "reminder_minutes_before": data["reminder_minutes_before"],
                "wake_alarm_enabled": data["wake_alarm_enabled"],
                "is_active": True,
            },
        )
        .returning(SleepSchedule.id)
    )
    row_id = session.scalar(stmt)
    session.flush()
    row = session.scalar(sa.select(SleepSchedule).where(SleepSchedule.id == row_id))
    session.refresh(row)
    return row


def toggle_sleep_schedule(session: Session, user_id: UUID, is_active: bool) -> SleepSchedule:
    row = get_sleep_schedule(session, user_id)
    row.is_active = is_active
    session.flush()
    session.refresh(row)
    return row


def disable_sleep_schedule(session: Session, user_id: UUID) -> None:
    row = get_sleep_schedule(session, user_id)
    row.is_active = False
    session.flush()
