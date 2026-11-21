"""Response/request contracts for reading and manually changing the current schedule."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field

from app.core.schemas import Contract, nonblank
from app.modules.scheduler.schemas import Instant

Explanation = Annotated[str, Field(max_length=2000), AfterValidator(nonblank)]


class ItemMove(Contract):
    start_at: Instant
    explanation: Explanation


class ItemNote(Contract):
    explanation: Explanation


class ScheduleItemRead(Contract):
    id: UUID
    kind: Literal["TASK", "EVENT", "BREAK"]
    task_id: UUID | None = None
    event_id: UUID | None = None
    title: str | None = None
    start_at: datetime
    end_at: datetime
    is_locked: bool
    event_occurrence_date: date | None = None


class OmittedTaskRead(Contract):
    task_id: UUID | None = None
    title: str | None = None
    reason_code: str
    explanation: str | None = None


class VersionRead(Contract):
    version_id: UUID
    version_number: int
    origin: Literal["GENERATED", "MANUAL", "RESCHEDULED"]
    status: Literal["DRAFT", "PUBLISHED"]
    window_start_at: datetime
    window_end_at: datetime
    timezone: str
    published_at: datetime | None = None
    explanation: str | None = None


class CurrentScheduleRead(Contract):
    version: VersionRead
    items: list[ScheduleItemRead]
    unscheduled: list[OmittedTaskRead]


class DayScheduleRead(Contract):
    date: date
    timezone: str
    version_id: UUID | None = None
    items: list[ScheduleItemRead]


class WeekScheduleRead(Contract):
    week_start: date
    week_end: date
    timezone: str
    version_id: UUID | None = None
    days: list[DayScheduleRead]