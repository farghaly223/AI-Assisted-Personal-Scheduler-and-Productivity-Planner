"""Contracts for validation, conflict detection and statistics."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import Contract
from app.modules.scheduler.schemas import Instant


class ProposalItem(Contract):
    kind: Literal["TASK", "EVENT", "BREAK"]
    start_at: Instant
    end_at: Instant
    task_id: UUID | None = None
    label: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_item(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.kind == "TASK" and self.task_id is None:
            raise ValueError("TASK items need a task_id")
        return self


class ProposalRequest(Contract):
    items: list[ProposalItem] = Field(min_length=1, max_length=500)


class ProposalIssueRead(Contract):
    severity: Literal["INFO", "WARNING", "ERROR"]
    code: str
    message: str
    item_index: int | None = None
    task_id: UUID | None = None


class ProposalResultRead(Contract):
    is_valid: bool
    issues: list[ProposalIssueRead]


class ConflictIssueRead(Contract):
    severity: Literal["INFO", "WARNING", "ERROR"]
    code: str
    message: str
    item_id: UUID | None = None
    source_id: UUID | None = None
    title: str | None = None


class ConflictsRead(Contract):
    version_id: UUID
    version_number: int
    checked_at: datetime
    has_blocking: bool
    issues: list[ConflictIssueRead]


class ScheduleStatisticsRead(Contract):
    version_id: UUID
    version_number: int
    window_start_at: datetime
    window_end_at: datetime
    scheduled_tasks: int
    unscheduled_tasks: int
    total_scheduled_minutes: int
    committed_minutes: int
    available_minutes: int
    unused_minutes: int
    locked_items: int
    completion_ratio: float