"""Schedule validation: overlaps, deadline violations and availability violations."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.scheduler.algorithms.time_intervals import Interval, merge_intervals


@dataclass(frozen=True)
class PlannedItem:
    kind: str  # "TASK", "EVENT" or "BREAK"
    source_id: UUID | None
    start: datetime
    end: datetime
    deadline: datetime | None = None  # only meaningful for TASK items
    is_fixed: bool = False  # FIXED tasks and events are never checked against availability


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # "ERROR" or "WARNING"
    code: str
    message: str
    item_index: int
    related_index: int | None = None


def detect_overlaps(items: list[PlannedItem]) -> list[ValidationIssue]:
    """Two items conflict when their time ranges share any time (touching is allowed)."""
    order = sorted(range(len(items)), key=lambda i: items[i].start)
    issues = []
    active: list[int] = []
    for index in order:
        current = items[index]
        active = [other for other in active if items[other].end > current.start]
        for other in active:
            issues.append(ValidationIssue("ERROR", "TIME_OVERLAP",
                                          "Two schedule items overlap in time", other, index))
        active.append(index)
    return issues


def detect_deadline_violations(items: list[PlannedItem]) -> list[ValidationIssue]:
    return [ValidationIssue("ERROR", "DEADLINE_EXCEEDED", "The task ends after its deadline", index)
            for index, item in enumerate(items)
            if item.kind == "TASK" and item.deadline is not None and item.end > item.deadline]


def detect_availability_violations(items: list[PlannedItem], availability: list[Interval]) -> list[ValidationIssue]:
    free = merge_intervals(availability)
    issues = []
    for index, item in enumerate(items):
        if item.kind != "TASK" or item.is_fixed:
            continue
        if not any(slot.start <= item.start and item.end <= slot.end for slot in free):
            issues.append(ValidationIssue("WARNING", "OUTSIDE_AVAILABILITY",
                                          "The task is not fully inside the user's availability", index))
    return issues


def validate_schedule(items: list[PlannedItem], availability: list[Interval] | None = None) -> list[ValidationIssue]:
    issues = detect_overlaps(items) + detect_deadline_violations(items)
    if availability is not None:
        issues += detect_availability_violations(items, availability)
    return issues


def is_valid(issues: list[ValidationIssue]) -> bool:
    """A schedule can be published only when it has no ERROR issues."""
    return not any(issue.severity == "ERROR" for issue in issues)