"""Contracts for the user's weekly hard availability windows (0 = Monday ... 6 = Sunday)."""

from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import Contract, Patch, WallTime, ZoneName

Weekday = Annotated[int, Field(strict=True, ge=0, le=6)]


class WindowCreate(Contract):
    weekday: Weekday
    start_time: WallTime
    end_time: WallTime  # earlier than start_time means the window continues past midnight
    timezone: ZoneName
    valid_from: date | None = None
    valid_until: date | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.start_time == self.end_time:
            raise ValueError("start_time and end_time must differ")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self


class WindowUpdate(Patch):
    required_values = frozenset({"weekday", "start_time", "end_time", "timezone"})
    weekday: Weekday | None = None
    start_time: WallTime | None = None
    end_time: WallTime | None = None
    timezone: ZoneName | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class WindowRead(Contract):
    id: UUID
    user_id: UUID
    weekday: int
    start_time: time
    end_time: time
    timezone: str
    valid_from: date | None = None
    valid_until: date | None = None
    created_at: datetime
    updated_at: datetime