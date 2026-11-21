"""Request/response contracts for schedule generation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.core.schemas import Contract
from app.modules.scheduler.schemas import Instant


class GenerateRequest(Contract):
    start: Instant
    end: Instant


class PreviewItemRead(Contract):
    kind: Literal["TASK", "EVENT"]
    source_id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    is_fixed: bool
    session_number: int | None = None


class UnscheduledRead(Contract):
    task_id: UUID
    reason_code: str
    explanation: str


class IssueRead(Contract):
    severity: Literal["INFO", "WARNING", "ERROR"]
    code: str
    message: str
    item_index: int
    related_index: int | None = None


class StatisticsRead(Contract):
    scheduled_tasks: int
    unscheduled_tasks: int
    total_scheduled_minutes: int
    available_minutes: int
    unused_minutes: int
    deadline_violations: int
    conflicts_count: int
    completion_ratio: float


class SchedulePreviewRead(Contract):
    range_start: datetime
    range_end: datetime
    generated_at: datetime
    is_publishable: bool
    items: list[PreviewItemRead]
    unscheduled: list[UnscheduledRead]
    issues: list[IssueRead]
    statistics: StatisticsRead


class GeneratedRead(SchedulePreviewRead):
    version_id: UUID
    version_number: int
    status: Literal["DRAFT", "PUBLISHED"]
    published_at: datetime | None = None