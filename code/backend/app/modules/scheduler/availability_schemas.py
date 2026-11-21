"""Response contracts for the availability endpoint."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.core.schemas import Contract


class SlotRead(Contract):
    start_at: datetime
    end_at: datetime
    duration_minutes: int


class BusyBlockRead(Contract):
    kind: Literal["EVENT", "TASK"]
    source_id: UUID
    start_at: datetime
    end_at: datetime
    occurrence_date: date | None = None


class AvailabilityRead(Contract):
    range_start: datetime
    range_end: datetime
    slots: list[SlotRead]
    busy: list[BusyBlockRead]
    total_available_minutes: int