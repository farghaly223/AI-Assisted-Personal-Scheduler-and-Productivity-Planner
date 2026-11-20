"""User-owned hard availability, separate from preferred working hours."""

from datetime import date, datetime, time
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIME, TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, owner_fk


class AvailabilityWindow(Base):
    __tablename__ = "availability_windows"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="pk_availability_windows"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_availability_weekday"),
        sa.CheckConstraint("start_time < TIME '24:00' AND end_time < TIME '24:00' AND start_time <> end_time", name="ck_availability_times"),
        sa.CheckConstraint("(valid_from IS NULL OR isfinite(valid_from)) AND (valid_until IS NULL OR isfinite(valid_until)) AND (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from)", name="ck_availability_dates"),
        sa.CheckConstraint("timezone = btrim(timezone) AND timezone ~ '[^[:space:]]'", name="ck_availability_timezone"),
        sa.Index("ix_availability_user_day", "user_id", "weekday", "start_time", "id"),
        {"info": {"owner_module": "user", "purpose": "Weekly local hard availability windows; overnight intervals wrap to next day.", "introduced_in": "0003"}},
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), owner_fk("fk_availability_profile"))
    weekday: Mapped[int] = mapped_column(sa.SmallInteger)
    start_time: Mapped[time] = mapped_column(TIME(precision=0))
    end_time: Mapped[time] = mapped_column(TIME(precision=0))
    timezone: Mapped[str] = mapped_column(sa.String(100))
    valid_from: Mapped[date | None] = mapped_column(sa.Date)
    valid_until: Mapped[date | None] = mapped_column(sa.Date)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=sa.text("now()"), server_onupdate=sa.FetchedValue())
