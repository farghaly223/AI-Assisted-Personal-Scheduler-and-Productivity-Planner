"""Task and Event API contracts."""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AfterValidator, Field, computed_field, model_validator

from app.core.schemas import Contract, Patch, WallTime, ZoneName, no_null_character, nonblank
from app.core.timezones import resolve_unambiguous_local_time


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timestamp with UTC offset is required")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("Timestamp exceeds supported UTC calendar dates") from None


Title = Annotated[str, Field(max_length=500), AfterValidator(nonblank)]


Category = Literal["STUDY", "WORK", "PERSONAL", "HEALTH", "MEETING"]


Priority = Annotated[int, Field(strict=True, ge=1, le=5)]


Duration = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


Instant = Annotated[datetime, AfterValidator(instant)]


Text = Annotated[str, Field(max_length=500), AfterValidator(no_null_character)]


DayOffset = Annotated[int, Field(strict=True, ge=0, le=32767)]


Weekday = Annotated[int, Field(strict=True, ge=1, le=7)]


class TaskCreate(Contract):
    title: Title
    task_type: Literal["FIXED", "FLEXIBLE"]
    estimated_duration_minutes: Duration
    category: Category
    priority: Priority
    deadline: Instant | None = None
    fixed_start_at: Instant | None = None
    fixed_end_at: Instant | None = None
    context: Text | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        start, end = self.fixed_start_at, self.fixed_end_at
        if self.task_type == "FLEXIBLE":
            if start is not None or end is not None:
                raise ValueError("Flexible tasks cannot have fixed endpoints")
        elif start is None or end is None or end <= start:
            raise ValueError("Fixed tasks require an end after their start")
        return self


class TaskUpdate(Patch):
    required_values = frozenset({"title", "task_type", "estimated_duration_minutes", "category", "priority"})
    title: Title | None = None
    task_type: Literal["FIXED", "FLEXIBLE"] | None = None
    estimated_duration_minutes: Duration | None = None
    category: Category | None = None
    priority: Priority | None = None
    deadline: Instant | None = None
    fixed_start_at: Instant | None = None
    fixed_end_at: Instant | None = None
    context: Text | None = None


class TaskRead(TaskCreate):
    task_id: UUID
    user_id: UUID
    status: Literal["UNSCHEDULED"]
    created_at: datetime
    updated_at: datetime


class EventFields(Contract):
    title: Title
    category: Category
    location: Text | None = None
    recurrence_type: Literal["ONCE", "WEEKLY"] = "ONCE"
    start_at: Instant | None = None
    end_at: Instant | None = None
    recurrence_first_date: date | None = None
    recurrence_until_date: date | None = None
    recurrence_start_time: WallTime | None = None
    recurrence_end_time: WallTime | None = None
    recurrence_end_day_offset: DayOffset | None = None
    recurrence_timezone: ZoneName | None = None


class EventCreate(EventFields):
    weekday: Weekday | None = Field(default=None, description="ISO weekday 1=Monday..7=Sunday; must match recurrence_first_date. Not stored.")

    @model_validator(mode="after")
    def valid_recurrence(self):
        weekly_fields = (self.recurrence_first_date, self.recurrence_until_date,
                         self.recurrence_start_time, self.recurrence_end_time,
                         self.recurrence_end_day_offset, self.recurrence_timezone, self.weekday)
        if self.recurrence_type == "ONCE":
            if any(value is not None for value in weekly_fields):
                raise ValueError("One-time events cannot contain weekly fields")
            if self.start_at is None or self.end_at is None or self.end_at <= self.start_at:
                raise ValueError("One-time events require an end after their start")
            return self
        if self.start_at is not None or self.end_at is not None:
            raise ValueError("Weekly rules cannot contain one-time endpoints")
        first, until = self.recurrence_first_date, self.recurrence_until_date
        if any(value is None for value in (first, self.recurrence_start_time, self.recurrence_end_time,
                                          self.recurrence_end_day_offset, self.recurrence_timezone)):
            raise ValueError("Weekly rules require first date, local endpoints, day offset and timezone")
        if until is not None and until < first:
            raise ValueError("Recurrence until date cannot precede first date")
        if self.weekday is not None and self.weekday != first.isoweekday():
            raise ValueError("Weekday must match the first occurrence date")
        if self.recurrence_end_day_offset == 0 and self.recurrence_end_time <= self.recurrence_start_time:
            raise ValueError("Same-day recurrence must end after its start")
        # Validate anchor and bounded final occurrence, without creating occurrences.
        dates = {first}
        if until is not None:
            dates.add(first + timedelta(days=((until - first).days // 7) * 7))
        try:
            for start_date in dates:
                start = resolve_unambiguous_local_time(datetime.combine(start_date, self.recurrence_start_time), self.recurrence_timezone)
                end_date = start_date + timedelta(days=self.recurrence_end_day_offset)
                end = resolve_unambiguous_local_time(datetime.combine(end_date, self.recurrence_end_time), self.recurrence_timezone)
                if end <= start:
                    raise ValueError("Resolved recurrence must end after its start")
        except OverflowError:
            raise ValueError("Recurrence endpoint exceeds supported calendar dates") from None
        return self


class EventUpdate(Patch):
    required_values = frozenset({"title", "category", "recurrence_type"})
    title: Title | None = None
    category: Category | None = None
    location: Text | None = None
    recurrence_type: Literal["ONCE", "WEEKLY"] | None = None
    start_at: Instant | None = None
    end_at: Instant | None = None
    recurrence_first_date: date | None = None
    recurrence_until_date: date | None = None
    recurrence_start_time: WallTime | None = None
    recurrence_end_time: WallTime | None = None
    recurrence_end_day_offset: DayOffset | None = None
    recurrence_timezone: ZoneName | None = None
    weekday: Weekday | None = None


class EventRead(EventFields):
    event_id: UUID
    user_id: UUID
    source: Literal["MANUAL"]
    status: Literal["SAVED"]
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def weekday(self) -> int | None:
        return self.recurrence_first_date.isoweekday() if self.recurrence_first_date else None


Item = TypeVar("Item")


class Page(Contract, Generic[Item]):
    items: list[Item]
    limit: int
    offset: int
    has_more: bool
