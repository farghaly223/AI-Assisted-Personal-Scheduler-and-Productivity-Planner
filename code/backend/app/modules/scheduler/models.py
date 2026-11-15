"""Task and Event tables using the shared Base and profile ownership."""

from datetime import date, datetime, time
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIME, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, owner_fk
from app.modules.user.models import Profile


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        sa.PrimaryKeyConstraint("task_id", name="pk_tasks"),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_tasks_title_nonblank"),
        sa.CheckConstraint("task_type IN ('FIXED', 'FLEXIBLE')", name="ck_tasks_type"),
        sa.CheckConstraint("deadline IS NULL OR isfinite(deadline)", name="ck_tasks_deadline_finite"),
        sa.CheckConstraint("estimated_duration_minutes > 0", name="ck_tasks_duration_positive"),
        sa.CheckConstraint("category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')", name="ck_tasks_category"),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="ck_tasks_priority_range"),
        sa.CheckConstraint("""
            (task_type = 'FLEXIBLE' AND fixed_start_at IS NULL AND fixed_end_at IS NULL)
            OR (task_type = 'FIXED' AND fixed_start_at IS NOT NULL AND fixed_end_at IS NOT NULL
                AND isfinite(fixed_start_at) AND isfinite(fixed_end_at)
                AND fixed_end_at > fixed_start_at)
        """, name="ck_tasks_fixed_interval"),
        sa.CheckConstraint("status = 'UNSCHEDULED'", name="ck_tasks_sprint1_status"),
        sa.Index("ix_tasks_user_deadline", "user_id", "deadline"),
    )
    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_tasks_profile"))
    title: Mapped[str] = mapped_column(sa.String(500))
    task_type: Mapped[str] = mapped_column(sa.String(8))
    deadline: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    estimated_duration_minutes: Mapped[int] = mapped_column(sa.Integer)
    category: Mapped[str] = mapped_column(sa.String(20))
    priority: Mapped[int] = mapped_column(sa.Integer)
    fixed_start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    fixed_end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(sa.String(20), server_default=sa.text("'UNSCHEDULED'"))
    context: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())
    profile: Mapped[Profile] = relationship(back_populates="tasks")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        sa.PrimaryKeyConstraint("event_id", name="pk_events"),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_events_title_nonblank"),
        sa.CheckConstraint("category IN ('STUDY', 'WORK', 'PERSONAL', 'HEALTH', 'MEETING')", name="ck_events_category"),
        sa.CheckConstraint("recurrence_type IN ('ONCE', 'WEEKLY')", name="ck_events_recurrence_type"),
        sa.CheckConstraint("""
            (recurrence_type = 'ONCE'
             AND start_at IS NOT NULL AND end_at IS NOT NULL
             AND isfinite(start_at) AND isfinite(end_at) AND end_at > start_at
             AND recurrence_first_date IS NULL AND recurrence_until_date IS NULL
             AND recurrence_start_time IS NULL AND recurrence_end_time IS NULL
             AND recurrence_end_day_offset IS NULL AND recurrence_timezone IS NULL)
            OR (recurrence_type = 'WEEKLY' AND start_at IS NULL AND end_at IS NULL
             AND recurrence_first_date IS NOT NULL AND isfinite(recurrence_first_date)
             AND (recurrence_until_date IS NULL OR
                  (isfinite(recurrence_until_date) AND recurrence_until_date >= recurrence_first_date))
             AND recurrence_start_time IS NOT NULL AND recurrence_end_time IS NOT NULL
             AND recurrence_start_time < TIME '24:00:00' AND recurrence_end_time < TIME '24:00:00'
             AND recurrence_end_day_offset IS NOT NULL AND recurrence_end_day_offset >= 0
             AND (recurrence_end_day_offset > 0 OR recurrence_end_time > recurrence_start_time)
             AND recurrence_timezone IS NOT NULL AND recurrence_timezone = btrim(recurrence_timezone)
             AND recurrence_timezone ~ '[^[:space:]]')
        """, name="ck_events_time_shape"),
        sa.CheckConstraint("source = 'MANUAL'", name="ck_events_sprint1_source"),
        sa.CheckConstraint("status = 'SAVED'", name="ck_events_sprint1_status"),
        sa.Index("ix_events_user_id", "user_id"),
        sa.Index("ix_events_once_user_start", "user_id", "start_at", postgresql_where=sa.text("recurrence_type = 'ONCE'")),
        sa.Index("ix_events_weekly_user_first", "user_id", "recurrence_first_date", postgresql_where=sa.text("recurrence_type = 'WEEKLY'")),
    )
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_events_profile"))
    title: Mapped[str] = mapped_column(sa.String(500))
    location: Mapped[str | None] = mapped_column(sa.String(500))
    category: Mapped[str] = mapped_column(sa.String(20))
    recurrence_type: Mapped[str] = mapped_column(sa.String(6), server_default=sa.text("'ONCE'"))
    start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    recurrence_first_date: Mapped[date | None] = mapped_column(sa.Date)
    recurrence_until_date: Mapped[date | None] = mapped_column(sa.Date)
    recurrence_start_time: Mapped[time | None] = mapped_column(TIME(precision=0, timezone=False))
    recurrence_end_time: Mapped[time | None] = mapped_column(TIME(precision=0, timezone=False))
    recurrence_end_day_offset: Mapped[int | None] = mapped_column(sa.SmallInteger)
    recurrence_timezone: Mapped[str | None] = mapped_column(sa.String(100))
    source: Mapped[str] = mapped_column(sa.String(20), server_default=sa.text("'MANUAL'"))
    status: Mapped[str] = mapped_column(sa.String(20), server_default=sa.text("'SAVED'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())
    profile: Mapped[Profile] = relationship(back_populates="events")
