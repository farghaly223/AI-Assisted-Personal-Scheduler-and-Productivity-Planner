"""Energy module API contracts and validation schemas."""

from datetime import date as dt_date, datetime, time, timezone
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AfterValidator, Field, field_validator, model_validator

from app.core.schemas import Contract, WallTime, ZoneName
from app.modules.energy.algorithms.sleep.sleep_detector import calculate_duration_minutes

DayInt = Annotated[int, Field(strict=True, ge=0, le=6)]


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timestamp with UTC offset is required")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("Timestamp exceeds supported UTC calendar dates") from None


Instant = Annotated[datetime, AfterValidator(instant)]

Item = TypeVar("Item")


class Page(Contract, Generic[Item]):
    items: list[Item]
    limit: int
    offset: int
    has_more: bool


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


class SleepSessionCreate(Contract):
    start_time: Instant
    end_time: Instant
    timezone: ZoneName

    @model_validator(mode="after")
    def validate_session(self):
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time")
        duration = calculate_duration_minutes(self.start_time, self.end_time)
        if duration < 30 or duration > 1440:
            raise ValueError("Duration must be between 30 minutes and 24 hours")
        return self


class SleepSessionConfirm(Contract):
    status: Literal["confirmed", "rejected"]
    start_time: Instant | None = None
    end_time: Instant | None = None
    timezone: ZoneName | None = None

    @model_validator(mode="after")
    def validate_confirm(self):
        if self.status == "rejected":
            if self.start_time is not None or self.end_time is not None or self.timezone is not None:
                raise ValueError("Corrections are only allowed when confirming a session")
        return self


class SleepSessionRead(Contract):
    id: UUID
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    timezone: str
    sleep_date: dt_date
    source: str
    confidence: float
    status: str
    created_at: datetime
    updated_at: datetime
    quality_rating: int | None = None
    quality_score: float | None = None


RatingValue = Annotated[int, Field(strict=True, ge=1, le=5)]


class SleepQualityCreate(Contract):
    rating: RatingValue


class SleepQualityUpdate(Contract):
    rating: RatingValue


class SleepQualityRead(Contract):
    session_id: UUID
    rating: int
    quality_score: float
    created_at: datetime
    updated_at: datetime


class SleepQualityHistoryItem(Contract):
    session_id: UUID
    sleep_date: dt_date
    rating: int
    quality_score: float


EventType = Literal[
    "SCREEN_INTERACTIVE",
    "SCREEN_NON_INTERACTIVE",
    "KEYGUARD_SHOWN",
    "KEYGUARD_HIDDEN",
]


class ScreenEvent(Contract):
    event_type: EventType
    timestamp: Instant


class SignalSyncRequest(Contract):
    timezone: ZoneName
    screen_events: list[ScreenEvent] = Field(min_length=1, max_length=5000)
    date: dt_date | None = None


class SignalSyncResponse(Contract):
    received: int
    inserted: int
    duplicates: int


class SleepEstimateRequest(Contract):
    target_date: dt_date
    timezone: ZoneName


class SleepEstimateResponse(Contract):
    status: Literal["estimated", "existing", "insufficient_data"]
    created: bool
    session: SleepSessionRead | None = None



