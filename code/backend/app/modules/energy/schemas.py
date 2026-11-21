"""Energy module API contracts and validation schemas."""

from datetime import datetime, time
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.schemas import Contract, WallTime, ZoneName

DayInt = Annotated[int, Field(strict=True, ge=0, le=6)]


class SleepScheduleCreate(Contract):
    bedtime: WallTime
    wake_time: WallTime
    timezone: ZoneName
    repeat_days: list[DayInt] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    bedtime_reminder_enabled: bool = Field(default=True, strict=True)
    reminder_minutes_before: int = Field(default=15, strict=True, ge=0, le=180)
    wake_alarm_enabled: bool = Field(default=True, strict=True)

    @field_validator("repeat_days")
    @classmethod
    def validate_repeat_days(cls, v: list[DayInt]) -> list[int]:
        if not v:
            raise ValueError("repeat_days must not be empty")
        return sorted(set(v))

    @model_validator(mode="after")
    def validate_times_differ(self):
        if self.bedtime == self.wake_time:
            raise ValueError("Bedtime and wake time must differ")
        return self


class SleepScheduleToggle(Contract):
    is_active: bool = Field(strict=True)


class SleepScheduleRead(Contract):
    id: UUID
    bedtime: time
    wake_time: time
    timezone: str
    repeat_days: list[int]
    bedtime_reminder_enabled: bool
    reminder_minutes_before: int
    wake_alarm_enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
